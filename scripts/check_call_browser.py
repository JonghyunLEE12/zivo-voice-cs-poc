"""Headless installed Chrome validation. No speech-model server is started by this script.
Setup: uv --cache-dir reports/t10/uv-cache pip install --target reports/t10/qa-deps playwright
Run: PYTHONPATH=reports/t10/qa-deps .venv/bin/python scripts/check_call_browser.py
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'reports/t10/qa-deps'))
from playwright.async_api import async_playwright
import httpx

OUT = ROOT / 'reports/t10'


def resource():
    swap = subprocess.check_output(['sysctl', 'vm.swapusage'], text=True).strip()
    pressure = int(subprocess.check_output(['sysctl', '-n', 'kern.memorystatus_vm_pressure_level'], text=True))
    disk = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize
    with (OUT / 'resources.jsonl').open('a') as f:
        f.write(json.dumps({'time': time.time(), 'swap': swap, 'pressure': pressure, 'disk_free_bytes': disk}) + '\n')
    if pressure == 4 or disk < 10_000_000_000:
        raise RuntimeError('Resource guard triggered')


async def main():
    resource()
    # Real speech for Chrome fake microphone; trailing silence ensures a VAD end.
    import soundfile as sf
    import numpy as np
    pcm, rate = sf.read(ROOT / 'data/ref_voices/en/ref.wav')
    pcm = pcm / max(float(np.max(np.abs(pcm))), .001) * .6
    sf.write(OUT / 'mic.wav', np.concatenate([np.zeros(rate), pcm, np.zeros(rate * 2)]), rate, subtype='PCM_16')
    handle = (OUT / 'stub-server.txt').open('w')
    proc = subprocess.Popen([str(ROOT / '.venv/bin/uvicorn'), 'scripts.call_browser_fixture:app', '--host', '127.0.0.1', '--port', '8019'], cwd=ROOT, stdout=handle, stderr=handle)
    records = []
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                try:
                    if (await client.get('http://127.0.0.1:8019/')).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(.1)
            else:
                raise RuntimeError('Fixture server did not start')
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True, args=['--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', f'--use-file-for-fake-audio-capture={OUT / "mic.wav"}'])
            try:
                for locale in ['en', 'zh-CN', 'zh-TW', 'ja']:
                    resource()
                    page = await browser.new_page(viewport={'width': 1280, 'height': 1000})
                    logs, errors, requests = [], [], []
                    def console(message):
                        if message.text.startswith('[call] '):
                            logs.append(json.loads(message.text[7:]))
                    page.on('console', console)
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda req: requests.append(req.url))
                    await page.goto('http://127.0.0.1:8019/?dev=1')
                    await page.select_option('#locale', locale)
                    await page.click('#connect')
                    await page.wait_for_function("!document.querySelector('#wav').disabled")
                    await page.set_input_files('#wav', str(ROOT / f'data/ref_voices/{locale}/ref.wav'))
                    await page.wait_for_function("document.querySelector('#logs').textContent.includes('playback.finished')", timeout=15000)
                    assert await page.locator('.assistant').inner_text()
                    assert not errors, errors
                    assert any(e['type'] == 'c_vad_end' for e in logs)
                    assert any(e['type'] == 'c_play_start' for e in logs)
                    assert not [url for url in requests if not url.startswith('http://127.0.0.1:8019/')]
                    await page.screenshot(path=str(OUT / f'call-{locale}.png'), full_page=True)
                    # A second real queue, interrupted after its first scheduled node starts.
                    await page.set_input_files('#wav', str(ROOT / f'data/ref_voices/{locale}/ref.wav'))
                    await page.wait_for_function("document.querySelector('#logs').textContent.includes('c_play_start') && document.querySelector('#status').textContent.includes('답변 중')")
                    await page.click('#interrupt')
                    await page.wait_for_timeout(350)
                    interrupted = [e for e in logs if e['type'] == 'barge_in' and e['reason'] == 'test']
                    assert interrupted and interrupted[-1]['nodes_active_before'], interrupted
                    assert interrupted[-1]['stop_ms'] <= 100
                    assert interrupted[-1]['nodes_active_after'] is False
                    assert not [e for e in logs if e['type'] == 'playback.finished' and e['turn_num'] == 2]
                    # Fresh turn after cancellation proves queue/session recovery.
                    await page.set_input_files('#wav', str(ROOT / f'data/ref_voices/{locale}/ref.wav'))
                    await page.wait_for_function("document.querySelector('#logs').textContent.split('\\n').some(line => { const e = JSON.parse(line); return e.type === 'playback.finished' && e.turn_num === 3; })")
                    await page.wait_for_timeout(100)
                    records.append({'locale': locale, 'events': logs, 'errors': errors, 'requests': requests})
                    await page.click('#stop')
                    await page.close()
                # Actual Silero browser VAD via fake getUserMedia speech, still fake server models.
                resource()
                page = await browser.new_page(viewport={'width': 1280, 'height': 1000})
                logs, errors, requests = [], [], []
                page.on('console', lambda m: logs.append(json.loads(m.text[7:])) if m.text.startswith('[call] ') else None)
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('request', lambda r: requests.append(r.url))
                await page.goto('http://127.0.0.1:8019/')
                assert not await page.locator('#dev').is_visible()
                await page.click('#start')
                await page.wait_for_function("document.querySelector('#logs').textContent.includes('vad.ready')", timeout=45000)
                await page.wait_for_function("document.querySelector('#logs').textContent.includes('playback.finished')", timeout=45000)
                await page.click('#stop')
                assert not errors, errors
                assert any(e['type'] == 'c_vad_end' and e['source'] == 'vad' for e in logs)
                assert not [url for url in requests if not url.startswith('http://127.0.0.1:8019/')]
                records.append({'locale': 'en-microphone-vad', 'events': logs, 'errors': errors, 'requests': requests})
                await page.set_viewport_size({'width': 390, 'height': 844})
                await page.screenshot(path=str(OUT / 'call-mobile.png'), full_page=True)
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.close()
            finally:
                await browser.close()
    finally:
        (OUT / 'browser-events.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
        proc.terminate()
        proc.wait(timeout=10)
        handle.close()
        resource()
    latest = {}
    for line in (OUT / 'stub/turns.jsonl').read_text().splitlines():
        row = json.loads(line)
        latest[(row['session_id'], row['turn_num'])] = row
    sessions = {e['session_id'] for record in records for e in record['events'] if e['type'] == 'session.ready'}
    normal = [row for row in latest.values() if row['session_id'] in sessions and row['status'] == 'done' and row['marks']['c_play_start'] is not None]
    assert len(normal) >= 9, len(normal)
    assert all(row['marks']['c_vad_end'] is not None for row in normal)
    summary = {'browser': 'installed Google Chrome, headless', 'mode': 'STT/LLM/TTS stubs; actual browser Web Audio and Silero VAD', 'sessions': len(records), 'normal_turns_with_client_marks': len(normal), 'barge_in_stop_ms': {r['locale']: [e['stop_ms'] for e in r['events'] if e['type'] == 'barge_in' and e['reason'] == 'test'] for r in records}, 'client_vad_to_play_ms': {r['locale']: [e['client_vad_to_play_ms'] for e in r['events'] if e['type'] == 'c_play_start'] for r in records}}
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
