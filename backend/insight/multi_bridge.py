"""Independent process bootstrap for six non-XHS MediaCrawler platform adapters.

Loads the user's upstream under NON-COMMERCIAL LEARNING LICENSE 1.1. No upstream edits.
"""
import asyncio
import importlib
import inspect
import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from insight.platforms import PLATFORMS
from insight.upstream_adapter import PlatformAdapter

async def main(settings):
    platform=settings['platform'];spec=PLATFORMS[platform];cfg=settings['config'];folder=Path(settings['output'])
    def emit(kind,**data):
        print('INSIGHT_EVENT '+json.dumps({'kind':kind,'platform':platform,**data},ensure_ascii=False),flush=True)
    crawler=None
    try:
        sys.path.insert(0,settings['crawler_path']);os.chdir(settings['crawler_path'])
        mc=importlib.import_module('config')
        for name,value in {'PLATFORM':platform,'CRAWLER_TYPE':'search','KEYWORDS':','.join(cfg['keywords']),
                           'LOGIN_TYPE':'qrcode','ENABLE_IP_PROXY':False,'ENABLE_GET_MEDIA':False,
                           'ENABLE_GET_COMMENTS':True,'ENABLE_GET_SUB_COMMENTS':cfg['include_replies'],
                           'HEADLESS':False,'CDP_HEADLESS':False,'ENABLE_CDP_MODE':True,'CDP_CONNECT_EXISTING':False,
                           'MAX_CONCURRENCY_NUM':1,'SAVE_DATA_PATH':str(folder/'upstream')}.items():setattr(mc,name,value)
        module='media_platform.'+spec['module']
        core=importlib.import_module(module+'.core');clients=importlib.import_module(module+'.client');fields=importlib.import_module(module+'.field')
        utils=importlib.import_module('tools.utils');utils.logger.disabled=True
        base=getattr(core,spec['crawler']);client_class=getattr(clients,spec['client'])
        async def refresh():
            client=getattr(crawler,spec['attribute'])
            await client.update_cookies(crawler.browser_context,urls=crawler.cookie_urls)
        async def pong():
            method=getattr(crawler,spec['attribute']).pong
            return await method(browser_context=crawler.browser_context) if 'browser_context' in inspect.signature(method).parameters else await method()
        async def pause(message):
            emit('waiting_login',message=message)
            marker=folder/'resume'
            while not marker.exists():await asyncio.sleep(.5)
            marker.unlink(missing_ok=True);await refresh()
            emit('collecting',message='已恢复，正在核对平台登录状态')
        async def manual_login(_login):
            while True:
                await pause('请在采集浏览器中手动完成该平台登录或验证，再点击恢复')
                if await pong():return
        getattr(core,spec['login']).begin=manual_login
        # Request-level interception keeps security restrictions out of upstream generic catches.
        for name in ('request','request_rest_v2','request_rest_v2_signed','_fetch_json_by_browser','_get_pc_page_data'):
            original=getattr(client_class,name,None)
            if original is None:continue
            original=getattr(original,'__wrapped__',original)
            def wrap(original):
                async def guarded(client,*args,**kwargs):
                    while True:
                        try:
                            result=await original(client,*args,**kwargs)
                            if isinstance(result,dict):
                                code=str(result.get('status_code',result.get('code','')))
                                message=str(result.get('message',result.get('msg',''))).lower()
                                if code in ('401','403','429','-101','-352','300011') or any(s in message for s in ('captcha','未登录','登录失效','请登录','安全验证')):
                                    await pause('平台要求人工登录或验证');continue
                            return result
                        except asyncio.CancelledError:raise
                        except Exception as exc:
                            message=str(exc).lower();status=getattr(getattr(exc,'response',None),'status_code',None)
                            page=getattr(client,'playwright_page',None)
                            title=await page.title() if page and hasattr(page,'title') else ''
                            if status in (401,403,429,461,471) or any(s in message for s in ('captcha','登录','验证码','安全验证','风控','请求过于频繁','forbidden','too many requests','471','461','-101','-352','300011','status=401','status=403','status=429')) or '安全验证' in title:
                                await pause('平台要求人工登录或验证')
                            else:raise
                return guarded
            setattr(client_class,name,wrap(original))
        class BoundedCrawler(base):
            async def search(self):
                variables=importlib.import_module('var')
                def keyword_context(keyword):
                    for name in ('source_keyword_var','request_keyword_var'):
                        variable=getattr(variables,name,None)
                        if variable is not None:variable.set(keyword)
                adapter=PlatformAdapter(platform,getattr(self,spec['attribute']),fields,{**cfg,'_crawl_interval':mc.CRAWLER_MAX_SLEEP_SEC},emit,settings,pause,keyword_context)
                await adapter.run()
            async def get_specified_tieba_notes(self):return  # No unrelated preconfigured forum names.
        crawler=BoundedCrawler()
        work=asyncio.create_task(crawler.start())
        async def watch():
            while not work.done():
                if (folder/'cancel').exists():work.cancel();return
                await asyncio.sleep(.5)
        watcher=asyncio.create_task(watch())
        try:
            await work;emit('done')
        finally:
            watcher.cancel()
            if getattr(crawler,'cdp_manager',None):await crawler.cdp_manager.cleanup(force=True)
            elif getattr(crawler,'browser_context',None):await crawler.browser_context.close()
    except asyncio.CancelledError:pass
    except ImportError:emit('fatal',message='采集依赖或接口不匹配，请检查 MediaCrawler 环境与本地版本')
    except Exception:emit('fatal',message='平台采集启动或请求失败，请检查登录、网络、浏览器与 MediaCrawler 版本')

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))))
