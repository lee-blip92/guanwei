import asyncio
import json
from . import config, db
from .analysis import ModelFailure, extract_all, make_report, provider_options
from .crawler import CrawlFailure, collect

class Manager:
    def __init__(self):
        self.running = {}
        self.processes = {}
        self.crawler_lock = asyncio.Lock()
        self.analysis_lock = asyncio.Lock()

    def start(self, task_id, crawl=False, report_only=False, cached_report=None, repair_ids=None):
        if task_id in self.running:
            raise ValueError('任务已在后台执行')
        db.snapshot_report(task_id)
        previous=db.task(task_id).get('report') or db.latest_report_snapshot(task_id)
        processed = db.task(task_id)['analyzed_count'] if report_only or repair_ids is not None else 0
        issue=db.task(task_id).get('error') if cached_report else None
        db.update(task_id, status='queued', stage='等待离线重算统计' if cached_report else '等待重新生成报告' if report_only else '等待采集资源' if crawl else '等待开始分析', error=issue, report=None, analyzed_count=processed)
        job = asyncio.create_task(self.run(task_id, crawl, report_only, cached_report, issue,repair_ids,previous))
        self.running[task_id] = job
        job.add_done_callback(lambda _: self.running.pop(task_id, None))

    async def run(self, task_id, crawl, report_only=False, cached_report=None, provider_issue=None,repair_ids=None,previous_report=None):
        t = db.task(task_id)
        cfg = t['config']
        failures = t['failure_count']
        async def checkpoint():
            await asyncio.sleep(0)
            if db.task(task_id)['status'] == 'cancelled':
                raise asyncio.CancelledError()
        try:
            if crawl:
                async with self.crawler_lock:
                    await checkpoint()
                    db.update(task_id, status='collecting', stage='正在启动采集浏览器', failure_count=0)
                    failures = await collect(task_id, cfg, self)
            await checkpoint()
            db.update(task_id, status='cleaning', stage='按平台与评论 ID 去重，补全父评论与笔记上下文')
            if not db.comments(task_id):
                raise ValueError('没有可分析的评论；请导入有效数据或检查采集范围后重试。')
            async with self.analysis_lock:
                await checkpoint()
                db.update(task_id, status='analyzing', stage='分批进行产品归属、观点拆分与痛点提取' if cfg.get('analysis_version')==2 else '分批进行相关性判断与反馈提取')
                db.event(task_id, 'analysis', '离线复算已保存标签，不调用模型；保守过滤旧文案依赖。' if cached_report else '复用本任务已保存的数据开始分析；人工修正优先于模型标签。')
                if not cached_report:
                    db.update(task_id, model_info={'mode':cfg['analysis_mode'], **({'model':db.settings()['model'], 'base_url':db.settings()['base_url'], 'provider_options':provider_options(db.settings())} if cfg['analysis_mode']=='ai' else {})})
                def progress(n, total):
                    if repair_ids is not None:
                        db.update(task_id,stage=f'仅修复校验失败项 · 已处理 {n} / {total} 条；其他评论保持原分析')
                        return
                    db.update(task_id, analyzed_count=n, stage=f'已处理 {n} / {total} 条评论 · ' + ('AI 逐维度观点提取' if cfg.get('analysis_version')==2 and cfg['analysis_mode']=='ai' else 'AI 结构化提取' if cfg['analysis_mode']=='ai' else '本地规则预览'))
                if not report_only:
                    if repair_ids is not None:
                        from .aspect_analysis import extract_all as repair_extract
                        # Research-only failures must not re-run their valid original opinion extraction.
                        extraction_ids={x['evidence_id'] for x in db.comments(task_id) if x['evidence_id'] in repair_ids and
                            (x.get('analysis') or {}).get('opinion_audit',{}).get('state') in ('pending','needs_review')}
                        await repair_extract(task_id,cfg,checkpoint,progress,target_ids=extraction_ids)
                    else:await extract_all(task_id, cfg, checkpoint, progress)
                await checkpoint()
                db.update(task_id, status='reporting', stage='离线重算统计并过滤旧文案，不调用模型' if cached_report else '归并主题，生成报告并校验证据引用')
                if cached_report:
                    if cached_report.get('decision_version')==3:
                        from .decision_analysis import make_report as recount
                        report=await recount(task_id,cfg,checkpoint,statistics_only=True,previous_report=cached_report)
                    else:
                        from .aspect_analysis import make_report as recount
                        report=await recount(task_id,cfg,checkpoint,previous_report=cached_report,provider_issue=provider_issue)
                elif cfg.get('insight_version')==3 and cfg.get('analysis_version')==2:
                    from .decision_analysis import make_report as decision_report
                    report=await decision_report(task_id,cfg,checkpoint,previous_report=previous_report,repair_ids=repair_ids)
                else:report = await make_report(task_id, cfg, checkpoint)
                await checkpoint()
                current = db.task(task_id)
                warnings=bool(report.get('generation_warnings'))
                reasons=[]
                if report.get('decision_version')==3:
                    review_count=report.get('quality',{}).get('audit_needs_review_count',0)
                    unmapped=len(report['decision'].get('unmapped_unit_ids',[]));claims=len(report['decision'].get('generated_review',[]))
                    if review_count:reasons.append(f'{review_count} 条评论校验待复核')
                    if unmapped:reasons.append(f'{unmapped} 条观点未归类')
                    if claims:reasons.append(f'{claims} 条生成表述待复核')
                warning_stage='报告已生成；'+'，'.join(reasons) if reasons else '部分内容未通过校验，已标记或省略；统计与证据报告已生成'
                db.update(task_id, report=report, version=current['version']+1,
                          status='partial' if failures or warnings else 'completed', failure_count=failures,
                          stage='已离线重算统计并保守复用旧文案；未新调用模型' if cached_report else warning_stage if warnings else '部分采集失败，已基于可用数据生成报告' if failures else '分析完成' if report['stats']['included'] else '分析完成，但无有效反馈；可在评论工作台人工核查')
                db.snapshot_report(task_id)
                db.event(task_id, 'complete', f'报告版本 {current["version"]+1} 已通过证据 ID 校验，纳入 {report["stats"]["included"]} 条。')
                if repair_ids is not None:
                    remaining=[x['evidence_id'] for x in db.comments(task_id) if x['evidence_id'] in repair_ids and
                        ((x.get('analysis') or {}).get('opinion_audit',{}).get('state') in ('pending','needs_review') or x['effective'].get('research_status')=='needs_review')]
                    db.event(task_id,'repair_complete',f'本轮修复 {len(repair_ids)} 条校验失败评论，恢复 {len(repair_ids)-len(remaining)} 条，剩余 {len(remaining)} 条待复核；未重新采集。')
        except asyncio.CancelledError:
            db.update(task_id, status='cancelled', stage='任务已取消，原始数据与人工修正已保留')
            db.event(task_id, 'cancelled', '后台任务已停止。')
        except (ModelFailure, CrawlFailure, ValueError) as exc:
            db.update(task_id, status='failed', stage='执行失败；数据已保留，可修正后重新分析或恢复采集', error=str(exc))
            db.event(task_id, 'failure', str(exc))
        except Exception as exc:
            # Never persist exception strings from providers: they may contain request headers.
            db.update(task_id, status='failed', stage='执行失败；原始数据已保留', error=f'内部执行失败（{type(exc).__name__}），请检查本地依赖。')
            db.event(task_id, 'failure', f'内部异常类型：{type(exc).__name__}')

    async def cancel(self, task_id):
        db.update(task_id, status='cancelled', stage='正在停止后台任务')
        job = self.running.get(task_id)
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        else:
            db.update(task_id, stage='任务已取消，已保存数据保留')

    async def close(self):
        jobs = list(self.running.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

manager = Manager()
