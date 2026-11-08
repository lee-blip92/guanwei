"""Compatibility bridge for the inspected local MediaCrawler. Original application code.

Loads MediaCrawler under its NON-COMMERCIAL LEARNING LICENSE 1.1; see md/LICENSE.
Does not modify upstream files. JSON stdout events are the only interface to the app.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
settings = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
cfg = settings['config']
folder = Path(settings['output'])
sys.path.insert(0, settings['crawler_path'])
os.chdir(settings['crawler_path'])

def emit(kind, **data):
    print('INSIGHT_EVENT ' + json.dumps({'kind': kind, **data}, ensure_ascii=False), flush=True)

async def main():
    try:
        import config as mc
        mc.PLATFORM = 'xhs'
        mc.CRAWLER_TYPE = 'search'
        mc.KEYWORDS = ','.join(cfg['keywords'])
        mc.LOGIN_TYPE = 'qrcode'
        mc.ENABLE_IP_PROXY = False
        mc.ENABLE_GET_MEDIA = False
        mc.ENABLE_GET_SUB_COMMENTS = cfg['include_replies']
        mc.HEADLESS = mc.CDP_HEADLESS = False
        mc.ENABLE_CDP_MODE = True
        mc.CDP_CONNECT_EXISTING = False
        mc.MAX_CONCURRENCY_NUM = 1
        mc.SAVE_DATA_PATH = str(folder / 'upstream')
        # Reuse MediaCrawler's dedicated profile; single crawler lock prevents concurrent access.
        from media_platform.xhs import core
        from media_platform.xhs.client import XiaoHongShuClient
        from media_platform.xhs.field import SearchSortType
        from media_platform.xhs.exception import NoteNotFoundError, PlatformAccessError, IPBlockError
        from tools import utils
        utils.logger.disabled = True  # upstream logs can include raw cookies/headers; never forward them

        total = settings.get('existing_comment_count', 0)
        seen_notes = set(settings.get('existing_note_ids', []))
        seen_comments = set(settings.get('existing_comment_ids', []))
        crawler = None
        finished = False

        async def pause(message):
            emit('waiting_login', message=message)
            marker = folder / 'resume'
            while not marker.exists():
                await asyncio.sleep(0.5)
            marker.unlink(missing_ok=True)
            if crawler and getattr(crawler, 'xhs_client', None):
                await crawler.xhs_client.update_cookies(crawler.browser_context, urls=crawler.cookie_urls)
            emit('collecting', message='已恢复，正在检查登录状态并继续采集')

        # Do not run upstream automated login helpers or captcha logic. User uses visible browser.
        async def manual_login(login):
            while True:
                await pause('请在打开的采集浏览器中手动登录，然后点击恢复采集')
                await crawler.xhs_client.update_cookies(crawler.browser_context, urls=crawler.cookie_urls)
                if await crawler.xhs_client.pong():
                    break

        core.XiaoHongShuLogin.begin = manual_login
        original_request = XiaoHongShuClient.request
        request_once = getattr(original_request, '__wrapped__', original_request)

        async def guarded_request(client, *args, **kwargs):
            while True:
                try:
                    return await request_once(client, *args, **kwargs)
                except Exception as exc:
                    message = str(exc).lower()
                    if isinstance(exc, (PlatformAccessError, IPBlockError)) or any(x in message for x in ('captcha', '471', '461', '登录', 'login', '未登录', '300011')):
                        await pause('平台要求登录或人工验证，采集已暂停')
                    else:
                        raise

        XiaoHongShuClient.request = guarded_request

        async def save_comment(raw, nid, keyword, parent_id=None, parent_text=None):
            nonlocal total
            cid = str(raw.get('id') or '')
            if cid and cid in seen_comments:
                return
            if total >= cfg['max_comments']:
                return
            if cid:
                seen_comments.add(cid)
            parent = raw.get('target_comment') or {}
            record = {'platform': 'xhs', 'comment_id': cid or None, 'note_id': nid,
                      'text': raw.get('content') or '', 'parent_comment_id': parent.get('id') or parent_id,
                      'parent_text': parent.get('content') or parent_text, 'create_time': raw.get('create_time'),
                      'like_count': raw.get('like_count'), 'source_keyword': keyword, '_upstream_record':raw}
            emit('comments', data=[record])
            total += 1

        async def get_comments(nid, token, keyword):
            first_count = 0
            cursor = ''
            while first_count < cfg['max_comments_per_note'] and total < cfg['max_comments']:
                data = await crawler.xhs_client.get_note_comments(nid, token, cursor)
                rows = (data.get('comments') or [])[:cfg['max_comments_per_note']-first_count]
                for row in rows:
                    await save_comment(row, nid, keyword)
                    first_count += 1
                    if cfg['include_replies'] and total < cfg['max_comments']:
                        for sub in row.get('sub_comments') or []:
                            await save_comment(sub, nid, keyword, row.get('id'), row.get('content'))
                        sub_cursor = row.get('sub_comment_cursor') or ''
                        more = row.get('sub_comment_has_more')
                        while more and total < cfg['max_comments']:
                            await asyncio.sleep(mc.CRAWLER_MAX_SLEEP_SEC)
                            sub = await crawler.xhs_client.get_note_sub_comments(nid, row['id'], token, num=10, cursor=sub_cursor)
                            for reply in sub.get('comments') or []:
                                await save_comment(reply, nid, keyword, row.get('id'), row.get('content'))
                            new_cursor = sub.get('cursor') or ''
                            more = sub.get('has_more') and new_cursor != sub_cursor
                            sub_cursor = new_cursor
                new_cursor = data.get('cursor') or ''
                if not data.get('has_more') or not rows or new_cursor == cursor:
                    break
                cursor = new_cursor
                await asyncio.sleep(mc.CRAWLER_MAX_SLEEP_SEC)

        class BoundedCrawler(core.XiaoHongShuCrawler):
            async def search(self):
                nonlocal finished
                # Round robin over keywords improves coverage within a small total note limit.
                exhausted = set()
                quota = max(1, (cfg['max_notes'] + len(cfg['keywords']) - 1) // len(cfg['keywords']))
                for page in range(1, cfg['max_notes']+2):
                    if len(exhausted) == len(cfg['keywords']):
                        break
                    for keyword in cfg['keywords']:
                        if len(seen_notes) >= cfg['max_notes'] or total >= cfg['max_comments']:
                            finished = True
                            return
                        if keyword in exhausted:
                            continue
                        emit('collecting', message=f'正在搜索：{keyword} · 第 {page} 页')
                        response = await self.xhs_client.get_note_by_keyword(keyword, core.get_search_id(), page=page, sort=SearchSortType(cfg['sort']))
                        if not response.get('has_more'):
                            exhausted.add(keyword)
                        processed_this_keyword = 0
                        for post in response.get('items') or []:
                            if len(seen_notes) >= cfg['max_notes'] or total >= cfg['max_comments']:
                                break
                            nid = post.get('id')
                            if not nid or nid in seen_notes or post.get('model_type') in ('rec_query','hot_query'):
                                continue
                            # Divide the note budget across keywords without discarding entire pages.
                            seen_notes.add(nid)
                            token = post.get('xsec_token') or ''
                            try:
                                note = await self.xhs_client.get_note_by_id(nid, post.get('xsec_source') or 'pc_search', token)
                                if not note:
                                    raise ValueError('empty note')
                                emit('note', data={'note_id': nid, 'title': note.get('title'), 'desc': note.get('desc'),
                                                  'note_url': f'https://www.xiaohongshu.com/explore/{nid}?xsec_token={token}&xsec_source=pc_search', 'source_keyword': keyword, '_upstream_record':note})
                                await get_comments(nid, token, keyword)
                            except NoteNotFoundError:
                                emit('failure', message='一条笔记已删除或不可访问')
                            except Exception:
                                emit('failure', message='一条笔记或评论请求失败；其余数据继续采集')
                            await asyncio.sleep(mc.CRAWLER_MAX_SLEEP_SEC)
                            processed_this_keyword += 1
                            if processed_this_keyword >= quota:
                                break
                finished = True

        crawler = BoundedCrawler()
        work = asyncio.create_task(crawler.start())
        async def watch():
            while not work.done():
                if (folder / 'cancel').exists():
                    work.cancel()
                    return
                await asyncio.sleep(0.5)
        watcher = asyncio.create_task(watch())
        try:
            await work
            if finished:
                emit('done')
        finally:
            watcher.cancel()
            if getattr(crawler, 'cdp_manager', None):
                await crawler.cdp_manager.cleanup(force=True)
            elif getattr(crawler, 'browser_context', None):
                await crawler.browser_context.close()
    except asyncio.CancelledError:
        pass
    except ImportError:
        emit('fatal', message='MediaCrawler 依赖不完整，请按 md/README.md 安装其依赖。')
    except Exception:
        emit('fatal', message='采集启动或平台请求失败，请检查网络、Chrome、平台状态与 MediaCrawler 版本。')

if __name__ == '__main__':
    asyncio.run(main())
