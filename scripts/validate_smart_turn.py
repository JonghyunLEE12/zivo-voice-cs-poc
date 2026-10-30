#!/usr/bin/env python3
"""Owned-process TCP verification. Live mode requires the coordinator's model window."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.llm_resources import ResourceGuard
from scripts.ws_wav_client import run as ws_run


async def warmup_live(options, folder, *, client=None, ws_runner=ws_run):
    """Warm Ollama explicitly, then discard one real server turn to warm Whisper.

    Whisper lives inside uvicorn, so warming a separate Python process would not
    warm its model cache. The discarded WS turn goes through the measured route.
    """
    from server.llm.ollama_client import OllamaClient
    client = client or OllamaClient()
    started = time.perf_counter()
    ollama = await client.warmup()
    request = argparse.Namespace(**vars(options))
    request.output = str(folder/'warmup-en')
    request.warm = False
    request.measurement_phase = 'warmup_discarded'
    result = await ws_runner(request)
    record = {'warm': True, 'ollama': ollama, 'server_warmup_turn': result,
              'elapsed_ms': (time.perf_counter()-started)*1000,
              'method': 'Ollama warmup + discarded en WebSocket turn warms server Whisper cache'}
    (folder/'warmup.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record


async def validate(args):
    folder = ROOT / 'reports/t11' / args.mode
    folder.mkdir(parents=True, exist_ok=True)
    guard = ResourceGuard(ROOT / 'reports/t11/resources.jsonl')
    if args.baseline_swap_mib is not None:
        guard.baseline_swap = args.baseline_swap_mib
    guard.check(starting=True)
    processes, handles = [], []
    def launch(command, stem, env=None, cwd=ROOT):
        stdout = (folder/f'{stem}.log').open('w')
        stderr = (folder/f'{stem}.err').open('w')
        handles.extend([stdout, stderr])
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        processes.append(process)
        return process

    def require_port(port):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'Port {port} already occupied; will not touch another process')

    async def ready(port, process):
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'Server exited with {process.returncode}; inspect {folder}')
            try:
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.close(); await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(.1)
        raise TimeoutError(f'Port {port} startup timeout')

    async def workload():
        require_port(args.port)
        if args.mode == 'live':
            if not args.model_window_released:
                raise RuntimeError('Live mode requires explicit --model-window-released after coordinator release')
            require_port(8104)
            listing = subprocess.check_output(['ps', '-axo', 'command'], text=True)
            if any(('serve.py' in line and 'python' in line.lower()) or 'ollama runner' in line or '/ollama/llama-server' in line
                   for line in listing.splitlines()):
                raise RuntimeError('An existing model process is active; exclusive model window required')
            engine_folder = ROOT/'engines/mlx_engines'
            engine = launch([str(engine_folder/'.venv/bin/python'), 'serve.py', '--engine', 'kokoro',
                             '--port', '8104', '--lazy', '--idle-unload', '600'], 'kokoro', cwd=engine_folder)
            await ready(8104, engine)
            target = 'server.app:app'
            env = os.environ.copy()
        else:
            target = 'tests.voice_fixture_app:app'
            env = {**os.environ, 'T9_STUB_LOG_DIR': str(folder)}
        server = launch([str(ROOT/'.venv/bin/python'), '-m', 'uvicorn', target,
                         '--host', '127.0.0.1', '--port', str(args.port)], 'server', env=env)
        await ready(args.port, server)
        if args.mode == 'live':
            await warmup_live(argparse.Namespace(wav=ROOT/args.inputs/'en.wav',
                url=f'ws://127.0.0.1:{args.port}/ws/call', locale='en', engine='kokoro',
                normalize='full', barge_in=False, timeout=300), folder)
        results = []
        for locale in ['en', 'zh-CN', 'zh-TW', 'ja']:
            for detector in ['vad', 'vad+smartturn']:
                result = await ws_run(argparse.Namespace(wav=ROOT/args.inputs/f'{locale}.wav',
                    url=f'ws://127.0.0.1:{args.port}/ws/call', locale=locale, engine='kokoro',
                    normalize='full', barge_in=False, turn_detect=detector,
                    timeout=300, output=str(folder/f'{locale}-{detector}'), warm=True))
                result['turn_detect'] = detector
                results.append(result)
        results.append(await ws_run(argparse.Namespace(wav=ROOT/args.inputs/'en.wav',
            url=f'ws://127.0.0.1:{args.port}/ws/call', locale='en', engine='kokoro', normalize='full',
            turn_detect='vad+smartturn', barge_in=True, timeout=300, output=str(folder/'en-barge'), warm=True)))
        await asyncio.sleep(.1)  # allow WS disconnect/session final append
        from server.pipeline.sessions import aggregate
        (folder/'sessions.json').write_text(json.dumps(aggregate(), ensure_ascii=False, indent=2))
        (folder/'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps({'mode': args.mode, 'completed': len(results), 'report': str(folder/'summary.json')}))
    try:
        await guard.run(workload)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.to_thread(process.wait, timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.to_thread(process.wait, timeout=5)
        for handle in handles: handle.close()
        # Only live mode started Ollama requests; never unload another worker's model in stub mode.
        if args.mode == 'live' and processes:
            from server.llm.ollama_client import OllamaClient
            await OllamaClient().unload()
        guard.check()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['stub', 'live'], default='live')
    parser.add_argument('--model-window-released', action='store_true')
    parser.add_argument('--port', type=int, default=8011)
    parser.add_argument('--inputs', default='reports/t9/inputs')
    parser.add_argument('--baseline-swap-mib', type=float)
    asyncio.run(validate(parser.parse_args()))


if __name__ == '__main__': main()
