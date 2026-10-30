#!/usr/bin/env python3
"""Read-only blind HTTP acceptance probe (creates a session, never casts a vote)."""
import argparse
import http.cookiejar
import io
import json
from pathlib import Path
import re
import urllib.error
import urllib.request
import wave

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--url',default='http://127.0.0.1:8000')
p.add_argument('--output',type=Path,default=Path('reports/blind-http-acceptance.json'))
a=p.parse_args()
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
records=[]
forbidden=['voxcpm','cosyvoice','qwen3','kokoro','mlx-community','fun-audiollm']
def get(path,body=None):
    request=urllib.request.Request(a.url+path,data=None if body is None else json.dumps(body).encode(),
                                   headers={'Content-Type':'application/json'} if body else {})
    with opener.open(request) as response:
        data=response.read();headers=dict(response.headers)
        exposed=(path+json.dumps(headers)+(data.decode() if 'audio/' not in headers.get('content-type',headers.get('Content-Type','')) else '')).lower()
        assert not any(name in exposed for name in forbidden),(path,'engine identifier leak')
        records.append({'path':path,'status':response.status,'headers':{k:('<redacted>' if k.lower()=='set-cookie' else v) for k,v in headers.items()},'bytes':len(data)})
        return data
assets=set()
for page in ('/blind','/blind/results'):
    html=get(page).decode()
    assets.update(re.findall(r'(?:src|href)="(/blind/assets/[^\"]+)"',html))
for asset in assets:get(asset)
get('/api/blind/session',{'alias':'__http_probe__','native':'ko','device':'headphones'})
pair=json.loads(get('/api/blind/next?locale=en'))
assert pair is not None,'No playable English pair'
assert set(pair)=={'id','locale','text','a','b'}
for side in ('a','b'):
    assert set(pair[side])=={'url','duration_ms'}
    data=get(pair[side]['url'])
    with wave.open(io.BytesIO(data),'rb') as wav:
        assert (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())==(24000,1,2)
    assert data[36:40]==b'data','Unexpected WAV metadata chunks'
    headers={k.lower():v for k,v in records[-1]['headers'].items()}
    assert not {'content-disposition','etag','last-modified'} & headers.keys()
get('/api/blind/results');get('/api/blind/results?format=csv')
for path in ('/data/blind/manifest.json','/api/blind/manifest','/blind/assets/manifest.json','/api/blind/audio/unknown'):
    try:get(path)
    except urllib.error.HTTPError as exc:assert exc.code==404
    else:raise AssertionError('Private/missing route exposed: '+path)
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps({'passed':True,'requests_checked':len(records),'engine_identifier_leaks':0,
                                'wav_format':'24000 Hz mono PCM16, 44-byte metadata-free header',
                                'records':records},indent=2))
print(f'PASS: {len(records)} responses; no engine names, private routes or identifying audio headers')
