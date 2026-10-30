import asyncio
import json
from pathlib import Path
from time import perf_counter
from server.pipeline.turn import Call, Settings
from server.pipeline.sessions import aggregate
from tests.voice_stubs import runtime
from tests.test_voice_pipeline import Socket


class Detector:
    def __init__(self, complete=False): self.complete=complete; self.inputs=[]
    async def decide(self, pcm):
        self.inputs.append(pcm)
        return dict(complete=self.complete, probability=.9 if self.complete else .1, inference_ms=1)


def test_merge_and_cancel_pending(tmp_path):
    async def run():
        rt=runtime(tmp_path); rt.smart_turn=Detector()
        call=Call(Socket(),rt); await call.configure(Settings(turn_detect='vad+smartturn'))
        await call.start(b'\x01\x00'*100, {'turn_num':1,'t_vad_end':10}, perf_counter()*1000)
        old=call.turn
        await asyncio.sleep(.02)
        await call.start(b'\x02\x00'*100, {'turn_num':2,'t_vad_end':20}, perf_counter()*1000)
        await asyncio.sleep(.02)
        assert old.cancelled and old.log.details['cancel_reason']=='merged'
        assert call.turn.pcm == b'\x01\x00'*100+b'\x02\x00'*100
        assert call.turn.decision_deadline == old.decision_deadline
        await call.close()
        assert len(rt.agents[0].messages) == 1
    asyncio.run(run())


def test_incomplete_times_out_and_complete_is_immediate(tmp_path):
    async def run(complete):
        rt=runtime(tmp_path); rt.smart_turn=Detector(complete)
        call=Call(Socket(),rt); await call.configure(Settings(turn_detect='vad+smartturn'))
        started=perf_counter()*1000
        await call.start(b'\x01\x00'*100, {'turn_num':1,'t_vad_end':10}, started)
        await call.turn.task
        duration=call.turn.log.marks['s_turn_decided']-started
        assert (duration < 500 if complete else 1450 <= duration < 2000)
        assert call.turn.log.details['smart_turn']['release']==('complete' if complete else 'timeout')
        await call.close()
    asyncio.run(run(False)); asyncio.run(run(True))


def test_session_rates_latest_revision_and_exclusions(tmp_path):
    sessions=[dict(session_id='a',revision=1,closed=False,duration_ms=1,engines=['kokoro'],handoff_queued=False),
              dict(session_id='a',revision=2,closed=True,duration_ms=180000,engines=['kokoro'],handoff_queued=True),
              dict(session_id='b',revision=1,closed=True,duration_ms=1000,engines=['kokoro'],handoff_queued=False),
              dict(session_id='warm',revision=1,closed=True,duration_ms=10,engines=['kokoro'],handoff_queued=False)]
    turns=[dict(session_id='a',turn_num=1,revision=1,user_chars=99,durations={}),
           dict(session_id='a',turn_num=1,revision=2,user_chars=10,barge_in={'played_ms':20},durations={'client_vad_to_play_ms':100,'client_vad_to_reply_play_ms':200}),
           dict(session_id='a',turn_num=2,revision=1,cancel_reason='merged',durations={}),
           dict(session_id='b',turn_num=1,revision=1,user_chars=20,barge_in={'played_ms':20,'during_playback':False},durations={}),
           dict(session_id='warm',turn_num=1,revision=1,measurement_phase='warmup_discarded',durations={})]
    for name,rows in [('sessions',sessions),('turns',turns)]:
        (tmp_path/(name+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in rows)+'\n{')
    r=aggregate(tmp_path)['kokoro']
    assert (r['calls'],r['over_3min_rate'],r['barge_in_rate'],r['customer_chars_per_call'],r['total_turns'],r['handoff_calls'])==(2,.5,.5,15,2,1)
    assert r['first_play_ms']['p50']==100 and r['reply_play_ms']['p50']==200


def test_native_prediction_settles_before_next_call_after_cancel(tmp_path):
    from server.turn_detect.smart_turn import SmartTurn
    import time
    async def run():
        path=tmp_path/'model.onnx';path.write_bytes(b'placeholder')
        detector=SmartTurn(path); active=0; maximum=0
        def predict(pcm):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active)
            time.sleep(.06);active-=1
            return {'complete':True}
        detector.predict=predict
        task=asyncio.create_task(detector.decide(b'1'))
        await asyncio.sleep(.02);task.cancel()
        second=asyncio.create_task(detector.decide(b'2'))
        result=await asyncio.gather(task,second,return_exceptions=True)
        assert isinstance(result[0],asyncio.CancelledError)
        assert result[1]['complete'] and maximum==1 and active==0
    asyncio.run(run())
