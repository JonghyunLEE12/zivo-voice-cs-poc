"""Shared offline TTS tooling. No model imports; owns at most one child server."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import threading
import urllib.request
import wave

import psutil
import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORTS = {'voxcpm2': 8101, 'cosyvoice3': 8102, 'qwen3tts': 8103, 'kokoro': 8104}
LOCALES = ['en', 'zh-CN', 'zh-TW', 'ja', 'yue']


def resource_status():
    pressure = subprocess.check_output(['memory_pressure'], text=True)
    swap = subprocess.check_output(['sysctl', 'vm.swapusage'], text=True)
    free_pct = int(re.search(r'free percentage:\s*(\d+)%', pressure)[1])
    used = re.search(r'used = ([\d.]+)([MG])', swap)
    swap_gb = float(used[1]) / (1024 if used[2] == 'M' else 1)
    return {'memory_free_pct': free_pct, 'swap_gib': swap_gb,
            'disk_free_gb': shutil.disk_usage(ROOT).free / 1e9,
            'pressure_level': int(subprocess.check_output(['sysctl','-n','kern.memorystatus_vm_pressure_level'],text=True))}


_SWAP_BASELINE = None

def guard(running=False):
    global _SWAP_BASELINE
    status = resource_status()
    if _SWAP_BASELINE is None:
        _SWAP_BASELINE = status['swap_gib']
    status['swap_baseline_gib'] = _SWAP_BASELINE
    reasons = []
    if status['pressure_level'] >= 4:
        reasons.append('critical memory pressure')
    if status['swap_gib'] - _SWAP_BASELINE > 4:
        reasons.append('swap growth >4GiB')
    if status['disk_free_gb'] < 10:
        reasons.append('disk free <10GB')
    if reasons:
        raise RuntimeError('Resource guard: ' + ', '.join(reasons) + '; ' + json.dumps(status))
    return status


def request(base, path, payload=None, timeout=600):
    req = urllib.request.Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def voice(engine, locale):
    if engine == 'kokoro':
        return {'preset': {'en': 'af_heart', 'zh-CN': 'zf_xiaobei', 'zh-TW': 'zf_xiaobei', 'ja': 'jf_alpha'}[locale]}
    manifest = json.loads((ROOT / 'data/refs/MANIFEST.json').read_text())
    ref = next(r for r in manifest['references'] if r['locale'] == manifest['locale_reference'][locale])
    return {'ref_audio_path': str(ROOT / ref['audio_path']), 'ref_text': (ROOT / ref['text_path']).read_text().strip()}


def supported(engine, locale):
    return locale != 'yue' or engine in ('voxcpm2', 'cosyvoice3')


def corpus(locales):
    return [s for s in yaml.safe_load((ROOT / 'data/blind/sentences.yaml').read_text()) if s['locale'] in locales]


def source_hash():
    paths = sorted((ROOT / 'server/normalize').glob('*.py')) + [ROOT / 'data/lexicon.yaml']
    return hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()


def require_model_window():
    # Inspect executable/arguments, not shell source text that happens to mention a model.
    for process in psutil.process_iter(['pid','cmdline']):
        try:
            command = process.info['cmdline'] or []
            if not command:
                continue
            executable = Path(command[0]).name.lower()
            tts = executable.startswith('python') and any(Path(arg).name == 'serve.py' for arg in command[1:])
            ollama = ('ollama' in command[0] and executable == 'llama-server') or (executable == 'ollama' and 'runner' in command[1:])
            if tts or ollama:
                raise RuntimeError(f"Another model process is active (pid={process.pid}); exclusive model window required")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


@contextmanager
def monitor_local_model():
    """Stop this scoring process on pressure instead of leaving an in-flight model."""
    guard()
    require_model_window()
    stopped = threading.Event()
    def monitor():
        while not stopped.wait(2):
            try:
                guard(running=True)
                require_model_window()
            except Exception as exc:
                print(str(exc), file=sys.stderr, flush=True)
                os.kill(os.getpid(), signal.SIGTERM)
                return
    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        yield
    finally:
        stopped.set()
        watcher.join(timeout=3)


@contextmanager
def engine_server(engine, log_prefix):
    guard()
    require_model_window()
    base = f'http://127.0.0.1:{PORTS[engine]}'
    folder = ROOT / ('engines/cosyvoice3' if engine == 'cosyvoice3' else 'engines/mlx_engines')
    command = [str(folder / '.venv/bin/python'), str(folder / 'serve.py'), '--lazy', '--idle-unload', '600']
    command += ['--device', 'mps'] if engine == 'cosyvoice3' else ['--engine', engine]
    command += ['--port', str(PORTS[engine])]
    log_prefix.parent.mkdir(parents=True, exist_ok=True)
    with log_prefix.with_suffix('.log').open('w') as stdout, log_prefix.with_suffix('.err').open('w') as stderr:
        proc = subprocess.Popen(command, cwd=folder, stdout=stdout, stderr=stderr, start_new_session=True)
        stopped = threading.Event()
        violations = []
        def monitor():
            while not stopped.wait(2):
                try:
                    guard(running=True)
                    current = subprocess.check_output(['ps', '-axo', 'command'], text=True)
                    if any('ollama runner' in line or '/ollama/llama-server' in line for line in current.splitlines()):
                        raise RuntimeError('Ollama model started during exclusive benchmark window')
                except Exception as exc:
                    violations.append(str(exc))
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGTERM)
                    return
        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f'{engine} server exited {proc.returncode}; see {log_prefix}.err')
                try:
                    info = request(base, '/v1/info', timeout=1)
                    if info['engine'] != engine:
                        raise RuntimeError('Port occupied by a different engine')
                    break
                except (OSError, TimeoutError):
                    time.sleep(.25)
            else:
                raise RuntimeError('Server startup timed out')
            guard()
            yield base
            if violations:
                raise RuntimeError(violations[0])
        finally:
            stopped.set()
            watcher.join(timeout=3)
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)


def synthesize(base, engine, sentence, text, seed, stream=False):
    guard()
    payload = {'text': text, 'locale': sentence['locale'], 'voice': voice(engine, sentence['locale']),
               'seed': seed, 'stream': stream}
    req = urllib.request.Request(base + '/v1/synthesize', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as response:
        if response.headers['Content-Type'].lower() != 'audio/l16; rate=24000; channels=1':
            raise RuntimeError('Unexpected audio format')
        first = response.read(2)
        ttfa = (time.perf_counter() - start) * 1000
        pcm = first + response.read()
        synth_ms = response.headers.get('X-Synth-Ms')
    wall = (time.perf_counter() - start) * 1000
    if not pcm or len(pcm) % 2:
        raise RuntimeError('Empty or malformed PCM')
    audio = np.frombuffer(pcm, dtype='<i2').astype(np.float64) / 32768
    duration = len(audio) / 24000
    failure = audio_failure(audio, sentence['text'], sentence['locale'])
    return pcm, {'duration_s': duration, 'synth_ms': float(synth_ms) if synth_ms else wall,
                 'synth_clock': 'engine' if synth_ms else 'client_stream_wall',
                 'ttfa_ms': ttfa, 'wall_ms': wall, 'rtf': (float(synth_ms) if synth_ms else wall) / 1000 / duration,
                 'rms': float(np.sqrt(np.mean(audio ** 2))), 'failure': failure}


def audio_failure(audio, text, locale):
    if not len(audio) or np.sqrt(np.mean(audio ** 2)) <= 1e-5 or np.max(np.abs(audio)) <= 1e-4:
        return 'silent (RMS <=1e-5 or peak <=1e-4)'
    units = len(text.split()) if locale == 'en' else len(re.sub(r'\W', '', text))
    expected = max(.5, units / (2.5 if locale == 'en' else 5))
    duration = len(audio) / 24000
    if duration > expected * 3:
        return f'abnormal length {duration:.2f}s >3x estimated {expected:.2f}s'
    if duration < expected * .25:
        return f'possible truncation {duration:.2f}s <0.25x estimated {expected:.2f}s'
    return None


def write_wav(path, pcm):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)
