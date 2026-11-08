"""Bounded per-platform protocol loops for the inspected MediaCrawler clients.

No upstream imports at module load; usable with protocol fixtures in tests.
"""
import asyncio
import math
from .platforms import as_dict, first, normalize_note, normalize_comment

class PlatformAdapter:
    def __init__(self, platform, client, fields, cfg, emit, existing=None, pause=None, on_keyword=None):
        existing=existing or {}
        self.platform,self.client,self.fields,self.cfg,self.emit=platform,client,fields,cfg,emit
        self.notes=set(existing.get('existing_note_ids',[]))
        self.comments=set(existing.get('existing_comment_ids',[]))
        self.total=existing.get('existing_comment_count',0)
        self.pause=pause
        self.on_keyword=on_keyword
        self.search_state={}
        self.delay=cfg.get('_crawl_interval',2)

    async def sleep(self):
        await asyncio.sleep(self.delay)

    async def page_safety(self):
        page=getattr(self.client,'playwright_page',None)
        if page and hasattr(page,'title'):
            title=await page.title()
            if any(word in title for word in ('安全验证','验证码','Security Verification')) and self.pause:
                await self.pause('平台要求人工验证')
                return False
        return True

    async def search_page(self, keyword, page):
        c,p,f=self.client,self.platform,self.fields
        mode=self.cfg['sort']
        state=self.search_state.setdefault(keyword,{})
        if p=='dy':
            order={'general':f.SearchSortType.GENERAL,'popularity_descending':f.SearchSortType.MOST_LIKE,'time_descending':f.SearchSortType.LATEST}[mode]
            r=await c.search_info_by_keyword(keyword=keyword,offset=(page-1)*15,sort_type=order,search_id=state.get('id',''))
            if 'data' not in r:raise ValueError('抖音搜索响应字段不匹配')
            state['id']=(r.get('extra') or {}).get('logid','')
            rows=[]
            for item in r.get('data') or []:
                raw=item.get('aweme_info')
                if not raw:
                    mix=(item.get('aweme_mix_info') or {}).get('mix_items') or []
                    raw=mix[0] if mix else None
                if raw:rows.append(raw)
            return rows,bool(r.get('has_more',bool(rows)))
        if p=='ks':
            r=await c.search_info_by_keyword_v2(keyword=keyword,pcursor=str(page),search_session_id=state.get('id',''))
            if r.get('result')!=1:raise ValueError('快手搜索没有返回成功结果')
            if 'feeds' not in r:raise ValueError('快手搜索响应字段不匹配')
            state['id']=r.get('searchSessionId','')
            return r.get('feeds') or [],r.get('pcursor')!='no_more'
        if p=='bili':
            order={'general':f.SearchOrderType.DEFAULT,'popularity_descending':f.SearchOrderType.MOST_CLICK,'time_descending':f.SearchOrderType.LAST_PUBLISH}[mode]
            r=await c.search_video_by_keyword(keyword=keyword,page=page,page_size=20,order=order,pubtime_begin_s=0,pubtime_end_s=0)
            if 'result' not in r:raise ValueError('B站搜索响应字段不匹配')
            return r.get('result') or [],bool(r.get('result'))
        if p=='wb':
            order={'general':f.SearchType.DEFAULT,'popularity_descending':f.SearchType.POPULAR,'time_descending':f.SearchType.REAL_TIME}[mode]
            r=await c.get_note_by_keyword(keyword=keyword,page=page,search_type=order)
            if 'cards' not in r:raise ValueError('微博搜索响应字段不匹配')
            rows=[]
            def walk(cards):
                for item in cards or []:
                    if item.get('mblog'):rows.append(item['mblog'])
                    if item.get('card_group'):walk(item['card_group'])
            walk(r.get('cards'))
            return rows,bool(rows)
        if p=='tieba':
            order=f.SearchSortType.TIME_DESC if mode=='time_descending' else f.SearchSortType.RELEVANCE_ORDER
            rows=await c.get_notes_by_keyword(keyword=keyword,page=page,page_size=20,sort=order,note_type=f.SearchNoteType.MAIN_THREAD)
            return rows,bool(rows)
        rows=await c.get_note_by_keyword(keyword=keyword,page=page)
        return rows,bool(rows)

    async def detail(self, raw):
        r=as_dict(raw)
        if self.platform=='bili':return await self.client.get_video_info(aid=int(r['aid']))
        if self.platform=='tieba':return await self.client.get_note_by_id(str(r['note_id']))
        return raw

    def source_url(self, raw, note):
        r=as_dict(raw);nid=note['note_id']
        p=self.platform
        if p=='dy':return f'https://www.douyin.com/video/{nid}'
        if p=='ks':return f'https://www.kuaishou.com/short-video/{nid}'
        if p=='bili':return f'https://www.bilibili.com/video/av{nid}'
        if p=='wb':return f'https://m.weibo.cn/detail/{nid}'
        return note.get('source_url')

    def save(self, raw, nid, keyword, parent=None):
        pr=normalize_comment(self.platform,parent,nid) if parent is not None else None
        r=normalize_comment(self.platform,raw,nid,pr.get('comment_id') if pr else None,pr.get('text') if pr else None)
        cid=str(r.get('comment_id') or '')
        if self.total>=self.cfg['max_comments'] or (cid and cid in self.comments):return
        if not isinstance(r.get('text'),str):r['text']=''
        r['source_keyword']=keyword
        r['_upstream_record']=as_dict(raw)
        if cid:self.comments.add(cid)
        self.emit('comments',data=[r])
        self.total+=1

    async def root_page(self, raw_note,nid,cursor):
        p,c=self.platform,self.client
        if p=='dy':
            r=await c.get_aweme_comments(nid,cursor or 0)
            return r.get('comments') or [],r.get('cursor'),bool(r.get('has_more'))
        if p=='ks':
            r=await c.get_video_comments(nid,cursor or '')
            next_cursor=r.get('pcursorV2','no_more')
            return r.get('rootCommentsV2') or [],next_cursor,next_cursor!='no_more'
        if p=='bili':
            r=await c.get_video_comments(nid,order_mode=self.fields.CommentOrderType.DEFAULT,next=cursor or 0)
            rows=list(r.get('replies') or [])
            if not cursor:
                def pinned(value):
                    if isinstance(value,dict):
                        if value.get('rpid'):return [value]
                        return [x for v in value.values() for x in pinned(v)]
                    if isinstance(value,list):return [x for v in value for x in pinned(v)]
                    return []
                rows=pinned(r.get('top'))+pinned(r.get('top_replies'))+rows
            info=r.get('cursor') or {}
            return rows,info.get('next'),not info.get('is_end',True)
        if p=='wb':
            pos=cursor or (-1,0)
            r=await c.get_note_comments(nid,pos[0],pos[1])
            return r.get('data') or [],(r.get('max_id') or 0,r.get('max_id_type') or 0),bool(r.get('max_id'))
        if p=='tieba':
            page=cursor or 1
            r=await c._get_pc_page_data(note_id=nid,page=page)
            rows=c._page_extractor.extract_tieba_note_parent_comments_from_api(r,note_detail=raw_note)
            return rows,page+1,page<getattr(raw_note,'total_replay_page',1)
        r=await c.get_root_comments(raw_note.content_id,raw_note.content_type,cursor or '',10)
        paging=r.get('paging') or {}
        return c._extractor.extract_comments(raw_note,r.get('data') or []),c._extractor.extract_offset(paging),not paging.get('is_end',True)

    async def replies(self, root,raw_note,nid,keyword):
        if not self.cfg['include_replies'] or self.total>=self.cfg['max_comments']:return
        p,c,r=self.platform,self.client,as_dict(root)
        cid=str(first(r,'comment_id','cid','commentId','rpid','id') or '')
        if not cid:return
        inline=r.get('reply_comment') if p=='dy' else r.get('replies') if p=='bili' else r.get('comments') if p=='wb' else []
        for sub in inline or []:self.save(sub,nid,keyword,root)
        if p=='wb':return  # Current upstream client only exposes embedded replies.
        count=first(r,'reply_comment_total','reply_comment_total_count','rcount','sub_comment_count','commentCount','subCommentCount') or 0
        if p=='ks':has_more=bool(r.get('hasSubComments'))
        else:
            try:has_more=int(count)>len(inline or [])
            except (ValueError,TypeError):has_more=False
        cursor=1 if p in ('bili','tieba') else 0 if p=='dy' else ''
        pages=0;visited=set()
        while has_more and self.total<self.cfg['max_comments'] and pages<self.cfg['max_comments']:
            await self.sleep();pages+=1
            if p=='dy':
                data=await c.get_sub_comments(nid,cid,cursor)
                rows,new,more=data.get('comments') or [],data.get('cursor'),bool(data.get('has_more'))
            elif p=='ks':
                data=await c.get_video_sub_comments(nid,int(cid),cursor)
                new=data.get('pcursorV2','no_more');rows=data.get('subCommentsV2') or [];more=new!='no_more'
            elif p=='bili':
                data=await c.get_video_level_two_comments(nid,int(cid),cursor,20,self.fields.CommentOrderType.DEFAULT)
                rows=data.get('replies') or [];new=cursor+1;more=cursor*20<int((data.get('page') or {}).get('count',count))
            elif p=='tieba':
                url=f'{c._host}/p/comment?tid={nid}&pid={cid}&fid={r["tieba_id"]}&pn={cursor}'
                await c.playwright_page.goto(url,wait_until='domcontentloaded')
                if not await self.page_safety():continue
                rows=c._page_extractor.extract_tieba_note_sub_comments(await c.playwright_page.content(),parent_comment=root)
                new=cursor+1;more=cursor*10<int(count)
            else:
                data=await c.get_child_comments(cid,cursor,10)
                paging=data.get('paging') or {};rows=c._extractor.extract_comments(raw_note,data.get('data') or [])
                new=c._extractor.extract_offset(paging);more=not paging.get('is_end',True)
            for sub in rows:self.save(sub,nid,keyword,root)
            if not rows or new==cursor or repr(new) in visited:break
            visited.add(repr(new))
            cursor=new;has_more=more

    async def collect_comments(self,raw_note,nid,keyword):
        count=0;cursor=None;seen_roots=set();pages=0;visited=set()
        while count<self.cfg['max_comments_per_note'] and self.total<self.cfg['max_comments'] and pages<=self.cfg['max_comments_per_note']:
            pages+=1
            rows,new,more=await self.root_page(raw_note,nid,cursor)
            if not await self.page_safety():continue
            for root in rows:
                cid=str(first(as_dict(root),'comment_id','cid','commentId','rpid','id') or '')
                if cid and cid in seen_roots:continue
                if count>=self.cfg['max_comments_per_note'] or self.total>=self.cfg['max_comments']:break
                if cid:seen_roots.add(cid)
                self.save(root,nid,keyword);count+=1
                await self.replies(root,raw_note,nid,keyword)
            if not rows or not more or new==cursor or repr(new) in visited:break
            visited.add(repr(new))
            cursor=new;await self.sleep()

    async def run(self):
        exhausted=set();terms=self.cfg['keywords'];quota=max(1,math.ceil(self.cfg['max_notes']/len(terms)))
        for page in range(1,self.cfg['max_notes']+2):
            if len(exhausted)==len(terms):break
            for keyword in terms:
                if len(self.notes)>=self.cfg['max_notes'] or self.total>=self.cfg['max_comments']:return
                if keyword in exhausted:continue
                if self.on_keyword:self.on_keyword(keyword)
                self.emit('collecting',message=f'搜索：{keyword} · 第 {page} 页')
                rows,more=await self.search_page(keyword,page)
                if not await self.page_safety():continue
                if not more or not rows:exhausted.add(keyword)
                processed=0
                for raw in rows:
                    if len(self.notes)>=self.cfg['max_notes'] or self.total>=self.cfg['max_comments']:return
                    preliminary=normalize_note(self.platform,raw)
                    if not preliminary.get('note_id') or preliminary['note_id'] in self.notes:continue
                    try:
                        detail=await self.detail(raw);note=normalize_note(self.platform,detail)
                        nid=note['note_id']
                        if not nid or nid in self.notes:continue
                        self.notes.add(nid)
                        note.update(source_url=self.source_url(detail,note),source_keyword=keyword,_upstream_record=as_dict(detail))
                        self.emit('note',data=note)
                        await self.collect_comments(detail,nid,keyword)
                    except asyncio.CancelledError:raise
                    except Exception:
                        self.emit('failure',message='一条内容或评论请求失败；已保存数据保留，继续当前平台')
                    processed+=1
                    await self.sleep()
                    if processed>=quota:break
