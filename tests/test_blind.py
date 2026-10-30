import json
from pathlib import Path
import sys
import wave
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.blind.aggregate import aggregate, sign_p
from server.blind.api import create_router
from server.blind.store import BlindStore
from server.blind.metrics import pronunciation_metrics, canonical

ENGINES = ['voxcpm2','cosyvoice3','qwen3tts','kokoro']

@pytest.fixture
def setup(tmp_path):
    items=[]
    for number in range(1,4):
        for engine in ENGINES:
            path=tmp_path/'audio'/f'{engine}-{number}.wav'
            path.parent.mkdir(exist_ok=True)
            with wave.open(str(path),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\x01\x00'*2400)
            items.append({'item_id':f'{engine}-{number}','engine':engine,'locale':'en','sentence_id':f'en-{number}',
                          'normalize':'full','status':'ok','display_text':'Hello there.','duration_s':.1,'file':str(path)})
    (tmp_path/'manifest.json').write_text(json.dumps({'items':items}))
    now=[0.0]
    store=BlindStore(tmp_path,clock=lambda:now[0])
    app=FastAPI();app.include_router(create_router(store))
    client=TestClient(app)
    client.post('/api/blind/session',json={'alias':'../rater','native':'zh-TW','device':'headphones'})
    return store,client,now


def cast(client,now,choice='A'):
    pair=client.get('/api/blind/next?locale=en').json()
    for side in ('a','b'):
        response=client.get(pair[side]['url'])
        assert response.status_code==200
        assert 'filename' not in str(response.headers)
        assert not {'etag','last-modified','x-engine'} & set(response.headers)
    now[0]+=1
    payload={'assignment_id':pair['id'],'choice':choice,'listened_ms_a':100,'listened_ms_b':100,
             'pron_issue_a':True,'pron_issue_b':False,'comment':'test'}
    response=client.post('/api/blind/vote',json=payload)
    assert response.status_code==200,response.text
    return pair,payload


def test_ten_saved_votes_and_public_no_identity(setup):
    store,client,now=setup
    seen=set()
    for i in range(10):
        pair,payload=cast(client,now,['A','same','B'][i%3])
        assert pair['id'] not in seen;seen.add(pair['id'])
        assert not any(e in json.dumps(pair) for e in ENGINES)
        assert client.post('/api/blind/vote',json=payload).status_code==400
        assert client.get(pair['a']['url']).status_code==404
    saved=store.votes()
    assert len(saved)==10
    assert len({v['pair_key'] for v in saved})==10
    assert len(list((store.root/'results').glob('*.jsonl')))==1
    actual=client.get('/api/blind/results').json()
    assert actual==aggregate(saved,labels=store.labels)
    assert actual['n']==10
    for endpoint in ('/api/blind/results','/api/blind/results?format=csv'):
        response=client.get(endpoint)
        assert not any(e in response.text for e in ENGINES)
    assert client.get('/api/blind/results?native=ko').json()['n']==0
    assert client.get('/api/blind/results?native=zh-TW').json()['taiwan_accent_evidence']


def test_assignment_ownership_and_listen_gate(setup):
    store,client,now=setup
    pair=client.get('/api/blind/next?locale=en').json()
    assert client.get('/api/blind/next?locale=en').json()==pair
    data={'assignment_id':pair['id'],'choice':'A','listened_ms_a':100,'listened_ms_b':100}
    assert client.post('/api/blind/vote',json=data).status_code==400
    stranger=TestClient(client.app)
    stranger.post('/api/blind/session',json={'alias':'other','native':'en','device':'speakers'})
    assert stranger.get(pair['a']['url']).status_code==404
    assert stranger.post('/api/blind/vote',json=data).status_code==400
    assert client.get('/api/blind/audio/../../manifest.json').status_code==404


def test_balance_and_repeat_identity(setup):
    store,client,now=setup
    first,_=cast(client,now)
    original=store.votes()[0]['pair_key']
    client.post('/api/blind/session',json={'alias':'second','native':'en','device':'speakers'})
    pair=client.get('/api/blind/next?locale=en').json()
    assert store.assignments[pair['id']]['pair_key']!=original
    client.post('/api/blind/session',json={'alias':'../RATER','native':'en','device':'speakers'})
    pair=client.get('/api/blind/next?locale=en').json()
    assert store.assignments[pair['id']]['pair_key']!=original


def test_orientation_and_exact_sign_test():
    votes=[]
    for i,choice in enumerate(['A']*6+['B']*2+['same']*2):
        a,b=('x','y') if i%2==0 else ('y','x')
        # Six x wins, two y wins, two ties, regardless of randomized orientation.
        oriented=choice if a=='x' or choice=='same' else {'A':'B','B':'A'}[choice]
        votes.append({'locale':'en','rater_native':'en','device':'headphones','a':{'engine':a},'b':{'engine':b},
                      'choice':oriented,'pron_issue_a':i<3,'pron_issue_b':False})
    result=aggregate(votes)
    p=result['pairs'][0]
    assert (p['x_wins'],p['same'],p['y_wins'],p['n'])==(6,2,2,10)
    assert (p['x_pct'],p['same_pct'],p['y_pct'])==(60,20,20)
    assert p['win_rate_excluding_ties']==.75
    assert p['sign_p_two_sided']==.2890625
    assert sign_p(0,0)==1 and sign_p(8,0)==.0078125


def test_metrics_numbers_and_pronunciation():
    assert canonical('fifty percent','en')==canonical('50%','en')
    assert canonical('two hundred eighty-six thousand won','en')==canonical('₩286,000','en')
    assert canonical('七萬八千韓元','zh-TW')==canonical('78,000韩元','zh-CN')
    assert pronunciation_metrics('hello world','hello','en')['error_rate']==.5
    assert pronunciation_metrics('明洞站','明洞站','zh-CN')['error_rate']==0
    assert pronunciation_metrics('Myeongdong Station','London station','en')['name_hits']==0


def test_inverse_numeric_dates_and_clocks():
    assert canonical('November thirty-first','en')==canonical('November 31st','en')
    assert canonical('晚上七点半','zh-CN')==canonical('晚上19:30','zh-CN')
    assert canonical('百分之五十','zh-TW')==canonical('50%','zh-TW')


def test_japanese_numeric_unit_inverse():
    reference='合計130,000ウォンです。'
    assert canonical(reference,'ja',reference)==canonical('合計13万ウォンです。','ja',reference)


def test_metric_inverse_spoken_code_prefixes():
    assert canonical('ZV-2610','en')==canonical('Zee Vee two six one zero','en')
    assert canonical('ZV-2610','ja','ZV-2610')==canonical('ぜっとぶい に ろく いち ゼロ','ja','ZV-2610')
