#!/usr/bin/env python3
"""Sequential warmed TTS benchmark, then (separate invocation) offline Whisper scoring."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import traceback
from tts_common import (ROOT, PORTS, LOCALES, atomic_json, corpus, engine_server, guard,
                        request, source_hash, supported, synthesize, write_wav, monitor_local_model)
from server.normalize.pipeline import variants


def benchmark(args):
    output = args.output
    result = json.loads(output.read_text()) if args.resume and output.exists() else {
        'created_at': datetime.now().isoformat(), 'repeats': args.repeats, 'rows': [], 'engines': {},
        'normalization_sha256': source_hash(), 'notes': [
            'One warmup per engine; locales may include first-use G2P cost.',
            'RTF = synthesis seconds / audio seconds; stream wall clock includes local transport.',
            'Peak memory = engine max(RSS, allocator); overlapping counters are not summed.',
            'Qwen3-ASR optional: not installed; no automatic download.',
            'Taiwan accent reference unavailable; zh-TW shares mainland reference.',
        ]}
    existing = {(r['engine'], r['sentence_id'], r['normalize'], r['repeat']) for r in result['rows']}
    result.setdefault('resource_starts', []).append(guard())
    sentences = corpus(args.locales)
    for engine in args.engines:
        result.setdefault('sampling_policy', {})[engine] = {'levels': args.levels, 'repeats': args.repeats}
        atomic_json(output, result)
        jobs = [(s, level, repeat) for s in sentences if supported(engine, s['locale'])
                for level in args.levels for repeat in range(args.repeats)
                if (engine, s['id'], level, repeat) not in existing]
        if not jobs:
            continue
        try:
            with engine_server(engine, ROOT / f'reports/bench-server-{engine}') as base:
                request(base, '/v1/warmup', {})
                info = request(base, '/v1/info')
                result['engines'][engine] = info
                for s, level, repeat in jobs:
                    row = {'engine': engine, 'locale': s['locale'], 'sentence_id': s['id'],
                           'normalize': level, 'repeat': repeat, 'seed': 42 + int(s['id'].rsplit('-', 1)[1]),
                           **variants(s['text'], s['locale'])}
                    try:
                        row['resources_before'] = guard()
                        row['normalization_sha256'] = source_hash()
                        pcm, metrics = synthesize(base, engine, s, row[f'tts_text_{level}'], row['seed'], info['supports_streaming'])
                        row.update(metrics)
                        row['info'] = request(base, '/v1/info')
                        # Retain first repeat only for STT, but measure all three independently.
                        if repeat == 0:
                            path = ROOT / f'reports/bench-audio/{output.stem}/{engine}/{s["id"]}-{level}.wav'
                            write_wav(path, pcm)
                            row['file'] = str(path.relative_to(ROOT))
                    except Exception as exc:
                        row['failure'] = str(exc)
                    result['rows'].append(row)
                    atomic_json(output, result)
                    print(json.dumps({k: row.get(k) for k in ('engine','sentence_id','normalize','repeat','rtf','failure')}, ensure_ascii=False), flush=True)
                    guard()  # Abort this engine promptly if the resource condition changes.
        except Exception as exc:
            reason = str(exc)
            result['engines'][engine] = {**result['engines'].get(engine, {}), 'failure': reason}
            completed = {(r['engine'],r['sentence_id'],r['normalize'],r['repeat']) for r in result['rows']}
            for s, level, repeat in jobs:
                if (engine,s['id'],level,repeat) not in completed:
                    result['rows'].append({'engine':engine,'locale':s['locale'],'sentence_id':s['id'],
                                          'normalize':level,'repeat':repeat,'failure':reason, **variants(s['text'],s['locale'])})
            print(f'{engine}: {reason}', flush=True)
        atomic_json(output, result)
    return result


def score(args):
    guard()
    from server.stt.mlx_whisper_stt import MLXWhisperSTT
    from server.blind.metrics import pronunciation_metrics
    result = json.loads(args.output.read_text())
    stt = MLXWhisperSTT()
    for row in result['rows']:
        if 'file' not in row or row.get('failure') or ('whisper' in row and args.resume):
            continue
        guard()
        try:
            transcript = stt.transcribe_sync(ROOT / row['file'], row['locale'])
            row['whisper'] = {'text': transcript.text, 'text_raw': transcript.text_raw, 'elapsed_ms': transcript.elapsed_ms,
                              'language': transcript.language_used, 'prompt_source': 'data/lexicon.yaml',
                              **pronunciation_metrics(row['display_text'], transcript.text_raw, row['locale'])}
        except Exception as exc:
            row['whisper'] = {'failure': str(exc)}
        atomic_json(args.output, result)
        print(row['sentence_id'], row['engine'], row['normalize'], row['whisper'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engines', default='all')
    parser.add_argument('--locales', default='all')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--levels', default='raw,full')
    parser.add_argument('--output', type=Path, default=ROOT / ('reports/bench-' + datetime.now().strftime('%Y%m%d') + '.json'))
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--score', action='store_true', help='Only STT; all TTS processes must already be stopped')
    args = parser.parse_args()
    args.levels = args.levels.split(',')
    if set(args.levels) - {'raw','full'}:
        parser.error('Levels must be raw,full or a subset')
    args.engines = list(PORTS) if args.engines == 'all' else args.engines.split(',')
    args.locales = LOCALES if args.locales == 'all' else args.locales.split(',')
    if set(args.engines) - PORTS.keys() or set(args.locales) - set(LOCALES) or args.repeats < 1:
        parser.error('Invalid engine, locale or repeat count')
    if args.score:
        with monitor_local_model():
            score(args)
    else:
        benchmark(args)

if __name__ == '__main__':
    main()
