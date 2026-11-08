"""Stable platform contract; never import upstream modules here."""
import html
import re

PLATFORMS = {
    'xhs': {'name':'小红书','module':'xhs','crawler':'XiaoHongShuCrawler','client':'XiaoHongShuClient','attribute':'xhs_client','login':'XiaoHongShuLogin','sorts':['general','popularity_descending','time_descending']},
    'dy': {'name':'抖音','module':'douyin','crawler':'DouYinCrawler','client':'DouYinClient','attribute':'dy_client','login':'DouYinLogin','sorts':['general','popularity_descending','time_descending']},
    'ks': {'name':'快手','module':'kuaishou','crawler':'KuaishouCrawler','client':'KuaiShouClient','attribute':'ks_client','login':'KuaishouLogin','sorts':['general']},
    'bili': {'name':'B站','module':'bilibili','crawler':'BilibiliCrawler','client':'BilibiliClient','attribute':'bili_client','login':'BilibiliLogin','sorts':['general','popularity_descending','time_descending']},
    'wb': {'name':'微博','module':'weibo','crawler':'WeiboCrawler','client':'WeiboClient','attribute':'wb_client','login':'WeiboLogin','sorts':['general','popularity_descending','time_descending']},
    'tieba': {'name':'贴吧','module':'tieba','crawler':'TieBaCrawler','client':'BaiduTieBaClient','attribute':'tieba_client','login':'BaiduTieBaLogin','sorts':['general','time_descending']},
    'zhihu': {'name':'知乎','module':'zhihu','crawler':'ZhihuCrawler','client':'ZhiHuClient','attribute':'zhihu_client','login':'ZhiHuLogin','sorts':['general']},
}
ALIASES = {'douyin':'dy','kuaishou':'ks','bilibili':'bili','weibo':'wb', **{v['name']:k for k,v in PLATFORMS.items()}}

def platform_id(value):
    p = str(value or '').strip()
    return ALIASES.get(p,p)

def platform_name(value):
    return PLATFORMS.get(value,{}).get('name',value)

def platform_names(values):
    return '、'.join(platform_name(p) for p in values)

def catalog():
    return [{'id':p,'name':v['name'],'sorts':v['sorts'],'replies':True,
             'reply_note':'仅采集接口已返回的嵌入回复，不保证完整' if p=='wb' else '采集可获取的回复，不保证完整'} for p,v in PLATFORMS.items()]

def first(record,*keys):
    for key in keys:
        value=record.get(key)
        if value is not None and value!='':return value
    return None

def plain(value):
    if value is None:return None
    return html.unescape(re.sub(r'<[^>]*>','',str(value)))

def as_dict(value):
    return value.model_dump() if hasattr(value,'model_dump') else dict(value)

def normalize_note(platform, raw):
    r=as_dict(raw)
    if platform=='dy' and 'aweme_info' in r:r=r['aweme_info'] or {}
    if platform=='ks' and 'photo' in r:r={**r,**(r['photo'] or {})}
    if platform=='bili' and 'View' in r:r=r['View'] or {}
    if platform=='wb' and 'mblog' in r:r=r['mblog'] or {}
    nid=first(r,'note_id','aweme_id','video_id','content_id','aid','id')
    if platform=='zhihu' and not r.get('note_id') and nid and r.get('content_type'):
        nid=f'{r["content_type"]}:{nid}'
    body=first(r,'body','note_body','desc','caption','content_text','text','content')
    title=first(r,'title','note_title','caption','desc','text','content')
    return {**r,'platform':platform,'note_id':str(nid) if nid is not None else None,
            'title':plain(title),'body':plain(body),
            'source_url':first(r,'source_url','note_url','aweme_url','video_url','content_url')}

def normalize_comment(platform, raw, note_id=None, parent_id=None, parent_text=None):
    r=as_dict(raw)
    content=first(r,'text','content')
    if isinstance(content,dict):content=content.get('message')
    if content is None and ('text' in r or 'content' in r):content=''
    nid=note_id or first(r,'note_id','aweme_id','video_id','content_id')
    if platform=='zhihu' and not note_id and not r.get('note_id') and nid and r.get('content_type'):
        nid=f'{r["content_type"]}:{nid}'
    parent=first(r,'parent_comment_id','reply_id','parent','rootid','reply_comment_id','root')
    if isinstance(parent,dict):parent=first(parent,'id','comment_id')
    if str(parent or '') in ('0','-1'):parent=None
    if platform=='xhs' and isinstance(r.get('target_comment'),dict):
        parent=first(r['target_comment'],'id') or parent
        parent_text=first(r['target_comment'],'content') or parent_text
    # A reply may address another child, rather than the root currently traversed.
    # Never label the root text as that child's parent text.
    if parent and parent_id and str(parent)!=str(parent_id):parent_text=None
    upstream=r.get('_upstream_record') if isinstance(r.get('_upstream_record'),dict) else r
    user=r.get('user_info') or r.get('user') or r.get('member') or upstream.get('user_info') or upstream.get('user') or upstream.get('member') or {}
    user_id=first(r,'user_id','author_id','uid','mid') or first(upstream,'user_id','author_id','uid','mid')
    if not user_id and isinstance(user,dict):user_id=first(user,'user_id','uid','mid','id','userId')
    if str(user_id or '') in ('','0','-1'):user_id=None
    return {**r,'platform':platform,'comment_id':first(r,'comment_id','cid','commentId','rpid','id'),
            'user_id':str(user_id) if user_id is not None else None,
            'parent_relation_known':bool(r.get('parent_relation_known') or parent_id or any(k in r for k in ('parent_comment_id','reply_id','rootid','reply_comment_id','root'))),
            'root_comment_id':first(r,'root_comment_id','rootid'),
            'note_id':str(nid) if nid is not None else None,'text':plain(content) if isinstance(content,str) and platform in ('wb','zhihu') else content,
            'parent_comment_id':parent or parent_id,'parent_text':r.get('parent_text') or parent_text,
            'published_at':first(r,'published_at','create_time','publish_time','ctime','timestamp','created_at'),
            'like_count':first(r,'like_count','digg_count','like','comment_like_count','likeCount','liked_count','likedCount'),
            'source_url':first(r,'source_url','note_url','aweme_url','video_url','content_url')}
