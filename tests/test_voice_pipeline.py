import asyncio
from copy import deepcopy
import json
from time import perf_counter

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from server.pipeline.api import create_router
from server.pipeline.latency import SERVER_MARKS, durations
from server.pipeline.sentence_splitter import SentenceSplitter
from server.pipeline.turn import Call, Settings, HEADER
from server.tts.client import TTSClient
from tests.voice_stubs import runtime, StubSTT, StubTTS, REPLIES


class Socket:
    def __init__(self): self.frames = []
    async def send_json(self, data): self.frames.append(deepcopy(data))
    async def send_bytes(self, data): self.frames.append(data)


async def configured(tmp_path, **kwargs):
    rt = runtime(tmp_path, **kwargs)
    socket = Socket()
    call = Call(socket, rt)
    await call.configure(Settings())
    return call, rt, socket


async def start(call, number=1):
    await call.start(b'\x01\x00'*1600, {'turn_num': number, 't_vad_end': 1000}, perf_counter()*1000)
    return call.turn


def records(tmp_path):
    return [json.loads(line) for line in (tmp_path/'turns.jsonl').read_text().splitlines()]


@pytest.mark.parametrize('locale', ['en', 'zh-CN', 'zh-TW', 'ja'])
def test_ws_four_locales_and_all_server_marks(tmp_path, locale):
    rt = runtime(tmp_path)
    app = FastAPI(); app.include_router(create_router(rt))
    with TestClient(app) as client, client.websocket_connect('/ws/call') as ws:
        ws.send_json({'type': 'session.start', 'locale': locale})
        assert ws.receive_json()['type'] == 'session.ready'
        ws.send_json({'type': 'utterance', 'turn_num': 1, 'samples': 1600, 't_vad_end': 1000})
        ws.send_bytes(HEADER.pack(1, 0, 0, 1) + b'\x01\x00'*1600)
        events, pcm, first_chunk = [], bytearray(), True
        while True:
            frame = ws.receive()
            if 'bytes' in frame:
                kind, reserved, sequence, turn = HEADER.unpack_from(frame['bytes'])
                assert kind == 2 and reserved == 0 and turn == 1
                pcm.extend(frame['bytes'][8:])
                if first_chunk:
                    ws.send_json({'type': 'playback.started', 'turn_num': 1, 'chunk_seq': sequence, 't_client': 1010})
                    first_chunk = False
                continue
            event = json.loads(frame['text']); events.append(event)
            assert event['type'] != 'error', event
            if event['type'] == 'tts.sentence': first_chunk = True
            if event['type'] == 'turn.done':
                assert all(event['marks'][key] is not None for key in SERVER_MARKS)
                ws.send_json({'type': 'playback.finished', 'turn_num': 1, 't_client': 1050})
                break
        assert any(pcm)
        assert any(event['type'] == 'tool.result' for event in events)
    log = records(tmp_path)[-1]
    assert log['spoken_text'] == REPLIES[locale]
    assert log['marks']['c_play_start'] == 1010
    assert len(log['sentences']) == 2
    assert all(set(sentence) >= {'display_text', 'tts_text_raw', 'tts_text_lex', 'tts_text_full'} for sentence in log['sentences'])
    assert rt.agents[0].messages[-1]['content'] == REPLIES[locale]


@pytest.mark.parametrize('phase', ['stt', 'llm', 'tts', 'done'])
def test_cancellation_ack_and_no_stale_frames(tmp_path, phase):
    async def run():
        stt = StubSTT(delay=20 if phase == 'stt' else 0)
        tts = StubTTS()
        call, rt, socket = await configured(tmp_path, stt=stt, tts=tts, llm_delay=20 if phase=='llm' else 0)
        if phase == 'tts': tts.delay = 20
        turn = await start(call)
        if phase == 'done': await turn.task
        elif phase == 'tts':
            while len(tts.calls) < 2: await asyncio.sleep(.001)
        else: await asyncio.sleep(.01)
        tick = perf_counter()
        await turn.cancel()
        elapsed = (perf_counter()-tick)*1000
        ack = next(i for i, frame in enumerate(socket.frames) if isinstance(frame, dict) and frame['type']=='turn.cancelled')
        assert elapsed < 200
        assert not any(isinstance(frame, bytes) for frame in socket.frames[ack+1:])
        assert not any(isinstance(frame, dict) and frame['type']=='turn.done' for frame in socket.frames[ack+1:])
        assert rt.agents[0].messages == [rt.agents[0]._system]
        assert not rt.agents[0].session.verified_codes
        assert tts.active == 0
        assert turn.task.done()
        if phase == 'stt': assert stt.cancelled
        if phase == 'llm': assert rt.agents[0].client.closed
        assert records(tmp_path)[-1]['status'] == 'cancelled'
    asyncio.run(run())


def test_barge_after_done_keeps_only_started_reply_prefix(tmp_path):
    async def run():
        call, rt, socket = await configured(tmp_path)
        turn = await start(call)
        await turn.task
        sequence = next(seq for seq, (index, role) in turn.chunk_sentences.items() if index == 0)
        turn.playback_started(sequence, 1100)
        await turn.cancel()
        assert rt.agents[0].messages[-1]['content'] == 'I found your booking.'
        assert rt.agents[0].session.verified_codes == {'ZV-4821'}
        assert records(tmp_path)[-1]['spoken_text'] == 'I found your booking.'
        # Delayed playback and duplicate barge cannot grow a cancelled history.
        turn.playback_started(max(turn.chunk_sentences), 1200)
        await turn.cancel()
        assert rt.agents[0].messages[-1]['content'] == 'I found your booking.'
    asyncio.run(run())


def test_cancel_during_tts_reconciles_then_new_turn(tmp_path):
    async def run():
        call, rt, socket = await configured(tmp_path)
        rt.tts.delay = .03
        turn = await start(call)
        while not any(index == 0 for index, role in turn.chunk_sentences.values()):
            await asyncio.sleep(.001)
        seq = next(seq for seq, (index, role) in turn.chunk_sentences.items() if index == 0)
        turn.playback_started(seq, 1100)
        await turn.cancel()
        assert rt.agents[0].messages[-1]['content'] == 'I found your booking.'
        new = await start(call, 2)
        await new.task
        await call.close()
        assert rt.tts.active == 0
    asyncio.run(run())


def test_filler_cache_once_and_queue_depth(tmp_path, monkeypatch):
    async def run():
        call, rt, socket = await configured(tmp_path)
        assert len(rt.tts.calls) == 1
        await call.configure(Settings())
        assert len(rt.tts.calls) == 1
        monkeypatch.setitem(REPLIES, 'en', 'One sentence. Two sentences. Three sentences. Four sentences. Five sentences.')
        turn = await start(call)
        await turn.task
        assert turn.log.details['max_queue_depth'] == 2
        assert rt.tts.max_active == 1
        await call.close()
    asyncio.run(run())


def test_tts_failure_cleans_up_and_rolls_back(tmp_path):
    async def run():
        call, rt, socket = await configured(tmp_path)
        rt.tts.fail = True
        turn = await start(call)
        await turn.task
        assert turn.status == 'error'
        assert any(isinstance(f, dict) and f.get('code') == 'turn_failed' for f in socket.frames)
        assert not any(isinstance(f, dict) and f.get('type') == 'turn.done' for f in socket.frames)
        assert rt.tts.active == 0
        assert rt.agents[0].messages == [rt.agents[0]._system]
    asyncio.run(run())


@pytest.mark.parametrize('metadata,binary', [
    ({'samples': 0}, None), ({'samples': 960001}, None), ({'samples': True}, None),
    ({'t_vad_end': float('nan')}, None), ({'turn_num': -1}, None),
    ({}, b'bad'), ({}, HEADER.pack(2, 0, 0, 1) + b'\0\0'),
    ({}, HEADER.pack(1, 0, 0, 2) + b'\0\0'),
    ({}, HEADER.pack(1, 0, 0, 1) + b'\0'),
])
def test_malformed_frames_rejected_before_inference(tmp_path, metadata, binary):
    rt = runtime(tmp_path)
    app = FastAPI(); app.include_router(create_router(rt))
    with TestClient(app) as client, client.websocket_connect('/ws/call') as ws:
        ws.send_json({'type': 'session.start'})
        ws.receive_json()
        ws.send_json({'type': 'utterance', 'turn_num': 1, 'samples': 1, 't_vad_end': 10, **metadata})
        if binary is not None: ws.send_bytes(binary)
        assert ws.receive_json()['code'] == 'invalid_message'
    assert rt.agents[0].turn_num == 0


def test_splitter_fragmentation_decimals_abbreviations_and_commas():
    text = 'The fee is 3.5 percent at 9 a.m. for ZV-4821. Thank you!'
    splitter = SentenceSplitter()
    results = []
    for char in text: results.extend(splitter.feed(char))
    results.extend(splitter.finish())
    assert results == ['The fee is 3.5 percent at 9 a.m. for ZV-4821.', ' Thank you!']
    splitter = SentenceSplitter(first_comma=True)
    assert splitter.feed('Well, I can check that now, please wait.') == ['Well, I can check that now,']
    assert splitter.finish() == [' please wait.']
    splitter = SentenceSplitter('zh-TW', first_comma=True)
    assert splitter.feed('您好，這裡是客服中心，請稍候。') == ['您好，這裡是客服中心，', '請稍候。']


def test_clock_domains_and_null_tool_points():
    values = durations({'s_turn_decided': 10000, 's_stt_done': 10050,
                       'c_vad_end': 20, 'c_play_start': 70})
    assert values['stt_ms'] == 50 and values['client_vad_to_play_ms'] == 50
    assert values['tts_ttfa_ms'] is None


def test_tts_http_handles_odd_chunks_and_closes_on_cancel():
    async def run():
        closed = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'\x01'
                yield b'\x02\x03\x04'
                await asyncio.Event().wait()
            async def aclose(self): closed.set()
        def handler(request):
            payload = json.loads(request.content)
            assert payload['stream'] and payload['voice'] == {'preset': 'af_heart'}
            return httpx.Response(200, headers={'content-type': 'audio/L16; rate=24000; channels=1'}, stream=Stream())
        tts = TTSClient(transport=httpx.MockTransport(handler))
        stream = tts.synthesize('kokoro', 'Hi.', 'en')
        assert await anext(stream) == b'\x01\x02\x03\x04'
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert closed.is_set()
    asyncio.run(run())


def test_missing_earlier_playback_ack_cannot_commit_unheard_text(tmp_path):
    async def run():
        call, rt, socket = await configured(tmp_path)
        turn = await start(call)
        await turn.task
        seq = next(seq for seq, (index, role) in turn.chunk_sentences.items() if index == 1)
        turn.playback_started(seq, 1100)
        await turn.cancel()
        assert rt.agents[0].messages == [rt.agents[0]._system]
        assert records(tmp_path)[-1]['spoken_text'] == ''
    asyncio.run(run())


def test_no_tool_turn_keeps_tool_marks_null(tmp_path, monkeypatch):
    from tests.voice_stubs import INPUTS
    monkeypatch.setitem(INPUTS, 'en', 'Hello, please help.')
    async def run():
        call, rt, socket = await configured(tmp_path)
        turn = await start(call)
        await turn.task
        assert turn.log.marks['s_tool_call'] is None
        assert turn.log.marks['s_tool_done'] is None
        assert not turn.log.details['tools']
        assert turn.log.details['llm']['metrics']['llm_rounds'] == 1
        await call.close()
    asyncio.run(run())


def test_cancel_ack_does_not_wait_for_slow_cleanup(tmp_path):
    class SlowClose(StubTTS):
        async def synthesize(self, engine, text, locale):
            self.calls.append((engine, text, locale))
            try:
                yield b'\1\0'*480
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.3)
    async def run():
        call, rt, socket = await configured(tmp_path)
        rt.tts = SlowClose()
        turn = await start(call)
        while not rt.tts.calls: await asyncio.sleep(.001)
        tick = perf_counter()
        cancelling = asyncio.create_task(turn.cancel())
        while not any(isinstance(f, dict) and f['type']=='turn.cancelled' for f in socket.frames):
            await asyncio.sleep(.001)
        assert (perf_counter()-tick)*1000 < 200
        assert not cancelling.done()
        await cancelling
        assert (perf_counter()-tick)*1000 >= 300
        assert rt.agents[0]._active is False
    asyncio.run(run())


def test_disconnect_waits_for_pipeline_cleanup(tmp_path):
    async def run():
        call, rt, socket = await configured(tmp_path, llm_delay=20)
        turn = await start(call)
        await asyncio.sleep(.01)
        await call.close()
        assert turn.task.done()
        assert rt.agents[0].client.closed
        assert rt.agents[0].messages == [rt.agents[0]._system]
    asyncio.run(run())


@pytest.mark.parametrize('settings', [{'stt': 'qwen3asr'}, {'turn_detect': 'invalid'},
                                      {'locale': 'yue', 'tts_engine': 'kokoro'},
                                      {'tts_engine': 'unknown'}, {'locale': 'ko'},
                                      {'normalize': 'bad'}, {'extra': True}])
def test_unsupported_session_settings_do_not_trigger_models(tmp_path, settings):
    rt = runtime(tmp_path)
    app = FastAPI(); app.include_router(create_router(rt))
    with TestClient(app) as client, client.websocket_connect('/ws/call') as ws:
        ws.send_json({'type': 'session.start', **settings})
        assert ws.receive_json()['code'] == 'invalid_message'
    assert not rt.tts.calls


def test_transferred_state_comes_only_from_tool_result(tmp_path, monkeypatch):
    from tests.voice_stubs import INPUTS
    monkeypatch.setitem(INPUTS, 'en', 'Please connect me with a human agent.')
    class Handoff:
        model = 'test-only-handoff'
        def __init__(self): self.calls = 0
        async def stream_chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield {'message': {'tool_calls': [{'function': {'name': 'transfer_to_human', 'arguments':
                    {'reason': 'user_request', 'summary_ko': '고객이 상담원을 요청했습니다.'}}}]}}
            else:
                yield {'message': {'content': 'You are in the queue. The estimated wait is three minutes.'}}
            yield {'done': True}
    async def run():
        call, rt, socket = await configured(tmp_path)
        rt.agents[0].client = Handoff()
        turn = await start(call)
        await turn.task
        assert turn.log.details['handoff_queued'] is True
        for seq, (index, role) in turn.chunk_sentences.items():
            if index is not None: turn.playback_started(seq, 1100)
        turn.reconcile()
        with pytest.raises(ValueError, match='Human handoff queued'):
            await start(call, 2)
    asyncio.run(run())


def test_failed_cancel_ack_still_awaits_cleanup_and_rolls_back(tmp_path):
    async def run():
        call, rt, socket = await configured(tmp_path, llm_delay=20)
        turn = await start(call)
        await asyncio.sleep(.01)
        async def disconnected(data): raise RuntimeError('socket already disconnected')
        socket.send_json = disconnected
        with pytest.raises(RuntimeError, match='socket already disconnected'):
            await turn.cancel()
        assert turn.task.done() and turn.finalized
        assert rt.agents[0].client.closed
        assert rt.agents[0].messages == [rt.agents[0]._system]
        await call.close()
    asyncio.run(run())


@pytest.mark.parametrize('sentence_count', [2, 4, 5])
def test_review_backpressure_cannot_rollback_completed_agent(tmp_path, monkeypatch, sentence_count):
    # Uses the real T6 VoiceAgent and prefetch; only its model's wire chunks and
    # audio synthesis are faked, exactly the path that failed the review probe.
    monkeypatch.setitem(REPLIES, 'en', ' '.join(['I found your booking.'] + ['More details.']*(sentence_count-1)))
    async def run():
        call, rt, socket = await configured(tmp_path)
        rt.tts.delay = .04
        turn = await start(call)
        while not any(index == 0 for index, role in turn.chunk_sentences.values()):
            await asyncio.sleep(.001)
        assert turn.log.details['llm']['metrics']['sentence_count'] == sentence_count
        assert turn.log.marks['s_llm_first_token'] is not None
        assert not rt.agents[0]._active
        seq = next(seq for seq, (index, role) in turn.chunk_sentences.items() if index == 0)
        turn.playback_started(seq, 1100)
        await turn.cancel()
        assert rt.agents[0].session.verified_codes == {'ZV-4821'}
        assert [m['role'] for m in rt.agents[0].messages] == ['system', 'user', 'assistant', 'tool', 'assistant']
        assert rt.agents[0].messages[-1]['content'] == 'I found your booking.'
        assert (tmp_path/'llm.jsonl').exists()
        assert turn.log.details['max_queue_depth'] <= 2
    asyncio.run(run())


def test_review_finished_with_only_filler_start_commits_complete_reply(tmp_path):
    rt = runtime(tmp_path)
    app = FastAPI(); app.include_router(create_router(rt))
    with TestClient(app) as client, client.websocket_connect('/ws/call') as ws:
        ws.send_json({'type': 'session.start'})
        ws.receive_json()
        ws.send_json({'type': 'utterance', 'turn_num': 1, 'samples': 1, 't_vad_end': 1000})
        ws.send_bytes(HEADER.pack(1, 0, 0, 1)+b'\1\0')
        acknowledged = False
        while True:
            frame = ws.receive()
            if 'bytes' in frame:
                if not acknowledged:
                    seq = HEADER.unpack_from(frame['bytes'])[2]
                    ws.send_json({'type': 'playback.started', 'turn_num': 1, 'chunk_seq': seq, 't_client': 1100})
                    acknowledged = True
            else:
                event = json.loads(frame['text'])
                assert event['type'] != 'error'
                if event['type'] == 'turn.done':
                    ws.send_json({'type': 'playback.finished', 'turn_num': 1, 't_client': 2000})
                    break
    assert rt.agents[0].messages[-1]['content'] == REPLIES['en']
    assert rt.agents[0].session.verified_codes == {'ZV-4821'}
    assert records(tmp_path)[-1]['spoken_text'] == REPLIES['en']


@pytest.mark.parametrize('text, expected', [
    ('ホテルは明洞です。」', ['ホテルは明洞です。」']),
    ('还需要？）', ['还需要？）']),
    ('He said "OK." Then left.', ['He said "OK."', ' Then left.']),
    ('1. Check in.\n2. Pay.', ['1. Check in.\n', '2. Pay.']),
])
def test_review_closing_quotes_and_numbered_lists(text, expected):
    from server.llm.sentences import split_sentences
    assert split_sentences(text) == expected


@pytest.mark.parametrize('reply', ['ホテルは明洞です。」', '還需要嗎？）', '1. Check in.\n2. Pay.', 'Confirmed.\n ）'])
def test_review_punctuation_runs_through_actual_agent_and_tts(tmp_path, monkeypatch, reply):
    monkeypatch.setitem(REPLIES, 'en', reply)
    async def run():
        call, rt, socket = await configured(tmp_path)
        turn = await start(call)
        await turn.task
        assert turn.status == 'done'
        assert all(any(char.isalnum() for char in text) for engine, text, locale in rt.tts.calls)
        turn.playback_finished(2000)
        assert rt.agents[0].messages[-1]['content'] == reply
        if reply.endswith('\n ）'):
            assert turn.sentences[-1]['audio_skipped'] == 'no_speakable_text'
            assert len(rt.tts.calls) == 2  # filler + actual reply, no punctuation TTS
    asyncio.run(run())


def test_review_reply_latency_is_separate_from_filler(tmp_path):
    async def run():
        call, rt, socket = await configured(tmp_path)
        turn = await start(call)
        await turn.task
        filler = next(seq for seq, (index, role) in turn.chunk_sentences.items() if role == 'filler')
        reply = next(seq for seq, (index, role) in turn.chunk_sentences.items() if role == 'reply')
        turn.log.mark('c_speech_end_est', 500)
        turn.playback_started(filler, 1100)
        turn.playback_started(reply, 2500)
        measured = durations(turn.log.marks)
        assert measured['client_vad_to_play_ms'] == 100
        assert measured['client_vad_to_reply_play_ms'] == 1500
        assert measured['client_speech_to_reply_play_ms'] == 2000
        assert turn.log.details['c_play_start_meaning'] == 'first_audio_including_filler'
        await call.close()
    asyncio.run(run())


def test_review_live_warmup_uses_discarded_server_turn(tmp_path):
    from argparse import Namespace
    from scripts.validate_voice_pipeline import warmup_live
    calls = []
    class Client:
        async def warmup(self):
            calls.append('ollama')
            return {'done_reason': 'load'}
    async def wire(args):
        assert calls == ['ollama']
        assert args.locale == 'en' and args.warm is False
        assert args.measurement_phase == 'warmup_discarded'
        calls.append('server-whisper-through-ws')
        return {'audio_bytes': 480, 'warm': args.warm}
    async def run():
        result = await warmup_live(Namespace(locale='en'), tmp_path, client=Client(), ws_runner=wire)
        assert result['warm'] is True and calls == ['ollama', 'server-whisper-through-ws']
        assert json.loads((tmp_path/'warmup.json').read_text())['warm'] is True
    asyncio.run(run())
