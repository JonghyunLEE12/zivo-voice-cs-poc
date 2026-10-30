"""Explicit model-window-only Chrome proof; owned local processes are unloaded afterward."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'reports/t10/qa-deps')]
from playwright.async_api import async_playwright
from scripts.llm_resources import ResourceGuard
from scripts.validate_voice_pipeline import warmup_live
from server.llm.ollama_client import OllamaClient

OUT = ROOT/'reports/t10/live'

async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-window-released', action='store_true')
    args = parser.parse_args()
    if not args.model_window_released:
        raise SystemExit('Coordinator model-window release required')
    OUT.mkdir(parents=True, exist_ok=True)
    for port in [8000, 8104]:
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'Port {port} occupied; no other process will be stopped')
    listing = subprocess.check_output(['ps', '-axo', 'command'], text=True)
    if any(('serve.py' in line and 'python' in line.lower()) or 'ollama runner' in line or '/ollama/llama-server' in line for line in listing.splitlines()):
        raise RuntimeError('Other model processes active')
    guard = ResourceGuard(OUT/'resources.jsonl')
    guard.check(starting=True)
    processes, handles, records = [], [], []
    def launch(argv, name, cwd):
        stdout, stderr = (OUT/f'{name}.log').open('w'), (OUT/f'{name}.err').open('w')
        handles.extend([stdout, stderr])
        process = subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True)
        processes.append(process)
        return process
    async def wait_port(port, process):
        for _ in range(900):
            if process.poll() is not None:
                raise RuntimeError(f'Process exited: {process.returncode}')
            try:
                _, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.close(); await writer.wait_closed(); return
            except OSError:
                await asyncio.sleep(.1)
        raise TimeoutError(str(port))
    async def workload():
        engine_dir = ROOT/'engines/mlx_engines'
        engine = launch([str(engine_dir/'.venv/bin/python'), 'serve.py', '--engine', 'kokoro', '--port', '8104', '--lazy', '--idle-unload', '600'], 'kokoro', engine_dir)
        await wait_port(8104, engine)
        server = launch([str(ROOT/'.venv/bin/python'), '-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '8000'], 'server', ROOT)
        await wait_port(8000, server)
        await warmup_live(argparse.Namespace(wav=ROOT/'reports/stt/say/en.wav', url='ws://127.0.0.1:8000/ws/call', locale='en', engine='kokoro', normalize='full', barge_in=False, timeout=300), OUT)
        async with async_playwright() as p:
            browser = await p.chromium.launch_persistent_context(str(OUT/'chrome-profile'), channel='chrome', headless=True, viewport={'width':1280,'height':1000}, args=['--autoplay-policy=no-user-gesture-required'])
            try:
                for locale in ['en','zh-CN','zh-TW','ja']:
                    page = await browser.new_page()
                    page.set_default_timeout(300000)
                    events, errors = [], []
                    record = {'locale':locale, 'warm':True, 'input':f'reports/stt/say/{locale}.wav', 'events':events, 'errors':errors}
                    records.append(record)
                    page.on('console', lambda m: events.append(json.loads(m.text[7:])) if m.text.startswith('[call] ') else None)
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    await page.goto('http://127.0.0.1:8000/?dev=1')
                    await page.select_option('#locale', locale)
                    await page.click('#connect')
                    await page.wait_for_function("!document.querySelector('#wav').disabled || !!document.querySelector('#error').textContent", timeout=300000)
                    assert not await page.locator('#error').inner_text()
                    await page.set_input_files('#wav', str(ROOT/f'reports/stt/say/{locale}.wav'))
                    await page.wait_for_function("document.querySelector('#logs').textContent.includes('playback.finished') || !!document.querySelector('#error').textContent", timeout=300000)
                    assert not await page.locator('#error').inner_text()
                    assert not errors, errors
                    record['stt'] = await page.locator('.user').inner_text()
                    record['reply'] = await page.locator('.assistant').inner_text()
                    assert record['stt'] and record['reply']
                    await page.screenshot(path=str(OUT/f'{locale}.png'), full_page=True)
                    await page.click('#stop')
                    await page.close()
                    (OUT/'browser-events.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
                    print(json.dumps({'locale':locale,'completed':True,'reply':record['reply']},ensure_ascii=False), flush=True)
            finally:
                await browser.close()
        sessions = {e['session_id'] for r in records for e in r['events'] if e['type']=='session.ready'}
        latest={}
        for path in (ROOT/'data/logs').glob('turns-*.jsonl'):
            for line in path.read_text().splitlines():
                row=json.loads(line)
                if row['session_id'] in sessions:
                    latest[(row['session_id'],row['turn_num'])]=row
        rows=list(latest.values())
        assert len(rows)==4, len(rows)
        assert all(row['marks']['c_vad_end'] is not None and row['marks']['c_play_start'] is not None for row in rows)
        (OUT/'turns-final.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        summary=[{'locale':row['locale'],'status':row['status'],'marks':row['marks'],'durations':row['durations'],'audio_bytes':row['audio_bytes']} for row in rows]
        (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    try:
        await guard.run(workload)
    finally:
        (OUT/'browser-events.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try: await asyncio.to_thread(process.wait, timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.to_thread(process.wait, timeout=5)
        for handle in handles: handle.close()
        if processes:
            await OllamaClient().unload()
        guard.check()

if __name__=='__main__':
    asyncio.run(main())
