import asyncio
from datetime import datetime
import json
import re
import httpx
import pytest
from server.llm.fake_db import FakeDB, SEOUL
from server.llm.tools import ToolSession, TOOLS
from server.llm.ollama_client import OllamaClient, VoiceAgent, response_metrics, convert_script

@pytest.mark.parametrize('code,surname,locale,name',[
('ZV-4821',' carter ','en','Hanbit Stay Myeongdong'),
('ZV-7305','WANG','zh-CN','乙支路灯笼酒店'),
('ZV-7305','王','zh-CN','乙支路灯笼酒店'),
('ZV-5196','陈','zh-TW','漢江月光套房飯店'),
('ZV-5196','Chen','zh-TW','漢江月光套房飯店'),
('ZV-2610','佐藤','ja','景福宮韓服体験ツアー'),
('ZV-2610','sato','ja','景福宮韓服体験ツアー'),
('ZV-9034','Chan','yue','明洞韓光酒店'),
])
def test_lookup_aliases(code,surname,locale,name):
    result=FakeDB().lookup(code,surname,locale)
    assert result['found'] and result['name']==name
    assert 'last_name_aliases' not in result

@pytest.mark.parametrize('code,refund,fee',[
('ZV-4821',143000,50),('ZV-7305',78000,0),('ZV-5196',412000,0),('ZV-2610',130000,0),('ZV-9034',300000,0),
])
def test_demo_policy(code,refund,fee):
    result=FakeDB().cancellation_policy(code,'en')
    assert result['refund_krw']==refund and result['fee_percent']==fee
    assert not result['cancellation_executed']

@pytest.mark.parametrize('code,now,fee',[
('ZV-4821','2026-10-31T23:59:59+09:00',0),
('ZV-4821','2026-11-01T00:00:00+09:00',50),
('ZV-4821','2026-11-02T23:59:59+09:00',50),
('ZV-4821','2026-11-03T00:00:00+09:00',100),
('ZV-7305','2026-11-06T19:30:00+09:00',0),
('ZV-7305','2026-11-06T19:30:01+09:00',50),
('ZV-2610','2026-11-03T23:59:59+09:00',0),
('ZV-2610','2026-11-04T00:00:00+09:00',100),
('ZV-4821','2026-10-31T15:00:00+00:00',50),
])
def test_policy_boundaries(code,now,fee):
    assert FakeDB().cancellation_policy(code,'en',datetime.fromisoformat(now))['fee_percent']==fee


def test_tools_authorization_and_schema():
    assert len(TOOLS)==3
    s=ToolSession('en')
    assert s.execute('get_cancellation_policy',{'reservation_code':'ZV-4821'})['error']=='lookup_required'
    assert s.execute('lookup_reservation',{'reservation_code':'ZV-4821','last_name':'Nope'})['found'] is False
    assert s.execute('lookup_reservation',{'reservation_code':'Z V four eight two one','last_name':'Carter'})['found']
    assert s.execute('get_cancellation_policy',{'reservation_code':'ZV-4821'})['refund_krw']==143000
    assert s.execute('lookup_reservation',{'reservation_code':'ZV-4821','last_name':'Nope'})['found'] is False
    assert s.execute('get_cancellation_policy',{'reservation_code':'ZV-4821'})['error']=='lookup_required'
    assert s.execute('lookup_reservation',{'reservation_code':'ZV-4821','last_name':'Nope'})['transfer_required']
    assert s.execute('cancel_reservation',{})['error']=='unknown_tool'
    assert s.execute('lookup_reservation',{'reservation_code':4821,'last_name':'Carter'})['error']=='invalid_arguments'
    assert s.execute('transfer_to_human',{'reason':'cancel','summary_ko':'상담 요청'})['error']=='invalid_reason'
    assert s.execute('transfer_to_human',{'reason':'user_request','summary_ko':'상담 요청'})=={'queued':True,'estimated_wait_min':3}
    assert s.execute('lookup_reservation',{'reservation_code':'ZV-4821','last_name':'Carter'})['error']=='session_transferred'


def test_no_booking_data_on_surname_mismatch():
    db=FakeDB()
    assert db.lookup('ZV-4821','Wrong','en')==db.lookup('ZV-9999','Wrong','en')


def test_locale_and_length_metrics():
    assert convert_script('我帮您查询预订，酒店提供出租车。','zh-TW')=='我幫您查詢預訂，酒店提供計程車。'
    assert convert_script('飯店預訂','zh-CN')=='饭店预订'
    assert response_metrics('a',' '.join(['word']*36),'en')['length_violation']
    assert response_metrics('我帮您','我幫您','zh-TW')['script_changed_chars']==1
    assert response_metrics('a','あ'*81,'ja')['length_violation']


def mock_stream(chunks):
    return httpx.Response(200,content='\n'.join(json.dumps(c,ensure_ascii=False) for c in chunks)+'\n')


def test_streaming_tool_roundtrip(tmp_path):
    requests=[]
    def handler(request):
        payload=json.loads(request.content); requests.append(payload)
        assert payload['think'] is False and payload['stream'] is True and payload['keep_alive']=='30m'
        if len(requests)==1:
            return mock_stream([
                {'message':{'tool_calls':[{'function':{'index':0,'name':'lookup_reservation','arguments':'{"reservation_code":"ZV-4821",'}}]}},
                {'message':{'tool_calls':[{'function':{'index':0,'arguments':'"last_name":"Carter"}'}}]}},
                {'done':True},
            ])
        assert payload['messages'][-1]['role']=='tool'
        assert json.loads(payload['messages'][-1]['content'])['found']
        return mock_stream([{'message':{'content':'Your booking '}},{'message':{'content':'is confirmed.'}},{'done':True}])
    client=OllamaClient(transport=httpx.MockTransport(handler))
    agent=VoiceAgent('en',client=client,log_path=tmp_path/'log.jsonl')
    result=asyncio.run(agent.turn('Find ZV-4821 Carter'))
    assert result['text']=='Your booking is confirmed.'
    assert result['tools'][0]['result']['found']
    assert len(requests)==2 and result['metrics']['thinking_chars']==0
    assert json.loads((tmp_path/'log.jsonl').read_text())['think'] is False


def test_chinese_conversion_across_chunks(tmp_path):
    def handler(request):
        return mock_stream([{'message':{'content':part}} for part in ['我帮','您叫出','租车','。']]+[{'done':True}])
    agent=VoiceAgent('zh-TW',client=OllamaClient(transport=httpx.MockTransport(handler)),log_path=tmp_path/'log.jsonl')
    result=asyncio.run(agent.turn('help'))
    assert result['text']=='我幫您叫計程車。'
    assert result['metrics']['script_changed_chars']>0


def test_two_failed_lookups_enforce_handoff(tmp_path):
    count=0
    def handler(request):
        nonlocal count
        count+=1
        if count<=2:
            return mock_stream([{'message':{'tool_calls':[{'function':{'name':'lookup_reservation','arguments':{'reservation_code':'ZV-4821','last_name':'Wrong'}}}]}},{'done':True}])
        assert json.loads(request.content)['tools']==TOOLS
        return mock_stream([{'message':{'content':'I will connect you with an agent.'}},{'done':True}])
    agent=VoiceAgent('en',client=OllamaClient(transport=httpx.MockTransport(handler)),log_path=tmp_path/'log.jsonl')
    result=asyncio.run(agent.turn('lookup'))
    assert result['transferred']
    assert result['tools'][-1]['server_enforced']
    with pytest.raises(RuntimeError,match='handoff'): asyncio.run(agent.turn('again'))


def test_incomplete_stream_is_error():
    async def run():
        c=OllamaClient(transport=httpx.MockTransport(lambda r: mock_stream([{'message':{'content':'hello'}}])))
        return [chunk async for chunk in c.stream_chat([],[])]
    with pytest.raises(RuntimeError,match='without a done'): asyncio.run(run())


def test_remote_endpoint_rejected():
    with pytest.raises(ValueError): OllamaClient('https://example.com')


def test_http_stream_cancellation_closes_connection():
    async def run():
        closed=asyncio.Event()
        waiting=asyncio.Event()
        class BlockedStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"message":{"content":"hello"}}\n'
                waiting.set()
                await asyncio.Event().wait()
            async def aclose(self):
                closed.set()
        async def handler(request):
            return httpx.Response(200,stream=BlockedStream())
        client=OllamaClient(transport=httpx.MockTransport(handler))
        async def consume():
            async for _ in client.stream_chat([],[]): pass
        task=asyncio.create_task(consume())
        await asyncio.wait_for(waiting.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert closed.is_set()
    asyncio.run(run())

@pytest.mark.parametrize('locale,code,refund', [('en','ZV-5196',412000),('zh-CN','ZV-7305',78000),('zh-TW','ZV-5196',412000),('ja','ZV-2610',130000)])
def test_policy_spoken_summary_keeps_exact_amounts(locale,code,refund):
    from scripts.test_llm_scenarios import validate
    db=FakeDB()
    policy=db.cancellation_policy(code,locale)
    result={'text':policy['spoken_summary'],'raw_text':policy['spoken_summary'],'tools':[
        {'name':'lookup_reservation','result':{'found':True,'reservation_code':code}},
        {'name':'get_cancellation_policy','result':policy}],
        'metrics':{'thinking_chars':0}}
    assert validate(result,locale,'cancellation')==[]
    assert f'{refund:,}' in policy['spoken_summary']


def test_overlong_response_is_logged_without_truncation(tmp_path):
    text=' '.join(['word']*40)+'.'
    client=OllamaClient(transport=httpx.MockTransport(lambda r: mock_stream([{'message':{'content':text}},{'done':True}])))
    agent=VoiceAgent('en',client=client,log_path=tmp_path/'length.jsonl')
    result=asyncio.run(agent.turn('help'))
    assert result['text']==text and result['metrics']['length_violation']
    saved=json.loads((tmp_path/'length.jsonl').read_text())
    assert saved['text']==text and saved['metrics']['units']==40
