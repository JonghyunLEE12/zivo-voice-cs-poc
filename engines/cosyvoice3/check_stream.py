"""Real HTTP streaming, disconnect, warmup and follow-up checks."""
import json,time
from pathlib import Path
import httpx
BASE=Path(__file__).resolve().parent
URL='http://127.0.0.1:8102'
payload={'text':'Hello, this is ZIVO customer support. I can help you check your hotel reservation and answer questions about your booking today.','locale':'en','stream':True,'seed':42}
with httpx.Client(timeout=180) as c:
    start=time.perf_counter(); sizes=[]; first=None
    with c.stream('POST',URL+'/v1/synthesize',json=payload) as r:
        r.raise_for_status()
        for block in r.iter_raw():
            if first is None: first=(time.perf_counter()-start)*1000
            sizes.append(len(block))
    full={'chunks':len(sizes),'bytes':sum(sizes),'duration_s':sum(sizes)/48000,'ttfa_ms':first,'wall_ms':(time.perf_counter()-start)*1000}
    before=c.get(URL+'/v1/info').json()['cancelled_requests']
    with c.stream('POST',URL+'/v1/synthesize',json={**payload,'text':payload['text']*3}) as r:
        r.raise_for_status(); block=next(r.iter_raw())
    stopped=time.perf_counter()
    while True:
        info=c.get(URL+'/v1/info').json()
        if info['active_requests']==0 and info['cancelled_requests']>before: break
        if time.perf_counter()-stopped>30: raise AssertionError(info)
        time.sleep(.02)
    cancel={'first_bytes':len(block),'release_ms':(time.perf_counter()-stopped)*1000,'cancelled_requests':info['cancelled_requests']}
    warm=c.post(URL+'/v1/warmup');warm.raise_for_status();assert warm.json()['ok']
    follow=c.post(URL+'/v1/synthesize',json={'text':'Hello, how can I help you today?','locale':'en','seed':42,'stream':False})
    follow.raise_for_status()
    result={'stream':full,'disconnect':cancel,'warmup':warm.json(),'followup_bytes':len(follow.content),'followup_synth_ms':float(follow.headers['X-Synth-Ms'])}
    (BASE/'artifacts/stream-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
