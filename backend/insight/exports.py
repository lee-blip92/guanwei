import csv
import html
import io
from . import db
from .platforms import platform_names, platform_name

def distribution(report):
    counts=report['stats'].get('platforms') or {'xhs':report['stats']}
    return [(platform_name(p),counts.get(p,{})) for p in report['config'].get('platforms',['xhs'])]


def markdown(report,view='brief'):
    if report.get('decision_version')==3:
        from .decision_exports import markdown as decision_markdown
        return decision_markdown(report,view)
    r = report
    s = r['stats']
    lines = [f'# {r["product"]} · 用户反馈洞察', '', f'生成时间：{r["generated_at"]}',
             f'数据来源：{"演示模式（人工虚构样本，非真实小红书数据）" if r["source"] == "demo" else r["source_name"] or platform_names(r["config"].get("platforms",["xhs"]))+"采集"}',
             f'分析方式：{r["model"]}', '', '## 分析范围与统计', '',
             '平台：' + platform_names(r['config'].get('platforms',['xhs'])) + '；产品：' + r['product'], '关键词：' + '、'.join(r['config']['keywords']),
             '关注问题：' + (r['config']['focus'] or '未指定'),
             '每个平台采集上限（导入不适用）：内容 ' + str(r['config']['max_notes']) + '；单条内容一级评论 ' + str(r['config']['max_comments_per_note']) + '；含回复总评论 ' + str(r['config']['max_comments']),
             '时间范围：未限制（适配工具未实现日期筛选）；排序：' + r['config']['sort'],
             f'原始评论 {s["raw"]}；去重后 {s["unique"]}；重复 {s["duplicates"]}；排除 {s["excluded"]}；待确认 {s["uncertain"]}；待处理 {s["pending"]}；最终纳入 {s["included"]}。', '']
    lines += ['### 平台样本分布', '', '| 平台 | 原始评论 | 去重后 | 最终纳入 | 待确认 |', '| --- | ---: | ---: | ---: | ---: |']
    for name, counts in distribution(r):
        lines.append('| '+name+' | '+' | '.join(str(counts.get(k,0)) for k in ('raw','unique','included','uncertain'))+' |')
    lines += ['', '各平台采样与排序口径不同，不宜直接比较数量；未跨平台合并用户身份。', '']
    from .aspect_exports import markdown_sections
    lines += markdown_sections(r)
    for key, title in [('findings','核心发现 · 系统归纳'),('positives','正面体验 · 评论表达'),('recommendations','改进建议 · 待验证'),('questions','待验证的问题')]:
        lines += ['## ' + title, '']
        for claim in r[key]:
            lines += ['- ' + claim['text'], '  证据：' + '、'.join(claim['evidence_ids'])]
        if not r[key]:
            lines += ['暂无可支持的结论。']
        lines += ['']
    lines += ['## 主要反馈主题', '']
    for t in r['themes']:
        lines += [f'### {t["name"]} · {t["count"]} 条（{t["share"]}%）', '',
                  '用户场景：' + '；'.join(t['scenarios']), '主要反馈：' + '；'.join(t['feedback']),
                  '可能影响：' + t['impact'], '建议：' + t['recommendation'], t['hypothesis'],
                  '证据 ID：' + '、'.join(t['evidence_ids']), '']
    lines += ['## 原文证据', '']
    for eid, e in r['evidence'].items():
        lines += ['### ' + eid + ' · ' + platform_name(e.get('platform') or eid.split(':')[0]), '', '> ' + (e['text'] or '').replace('\n', '\n> '),
                  '来源内容：' + (e.get('note_title') or '未知'), '父评论（仅上下文）：' + (e.get('parent_text') or '无/未获取'),
                  '原文链接：' + (e.get('source_url') or '未获取'), '']
    lines += ['## 样本限制', ''] + ['- ' + x for x in r['limitations']]
    return '\n'.join(lines) + '\n'

def safe_cell(value):
    text = str(value) if value is not None else ''
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text

def comments_csv(task_id):
    from .aspect_exports import csv_fields
    report=db.task(task_id)['report']
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    fields = ['evidence_id','platform','comment_id','note_id','note_title','note_body','text','parent_comment_id','parent_text','published_at','like_count','source_url','source_keywords','collected_at','relevance','sentiment','themes','suspected_ad','manual_excluded','included','reason','review_history','analysis_version','aspects','keywords','pain_points','expressed_impacts','explicit_requests','opinion_points_json','report_topics',
        'user_id','parent_relation_known','thread_id','decision_domains','decision_categories','intents','evaluation_objects','stances','epistemic_bases','severity','original_opinion_points_json','research_checks']
    from .decision_state import thread_keys,original_points
    all_items=db.comments(task_id);threads=thread_keys(all_items)
    writer.writerow(fields)
    for x in all_items:
        e = x['effective']
        values = {**x, **e, **csv_fields(x,report), 'source_keywords': ' | '.join(x['source_keywords']), 'themes': ' | '.join(e.get('themes', [])),
                  'manual_excluded': bool(e.get('excluded')), 'included': db.included(x), 'review_history': db.dumps(x['audit'])}
        if report.get('decision_version')==3:
            units=[u for u in report['opinion_points'] if u['evidence_id']==x['evidence_id']]
            domains={d['id']:d['name'] for d in report['decision']['taxonomy']};cats={c['id']:c['name'] for d in report['decision']['taxonomy'] for c in d['children']}
            join=lambda vs:' | '.join(dict.fromkeys(v for v in vs if v))
            values.update(thread_id=threads[x['evidence_id']],decision_domains=join(domains.get(u.get('domain_id')) for u in units),
                decision_categories=join(cats.get(u.get('category_id')) for u in units),
                intents=join(i for p in e.get('points',[]) for i in p.get('intents',[])),
                evaluation_objects=join(p.get('evaluation_object') for p in e.get('points',[])),stances=join(p.get('stance') for p in e.get('points',[])),
                epistemic_bases=join(p.get('epistemic') for p in e.get('points',[])),severity=join(p.get('severity') for p in e.get('points',[])),
                original_opinion_points_json=db.dumps((x.get('analysis') or {}).get('points',[])),research_checks=db.dumps((x.get('research') or {}).get('checks',[])))
        writer.writerow([safe_cell(values.get(k)) for k in fields])
    return '\ufeff' + out.getvalue()

def print_html(report,view='brief',version=None):
    if report.get('decision_version')==3:
        from .decision_exports import print_html as decision_html
        return decision_html(report,view,version)
    # Render semantic HTML while escaping every untrusted field; no Markdown execution.
    from urllib.parse import urlparse
    r = report
    s = r['stats']
    esc = lambda value: html.escape(str(value) if value is not None else '', quote=True)
    blocks = []
    for key, title in [('findings','核心发现 · 系统归纳'),('positives','正面体验 · 评论表达'),('recommendations','改进建议 · 待验证'),('questions','待验证的问题')]:
        claims = []
        for claim in r[key]:
            refs = '、'.join(f'<a href="#ev-{esc(eid)}">{esc(eid)}</a>' for eid in claim['evidence_ids'])
            claims.append(f'<li>{esc(claim["text"])}<small>证据：{refs}</small></li>')
        blocks.append(f'<section><h2>{title}</h2>' + ('<ol>'+''.join(claims)+'</ol>' if claims else '<p class="muted">暂无可支持的结论。</p>') + '</section>')
    themes = []
    for t in r['themes']:
        refs = '、'.join(f'<a href="#ev-{esc(eid)}">{esc(eid)}</a>' for eid in t['evidence_ids'])
        themes.append(f'<article><h3>{esc(t["name"])} <span>{t["count"]} 条 / {t["share"]}%</span></h3><p>场景：{esc("；".join(t["scenarios"]))}</p><p>主要反馈：{esc("；".join(t["feedback"]))}</p><p>可能影响：{esc(t["impact"])}</p><p>建议：{esc(t["recommendation"])}</p><p>{esc(t["hypothesis"])}</p><small>证据：{refs}</small></article>')
    evidence = []
    for eid, e in r['evidence'].items():
        url = e.get('source_url') or ''
        link = f'<a href="{esc(url)}">打开原文</a>' if urlparse(url).scheme in ('https','http') else '原文链接未获取'
        evidence.append(f'<article class="evidence" id="ev-{esc(eid)}"><h3>{esc(platform_name(e.get("platform") or eid.split(":")[0]))} · {esc(eid)}</h3><blockquote>{esc(e["text"])}</blockquote><p>来源内容：{esc(e.get("note_title") or "未获取")}</p><p>父评论（仅上下文）：{esc(e.get("parent_text") or "无 / 未获取")}</p><small>{link}</small></article>')
    source = '演示模式 · 人工虚构样本，非真实小红书数据' if r['source']=='demo' else r['source_name'] or platform_names(r['config'].get('platforms',['xhs']))+' · MediaCrawler'
    body = f'<header><p class="eyebrow">观微 / PRODUCT FEEDBACK RESEARCH</p><h1>{esc(r["product"])}<br><span>用户反馈洞察报告</span></h1><p>生成时间：{esc(r["generated_at"])}</p><p>来源：{esc(source)}</p><p>分析方式：{esc(r["model"])}</p></header>'
    body += f'<section><h2>分析范围与样本概况</h2><p>平台：{esc(platform_names(r["config"].get("platforms",["xhs"])))}；关键词：{esc("、".join(r["config"]["keywords"]))}</p><p>关注问题：{esc(r["config"]["focus"] or "综合用户体验")}</p><p>时间范围未限制；采集排序：{esc(r["config"]["sort"])}。每个平台采集上限：{r["config"]["max_notes"]} 条内容、{r["config"]["max_comments"]} 条评论（导入不适用）。</p><div class="stats">原始评论 {s["raw"]}；去重后 {s["unique"]}；重复 {s["duplicates"]}；排除 {s["excluded"]}；待确认 {s["uncertain"]}；待处理 {s["pending"]}；最终纳入 {s["included"]}。</div></section>'
    rows=''.join('<tr><td>'+esc(name)+'</td>'+''.join('<td>'+str(counts.get(k,0))+'</td>' for k in ('raw','unique','included','uncertain'))+'</tr>' for name,counts in distribution(r))
    body += '<section><h2>平台样本分布</h2><table><thead><tr><th>平台</th><th>原始评论</th><th>去重后</th><th>最终纳入</th><th>待确认</th></tr></thead><tbody>'+rows+'</tbody></table><small>各平台采样与排序口径不同，数量不代表口碑差异；未跨平台合并用户身份。</small></section>'
    from .aspect_exports import html_sections
    body += html_sections(r)
    body += ''.join(blocks[:1]) + '<section><h2>主要反馈主题</h2>'+(''.join(themes) or '<p>未形成可展示主题。</p>')+'</section>'+''.join(blocks[1:])
    body += '<section><h2>样本限制</h2><ul>'+''.join('<li>'+esc(x)+'</li>' for x in r['limitations'])+'</ul></section>'
    body += '<section><h2>原文证据附录</h2>'+''.join(evidence)+'</section><footer>观微 · 让每个结论，都有据可查。</footer>'
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(r['product'])} 用户反馈报告</title>
<style>body{{font:14px/1.9 "Microsoft YaHei",sans-serif;color:#304c40;max-width:960px;margin:38px auto;padding:0 30px}}header{{padding:25px 0 30px;border-bottom:2px solid #85ac98}}h1{{font-size:32px;line-height:1.6}}h1 span{{font-size:26px;font-weight:400}}h2{{font-size:20px;margin:30px 0 18px;border-left:3px solid #529b79;padding-left:12px}}h3{{font-size:15px;margin:8px 0}}h3 span{{font-size:12px;font-weight:400;color:#8a9e90;margin-left:15px}}p{{margin:8px 0;overflow-wrap:anywhere}}small{{display:block;font-size:11px;color:#8c9b90;overflow-wrap:anywhere;margin-top:8px}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{border-bottom:1px solid #e1e9e2;text-align:right;padding:7px 12px}}th:first-child,td:first-child{{text-align:left}}a{{color:#448f73}}article{{padding:15px 18px;border:1px solid #e1e9e2;margin:12px 0;border-radius:6px}}blockquote{{white-space:pre-wrap;font-size:14px;background:#f5f8f3;border-left:2px solid #a7c0ab;margin:12px 0;padding:12px 15px;overflow-wrap:anywhere}}li{{padding:5px 0}}.stats{{background:#f0f6ef;border:1px solid #dae7d9;padding:16px;border-radius:6px;margin:18px 0}}.eyebrow{{font-size:10px;letter-spacing:2px;color:#96ab9b}}.muted{{color:#91a294}}footer{{margin:35px 0;padding:18px 0;border-top:1px solid #e4eae3;font-size:11px;color:#92a698}}button{{padding:10px 22px;background:#13796a;color:#fff;border:0;border-radius:6px;cursor:pointer}}@media print{{button{{display:none}}body{{margin:0;max-width:none;padding:0;font-size:10pt}}h1{{font-size:26pt}}h2{{font-size:16pt;break-after:avoid}}h3{{break-after:avoid}}article,.stats,li{{break-inside:avoid}}a{{text-decoration:none}}.evidence{{font-size:9pt}}blockquote{{font-size:10pt}}}}@page{{size:A4;margin:17mm}}</style><button onclick="window.print()">打印 / 另存为 PDF</button>{body}</html>'''
