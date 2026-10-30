#!/usr/bin/env python3
"""Finish loudness alignment and validate the manifest after all TTS processes exit."""
import argparse
import hashlib
import json
from pathlib import Path
import wave
import numpy as np
from gen_blind_set import align_loudness
from tts_common import ROOT,atomic_json
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--manifest',type=Path,default=ROOT/'data/blind/manifest.json')
a=p.parse_args()
if any(f.stat().st_size for f in (a.manifest.parent/'results').glob('*.jsonl')):
    p.error('Cannot modify a dataset with existing human votes')
data=json.loads(a.manifest.read_text());checked=[]
for item in data['items']:
    item.setdefault('stream',False)
    if item['status']!='ok':continue
    path=ROOT/item['file']
    if 'post_gain_db' not in item:
        item.update(align_loudness(path))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
    with wave.open(str(path),'rb') as wav:
        assert (wav.getnchannels(),wav.getframerate(),wav.getsampwidth())==(1,24000,2)
        audio=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
        assert abs(len(audio)/24000-item['duration_s'])<1/24000
    assert path.read_bytes()[36:40]==b'data'
    assert np.all(audio[:3600]==0) and np.all(audio[-3600:]==0)
    assert np.max(np.abs(audio.astype(np.float64)))<32767
    assert -21 <= item['lufs'] <= -19
    checked.append(item)
atomic_json(a.manifest,data)
report={'checked':len(checked),'failed':sum(i['status']=='failed' for i in data['items']),
        'lufs_min':min(i['lufs'] for i in checked),'lufs_max':max(i['lufs'] for i in checked),
        'true_peak_max_db':max(i['true_peak_db'] for i in checked),
        'sample_rate':24000,'channels':1,'pcm_bits':16,'edge_silence_ms':150,
        'sha256_all_match':True,'metadata_free_wav_headers':True,
        'source_sha256':data['normalization_sha256']}
atomic_json(ROOT/'reports/blind-audio-validation.json',report)
print(json.dumps(report,ensure_ascii=False))
