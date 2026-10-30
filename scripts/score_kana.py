#!/usr/bin/env python3
"""Add Japanese kana CER using the already-installed MLX engine G2P environment."""
import argparse
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pyopenjtalk
from server.blind.metrics import distance
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--input',type=Path,required=True)
a=p.parse_args();data=json.loads(a.input.read_text());count=0
for row in data['rows']:
    if row['locale']!='ja' or 'error_rate' not in row.get('whisper',{}):continue
    score=row['whisper']
    try:
        ref=re.sub(r'\W','',pyopenjtalk.g2p(score['reference_canonical'],kana=True))
        hyp=re.sub(r'\W','',pyopenjtalk.g2p(score['hypothesis_canonical'],kana=True))
        score['kana_cer']=distance(ref,hyp)/max(1,len(ref))
        score['reference_kana']=ref;score['hypothesis_kana']=hyp
        score.pop('kana_failure',None);count+=1
    except Exception as exc:score['kana_failure']=str(exc)
temporary=a.input.with_suffix(a.input.suffix+'.tmp')
temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
temporary.replace(a.input)
print(f'Kana scored {count} Japanese WAVs')
