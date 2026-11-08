"""Render the actual shared print exports as PDF; no collection or model requests."""
import argparse
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright

def main():
    parser=argparse.ArgumentParser();parser.add_argument('task_id');parser.add_argument('--chrome',default=r'C:\Program Files\Google\Chrome\Application\chrome.exe');args=parser.parse_args()
    out=Path('output/pdf');out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.chrome,headless=True)
        for view,label in [('brief','精简版'),('full','完整版')]:
            response=httpx.get(f'http://127.0.0.1:8765/api/tasks/{args.task_id}/export/html',params={'view':view},trust_env=False);response.raise_for_status()
            page=browser.new_page(viewport={'width':1280,'height':900});page.set_content(response.text,wait_until='load');page.emulate_media(media='print')
            assert page.locator('.dr-donut').count()==1 and page.locator('.dr-matrix circle').count()>0
            assert page.locator('details:not([open])').count()==0
            product=page.locator('.dr-cover h1').inner_text().splitlines()[0]
            path=out/(product+'-共享模板-'+label+'.pdf')
            # Exact print colors preserve charts even when the background checkbox is off.
            page.pdf(path=str(path),prefer_css_page_size=True,print_background=False,display_header_footer=False)
            print(view,path,'evidence_entries',page.locator('.dr-print-source').count());page.close()
        browser.close()

if __name__=='__main__':main()
