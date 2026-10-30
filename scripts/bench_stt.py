#!/usr/bin/env python3
"""Benchmark refs or arbitrary WAVs with raw/display text and measured timings.

Examples:
  uv run python scripts/bench_stt.py --refs --output reports/stt/refs
  uv run python scripts/bench_stt.py --wav en=data/smoke/kokoro-en.wav
  uv run python scripts/bench_stt.py --manifest reports/stt/say/manifest.json
Manifest format: {"items": [{"locale": "en", "audio_path": "...",
                             "display_text": "...", "source": "..."}]}.
Paths are relative to the project root. No downloads, server changes, or LLM
unloading are performed (other workers may be using those processes).
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.stt.base import LanguageState
from server.stt.mlx_whisper_stt import MLXWhisperSTT


def reference_items(locales):
    manifest = json.loads((ROOT / 'data/refs/MANIFEST.json').read_text())
    refs = {entry['locale']: entry for entry in manifest['references']}
    return [dict(locale=locale,
                 audio_path=refs[manifest['locale_reference'][locale]]['audio_path'],
                 display_text=refs[manifest['locale_reference'][locale]]['raw_transcription'],
                 source='Google FLEURS CC-BY-4.0; see data/refs/MANIFEST.json')
            for locale in locales]


def markdown(report):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    lines = ['# STT benchmark', '', f"UTC: {report['created_at']}", '',
             'Times include file decoding and STT; first inference includes lazy model loading.',
             'Concurrent TTS/LLM workers may affect timing; these are functional smoke measurements, not isolated performance claims.',
             'zh-TW FLEURS uses the mainland Mandarin recording: 대만 억양 참조 없음.',
             'Specified-language mode does not measure LID probability (null); auto mode does.',
             'Qwen3-ASR optional cross-check not installed/run; no additional download.', '',
             '| Source / WAV | Locale | Mode | Seconds | STT ms | RTF | Detected / probability | Raw | Display |',
             '|---|---|---|---:|---:|---:|---|---|---|']
    for row in report['results']:
        if row.get('error'):
            lines.append(f"| {cell(row['audio_path'])} | {row['locale']} | 실패 | | | | | {cell(row['error'])} | |")
            continue
        result = row['result']
        lines.append('| ' + ' | '.join(map(cell, [row['audio_path'], row['locale'], result['language_mode'],
                     f"{result['audio_duration_s']:.3f}", f"{result['elapsed_ms']:.1f}",
                     f"{result['elapsed_ms']/1000/result['audio_duration_s']:.3f}",
                     f"{result['lang_detected']} / {result['lang_prob']}", result['text_raw'], result['text']])) + ' |')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refs', action='store_true')
    parser.add_argument('--locales', default='en,zh-CN,zh-TW,ja,yue')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--wav', action='append', default=[], metavar='LOCALE=PATH')
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--output', type=Path, default=Path('reports/stt/bench'))
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    items = reference_items(args.locales.split(',')) if args.refs else []
    if args.manifest:
        data = json.loads(args.manifest.read_text())
        items += data if isinstance(data, list) else data['items']
    for wav in args.wav:
        locale, path = wav.split('=', 1)
        items.append(dict(locale=locale, audio_path=path, source='explicit WAV'))
    if not items:
        parser.error('Choose --refs, --manifest, or --wav LOCALE=PATH')
    engine = MLXWhisperSTT()
    report = dict(created_at=datetime.now(timezone.utc).isoformat(),
                  model_path=str(engine.model_path), results=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    failed = False
    for item in items:
        # Repeats of one sample share state; unrelated samples do not.
        state = LanguageState(item['locale'])
        for repeat in range(args.repeats):
            if shutil.disk_usage(ROOT).free < 5_000_000_000:
                raise RuntimeError('Disk free space below 5 GB: stop and escalate')
            row = dict(item, repeat=repeat + 1)
            try:
                result = engine.transcribe_sync(ROOT / item['audio_path'], item['locale'],
                                                auto=args.auto, state=state)
                row['result'] = result.to_dict()
                if not result.text:
                    row['error'] = 'Empty transcription'
                    failed = True
                print(f"{item['locale']} {result.elapsed_ms:.1f} ms: {result.text}", flush=True)
            except Exception as error:
                row['error'] = f'{type(error).__name__}: {error}'
                failed = True
                print(row['error'], file=sys.stderr, flush=True)
            report['results'].append(row)
            args.output.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            args.output.with_suffix('.md').write_text(markdown(report))
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
