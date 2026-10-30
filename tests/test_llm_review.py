import asyncio
from datetime import datetime
import json
import httpx
import pytest
from server.llm.fake_db import FakeDB
from server.llm.ollama_client import VoiceAgent, OllamaClient
from server.llm.prefetch import prefetch_calls
from server.llm.sentences import split_sentences
from tests.test_llm import mock_stream


def agent_for(tmp_path, handler, **kwargs):
    return VoiceAgent(client=OllamaClient(transport=httpx.MockTransport(handler)), log_path=tmp_path/'log.jsonl', **kwargs)


@pytest.mark.parametrize('failure', ['http','length','empty','round_limit','invalid_chunk'])
def test_failed_turn_rolls_back_history_and_tools(tmp_path,failure):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        if failure == 'round_limit' or count == 1:
            return mock_stream([{'message':{'tool_calls':[{'function':{'name':'lookup_reservation','arguments':{'reservation_code':'ZV-4821','last_name':'Carter'}}}]}},{'done':True}])
        if failure == 'http': return httpx.Response(500)
        if failure == 'length': return mock_stream([{'message':{'content':'Partial.'}},{'done':True,'done_reason':'length'}])
        if failure == 'invalid_chunk': return httpx.Response(200,content='not json\n')
        return mock_stream([{'done':True}])
    agent = agent_for(tmp_path,handler)
    before = list(agent.messages)
    with pytest.raises(Exception): asyncio.run(agent.turn('lookup'))
    assert agent.messages == before
    assert not agent.session.verified_codes
    assert agent.session.lookup_failures == 0 and not agent.session.transferred


def test_cancel_and_commit_only_played_sentence(tmp_path):
    async def run():
        agent = agent_for(tmp_path,lambda r: mock_stream([{'message':{'content':'One sentence. Another sentence.'}},{'done':True}]))
        stream = agent.stream_turn('hello')
        event = await anext(stream)
        assert event == {'type':'llm.delta','text':'One sentence.'}
        await stream.aclose()
        assert len(agent.messages)==1
        agent.commit_spoken('One sentence.')
        assert [m['role'] for m in agent.messages] == ['system','user','assistant']
        assert agent.messages[-1]['content'] == 'One sentence.'
        with pytest.raises(ValueError): agent.commit_spoken('invented')
        agent.commit_spoken('')
        assert len(agent.messages)==1
    asyncio.run(run())


def test_async_cancellation_rolls_back_and_closes(tmp_path):
    async def run():
        waiting, closed = asyncio.Event(), asyncio.Event()
        class Blocked(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"message":{"content":"Unverified sentence."}}\n'
                waiting.set()
                await asyncio.Event().wait()
            async def aclose(self): closed.set()
        async def handler(request): return httpx.Response(200,stream=Blocked())
        agent = agent_for(tmp_path,handler)
        task = asyncio.create_task(agent.turn('help'))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert closed.is_set() and len(agent.messages)==1
        agent.commit_spoken('')
    asyncio.run(run())


def test_successful_playback_reconciliation_preserves_complete_tools(tmp_path):
    agent = agent_for(tmp_path,lambda r: mock_stream([{'message':{'content':'Confirmed. More details.'}},{'done':True}]))
    asyncio.run(agent.turn('Find ZV-4821, surname Carter.'))
    agent.commit_spoken('Confirmed.')
    assert [m['role'] for m in agent.messages] == ['system','user','assistant','tool','assistant']
    assert agent.messages[-1]['content']=='Confirmed.'
    assert agent.session.verified_codes == {'ZV-4821'}


def test_late_tool_preamble_dropped_and_invalid_json_returned(tmp_path):
    requests = []
    def handler(request):
        payload = json.loads(request.content); requests.append(payload)
        if len(requests)==1:
            return mock_stream([{'message':{'content':'This must never reach TTS.'}},
                {'message':{'tool_calls':[{'function':{'name':'lookup_reservation','arguments':'{broken'}}]}},{'done':True}])
        assert json.loads(payload['messages'][-1]['content']) == {'error':'invalid_arguments'}
        return mock_stream([{'message':{'content':'Please repeat the code.'}},{'done':True,'eval_count':5,'eval_duration':2000,'prompt_eval_count':99,'prompt_eval_duration':1000,'load_duration':1}])
    agent = agent_for(tmp_path,handler)
    async def run(): return [e async for e in agent.stream_turn('hello')]
    events = asyncio.run(run())
    assert [e['text'] for e in events if e['type']=='llm.delta']==['Please repeat the code.']
    result = events[-1]
    assert result['raw_text']=='Please repeat the code.'
    assert result['discarded_preambles']==['This must never reach TTS.']
    assert result['metrics']['rounds'][-1]['prompt_eval_count']==99
    assert result['metrics']['first_sentence_ms']>=result['metrics']['first_token_ms']


def test_history_bounded_and_summary_authoritative(tmp_path):
    requests=[]
    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        return mock_stream([{'message':{'content':'Okay.'}},{'done':True}])
    agent=agent_for(tmp_path,handler,max_history_turns=2)
    asyncio.run(agent.turn('Find ZV-4821, surname Carter. If I cancel today?'))
    assert 'policy' not in json.loads(agent.messages[-2]['content'])
    for _ in range(5): asyncio.run(agent.turn('hello'))
    assert len(agent.messages)==5
    assert 'ZV-4821' in requests[-1]['messages'][0]['content']
    assert len(requests[-1]['messages'])==4
    assert all(p['tools']==requests[0]['tools'] for p in requests)


@pytest.mark.parametrize('prompt,expected',[
('Find ZV-4821, surname Carter.',1),
('Z V four eight two one, my last name is Carter. Cancel today?',2),
('Find ZV-4821 Carter',0),('Find ZV-4821, my surname is not Carter.',0),
('Find ZV-4821, surname Carter. Please make an exception.',0),
('Find ZV-4821, surname Carter, surname Smith.',0),
])
def test_prefetch_conservative(prompt,expected):
    assert len(prefetch_calls(prompt,'en'))==expected


def test_prefetch_one_round_and_failed_lookup_stops_policy(tmp_path):
    for surname,expected in [('Carter',2),('Smith',1)]:
        requests=[]
        def handler(request):
            requests.append(json.loads(request.content))
            return mock_stream([{'message':{'content':'Okay.'}},{'done':True}])
        agent=agent_for(tmp_path,handler)
        result=asyncio.run(agent.turn(f'Find ZV-4821, surname {surname}. Cancel today?'))
        assert len(requests)==1 and len(result['tools'])==expected
        assert all(t['source']=='server_prefetch' for t in result['tools'])


def test_warmup_and_unload_do_not_pull_model():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'done':True,'load_duration':12})
    async def run():
        c=OllamaClient(transport=httpx.MockTransport(handler))
        assert (await c.warmup())['load_duration']==12
        await c.unload()
    asyncio.run(run())
    assert all(r.url.path=='/api/chat' for r in requests)
    assert json.loads(requests[0].content)['options']['num_ctx']==8192
    assert json.loads(requests[-1].content)['keep_alive']==0


def test_sentence_boundaries():
    assert split_sentences('At 3.5 p.m. arrive. Confirm ZV-4821!') == ['At 3.5 p.m. arrive.',' Confirm ZV-4821!']
    assert split_sentences('確認しました。次です。')==['確認しました。','次です。']


@pytest.mark.parametrize('now,fee', [('2026-11-06T09:00:00+09:00',0),('2026-11-06T20:00:00+09:00',50)])
def test_configured_now_not_midnight(monkeypatch,now,fee):
    monkeypatch.setenv('DEMO_NOW',now)
    monkeypatch.delenv('DEMO_TODAY',raising=False)
    db=FakeDB()
    assert db.today.isoformat()=='2026-11-06'
    assert db.cancellation_policy('ZV-7305','en')['fee_percent']==fee


@pytest.mark.parametrize('now',['2026-11-07T19:30:00+09:00','2026-11-08T00:00:00+09:00'])
def test_started_service_is_not_cancellable(now):
    result=FakeDB(now=now).cancellation_policy('ZV-7305','en')
    assert result['cancellable'] is False and result['reason']=='service_started'
    assert 'fee_percent' not in result and 'refund_krw' not in result


def test_naive_or_conflicting_demo_now_rejected():
    with pytest.raises(ValueError): FakeDB(now='2026-11-06T20:00:00')
    with pytest.raises(ValueError): FakeDB('2026-11-01',now='2026-11-06T20:00:00+09:00')


def test_validator_rejects_normalized_script_false_positive():
    from scripts.test_llm_scenarios import validate_quality
    result={'raw_text':'我帮您查询。','text':'我幫您查詢。','metrics':{'thinking_chars':0}}
    assert 'raw Chinese script/lexicon required conversion' in validate_quality(result,'zh-TW')


def test_validator_detects_name_date_money_and_handoff_errors():
    from scripts.test_llm_scenarios import validate_lookup_reply, validate_mismatch_reply, validate_handoff
    booking=FakeDB().lookup('ZV-4821','Carter','en')
    assert len(validate_lookup_reply('You are at the Wrong Hotel on November 4th. ',booking,'en'))==3
    assert validate_mismatch_reply('Your hotel is Hanbit Stay Myeongdong.')
    assert validate_mismatch_reply('The cost is 286,000 won.')
    result={'text':'已为您转接人工客服。','transferred':True,'tools':[{'name':'transfer_to_human','args':{'reason':'user_request','summary_ko':'상담원 요청'},'result':{'queued':True}}]}
    assert len(validate_handoff(result,'zh-CN','user_request'))==2
    result['text']='您已进入排队队列，预计等待约3分钟。'
    assert not validate_handoff(result,'zh-CN','user_request')


def test_validator_rejects_swapped_fee_and_refund():
    from scripts.test_llm_scenarios import validate
    policy=FakeDB().cancellation_policy('ZV-5196','en')
    wrong='Today the cancellation fee is 0%, or ₩412,000, and the refund is ₩0.'
    result={'text':wrong,'raw_text':wrong,'tools':[
        {'name':'lookup_reservation','result':{'found':True,'reservation_code':'ZV-5196'}},
        {'name':'get_cancellation_policy','result':policy}], 'metrics':{'thinking_chars':0}}
    assert 'reply differs from exact fee/refund spoken_summary' in validate(result,'en','cancellation')


def test_validator_counts_length_as_failure():
    from scripts.test_llm_scenarios import validate_quality
    text=' '.join(['word']*40)+'.'
    assert 'response length exceeded' in validate_quality({'text':text,'raw_text':text,'metrics':{'thinking_chars':0}},'en')


def test_dialogue_reports_fallback_without_claiming_model_success():
    from scripts.test_llm_dialogue import validate_dialogue
    first={'text':'Please verify your code.','raw_text':'Please verify your code.','metrics':{'thinking_chars':0},'tools':[{'name':'lookup_reservation','result':{'found':False}}]}
    last={'text':'You are queued. The wait is about 3 minutes.','raw_text':'You are queued. The wait is about 3 minutes.','metrics':{'thinking_chars':0},'transferred':True,'tools':[
        {'name':'lookup_reservation','result':{'found':False}},
        {'name':'transfer_to_human','server_enforced':True,'args':{'reason':'lookup_failed','summary_ko':'조회 실패'},'result':{'queued':True}}]}
    errors,source=validate_dialogue('two_failures',[first,last],'en','lookup_failed')
    assert not errors and source=='server_enforced'


def test_resource_guard_coordinator_thresholds(monkeypatch,tmp_path):
    from scripts.llm_resources import ResourceGuard, ResourceLimit
    import scripts.llm_resources as resource
    from types import SimpleNamespace
    values={'pressure':1,'swap':22000,'disk':30,'time':0}
    def output(command,**kwargs):
        if command==['memory_pressure']: return 'System-wide memory free percentage: 14%'
        if command[-1]=='kern.memorystatus_vm_pressure_level': return str(values['pressure'])
        return f'vm.swapusage: total = 30000M used = {values["swap"]}M free = 1000M'
    monkeypatch.setattr(resource.subprocess,'check_output',output)
    monkeypatch.setattr(resource.shutil,'disk_usage',lambda _:SimpleNamespace(free=values['disk']*1024**3))
    monkeypatch.setattr(resource,'monotonic',lambda:values['time'])
    guard=ResourceGuard(tmp_path/'resources.jsonl')
    assert guard.check(starting=True)['pressure_level']==1  # residual swap and 14% free allowed
    values['pressure']=2;guard.check()
    values['time']=29;guard.check()
    values['time']=30
    guard.check()  # Coordinator: warning pressure is observation only.
    values['pressure']=1;values['swap']=26097
    with pytest.raises(ResourceLimit):guard.check()
    values['swap']=22000;values['pressure']=4
    with pytest.raises(ResourceLimit):guard.check()
    values['pressure']=1;values['disk']=9
    with pytest.raises(ResourceLimit):guard.check()


def test_date_checker_accepts_spaced_chinese_dates():
    from scripts.test_llm_scenarios import has_date
    text='入住日期為 2026 年 11 月 10 日至 11 月 12 日。'
    assert has_date(text,'2026-11-10','zh-TW')
    assert has_date(text,'2026-11-12','zh-TW')


def test_date_checker_rejects_wrong_explicit_year_and_accepts_range():
    from scripts.test_llm_scenarios import has_date
    assert not has_date('2027年11月10日','2026-11-10','zh-TW')
    assert not has_date('November 3rd, 2027','2026-11-03','en')
    assert has_date('November 3rd to 5th','2026-11-05','en')
    assert split_sentences('Really?! Yes!!')==['Really?!',' Yes!!']
