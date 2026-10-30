"""Model-free installed-Chrome check of Smart Turn control and session report."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'reports/t10/qa-deps'))
import httpx
from playwright.async_api import async_playwright
from scripts.llm_resources import ResourceGuard

async def main():
    out=ROOT/'reports/t11'; guard=ResourceGuard(out/'page-resources.jsonl'); guard.check(starting=True)
    stdout=(out/'page-server.log').open('w'); stderr=(out/'page-server.err').open('w')
    proc=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-m','uvicorn','server.app:app','--host','127.0.0.1','--port','8012'],cwd=ROOT,stdout=stdout,stderr=stderr)
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                if proc.poll() is not None: raise RuntimeError('Server startup failed')
                try:
                    if (await client.get('http://127.0.0.1:8012/report')).status_code==200: break
                except httpx.ConnectError: pass
                await asyncio.sleep(.1)
            paths=['/','/blind','/blind/results','/report','/api/report/sessions']
            status={p:(await client.get('http://127.0.0.1:8012'+p)).status_code for p in paths}
            assert all(v==200 for v in status.values())
            metrics=(await client.get('http://127.0.0.1:8012/api/report/sessions')).json()
        async with async_playwright() as p:
            browser=await p.chromium.launch(channel='chrome',headless=True)
            try:
                page=await browser.new_page(viewport={'width':1280,'height':1000}); errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto('http://127.0.0.1:8012/')
                assert await page.locator('#turn-detect').input_value()=='vad'
                await page.select_option('#turn-detect','vad+smartturn')
                assert await page.locator('#turn-detect').input_value()=='vad+smartturn'
                await page.screenshot(path=str(out/'smart-toggle.png'))
                await page.goto('http://127.0.0.1:8012/report')
                assert await page.get_by_role('heading',name='통화 지표',exact=True).count()==1
                assert await page.locator('body').inner_text()
                await page.screenshot(path=str(out/'session-report.png'))
                assert not errors,errors
            finally: await browser.close()
        guard.check()
        (out/'page-acceptance.json').write_text(json.dumps({'routes':status,'toggle':'vad -> vad+smartturn','page_errors':errors,'calls':{k:v['calls'] for k,v in metrics.items()}},ensure_ascii=False,indent=2))
    finally:
        proc.terminate(); proc.wait(timeout=10); stdout.close();stderr.close()

if __name__=='__main__':asyncio.run(main())
