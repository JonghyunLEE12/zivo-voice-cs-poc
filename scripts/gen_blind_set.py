#!/usr/bin/env python3
"""Generate the full-normalized blind set with exactly one engine loaded at a time."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
import wave
import numpy as np
from tts_common import (ROOT, PORTS, LOCALES, atomic_json, corpus, engine_server, guard,
                        request, source_hash, supported, synthesize, write_wav)
from server.normalize.pipeline import variants


def prepare_audio(pcm, path):
    """Two-pass -20 LUFS, fixed 24k PCM, no metadata, 150ms edge silence."""
    raw = path.with_suffix('.input.wav')
    temporary = path.with_suffix('.normalized.wav')
    write_wav(raw, pcm)
    try:
        first = subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(raw),'-af',
                                'loudnorm=I=-20:TP=-2:LRA=11:print_format=json','-f','null','-'],
                               capture_output=True, text=True, check=True)
        stats = json.JSONDecoder().raw_decode(first.stderr[first.stderr.rfind('{'):])[0]
        filt = ('loudnorm=I=-20:TP=-2:LRA=11:linear=true:' +
                ':'.join(f'{k}={stats[v]}' for k,v in [('measured_I','input_i'),('measured_TP','input_tp'),
                         ('measured_LRA','input_lra'),('measured_thresh','input_thresh'),('offset','target_offset')]))
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(raw),'-af',filt,'-ar','24000','-ac','1',
                        '-map_metadata','-1','-c:a','pcm_s16le',str(temporary)],capture_output=True,check=True)
        with wave.open(str(temporary),'rb') as wav:
            audio = np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
        active = np.flatnonzero(np.abs(audio.astype(np.float64)) > max(1, np.max(np.abs(audio.astype(np.float64))) * .01))
        if not len(active):
            raise ValueError('Silent after loudness normalization')
        audio = np.pad(audio[active[0]:active[-1]+1], (3600,3600))
        write_wav(path, audio.astype('<i2').tobytes())
        # Padding/trimming slightly changes integrated loudness: retain measured value.
        verify = subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(path),'-af',
                                 'loudnorm=I=-20:print_format=json','-f','null','-'],capture_output=True,text=True,check=True)
        measured = json.JSONDecoder().raw_decode(verify.stderr[verify.stderr.rfind('{'):])[0]
        return {'duration_s':len(audio)/24000, 'edge_silence_ms':150, **align_loudness(path, measured)}
    finally:
        raw.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def loudness_measure(path):
    result = subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(path),'-af',
                             'loudnorm=I=-20:TP=-2:print_format=json','-f','null','-'],
                            capture_output=True,text=True,check=True)
    return json.JSONDecoder().raw_decode(result.stderr[result.stderr.rfind('{'):])[0]


def align_loudness(path, measured=None):
    """Correct post-trim integrated loudness with common gain, retaining -1 dBTP headroom."""
    measured = measured or loudness_measure(path)
    before = float(measured['input_i'])
    true_peak = float(measured['input_tp'])
    requested = -20 - before
    gain_db = min(requested, -1 - true_peak)
    if abs(gain_db) > .04:
        with wave.open(str(path),'rb') as wav:
            audio = np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(np.float64)
        scaled = np.rint(audio * 10**(gain_db/20)).clip(-32768,32767).astype('<i2')
        write_wav(path,scaled.tobytes())
        measured = loudness_measure(path)
    else:
        gain_db = 0.0
    return {'lufs':float(measured['input_i']), 'true_peak_db':float(measured['input_tp']),
            'lufs_before_gain':before, 'post_gain_db':gain_db, 'gain_limited':gain_db < requested-.05,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engines', default='all')
    parser.add_argument('--locales', default='en,zh-CN,zh-TW,ja,yue')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--manifest',type=Path,default=ROOT/'data/blind/manifest.json')
    args = parser.parse_args()
    engines = list(PORTS) if args.engines == 'all' else args.engines.split(',')
    locales = args.locales.split(',')
    if set(engines) - PORTS.keys() or set(locales) - set(LOCALES):
        parser.error('Invalid engine or locale')
    if not args.resume and any(p.stat().st_size for p in (args.manifest.parent/'results').glob('*.jsonl')):
        parser.error('Existing human votes: archive manifest/audio/results together before generating a new set')
    data = json.loads(args.manifest.read_text()) if args.resume and args.manifest.exists() else {
        'created_at':datetime.now().isoformat(),'normalization_sha256':source_hash(),'items':[]}
    if args.resume and data['normalization_sha256'] != source_hash():
        parser.error('Normalization changed; generate a fresh set without --resume')
    for engine in engines:
        done = {(i['engine'],i['sentence_id']) for i in data['items']}
        sentences = [s for s in corpus(locales) if supported(engine,s['locale']) and (engine,s['id']) not in done]
        if not sentences:
            continue
        try:
            with engine_server(engine, ROOT/f'reports/blind-server-{engine}') as base:
                request(base,'/v1/warmup',{})
                for s in sentences:
                    item = {'item_id':uuid.uuid4().hex,'engine':engine,'locale':s['locale'],'sentence_id':s['id'],
                            'normalize':'full','seed':42+int(s['id'].rsplit('-',1)[1]),'file':None,'duration_s':None,'sha256':None,**variants(s['text'],s['locale'])}
                    try:
                        pcm, metrics = synthesize(base,engine,s,item['tts_text_full'],item['seed'])
                        if metrics['failure']:
                            raise ValueError(metrics['failure'])
                        path = ROOT/f'data/blind/audio/{item["item_id"]}.wav'
                        item.update(prepare_audio(pcm,path))
                        item.update(file=str(path.relative_to(ROOT)),status='ok')
                    except Exception as exc:
                        item.update(status='failed',failure=str(exc))
                    data['items'].append(item)
                    atomic_json(args.manifest,data)
                    print(json.dumps(item,ensure_ascii=False),flush=True)
                    guard()
        except Exception as exc:
            done = {(i['engine'],i['sentence_id']) for i in data['items']}
            for s in sentences:
                if (engine,s['id']) not in done:
                    data['items'].append({'item_id':uuid.uuid4().hex,'engine':engine,'locale':s['locale'],
                        'sentence_id':s['id'],'normalize':'full','seed':42+int(s['id'].rsplit('-',1)[1]),'file':None,'duration_s':None,'sha256':None,
                        **variants(s['text'],s['locale']),'status':'failed','failure':str(exc)})
            atomic_json(args.manifest,data)
    print(f'Manifest: {len(data["items"])} items; {sum(i["status"]=="ok" for i in data["items"])} playable')

if __name__ == '__main__':
    main()
