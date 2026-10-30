import json
from pathlib import Path
import sys
import wave
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import tts_common
from gen_blind_set import prepare_audio
from report import render


def test_resource_guard_growth_and_thresholds(monkeypatch):
    monkeypatch.setattr(tts_common,'_SWAP_BASELINE',None)
    state={'memory_free_pct':76,'swap_gib':22,'disk_free_gb':100,'pressure_level':1}
    monkeypatch.setattr(tts_common,'resource_status',lambda:dict(state))
    assert tts_common.guard()['swap_baseline_gib']==22
    state['swap_gib']=25.9
    assert tts_common.guard()
    state['swap_gib']=26.1
    with pytest.raises(RuntimeError,match='swap growth'):
        tts_common.guard()
    state.update(swap_gib=22,memory_free_pct=14)
    assert tts_common.guard()  # free percentage is no longer a pressure proxy
    state['pressure_level']=4
    with pytest.raises(RuntimeError,match='critical'):
        tts_common.guard(running=True)
    state['pressure_level']=1
    state.update(memory_free_pct=76,disk_free_gb=9)
    with pytest.raises(RuntimeError,match='disk free'):
        tts_common.guard()


def test_postprocess_pcm_header_padding_and_loudness(tmp_path):
    # Speech-like amplitude-modulated tone tests processing, not model quality.
    t=np.arange(24000*4)/24000
    sound=np.sin(2*np.pi*220*t)*(.1+.04*np.sin(2*np.pi*3*t))
    pcm=(np.pad(sound,(12000,12000))*32767).astype('<i2').tobytes()
    path=tmp_path/'opaque.wav'
    metrics=prepare_audio(pcm,path)
    with wave.open(str(path),'rb') as wav:
        assert (wav.getnchannels(),wav.getframerate(),wav.getsampwidth())==(1,24000,2)
        audio=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
    assert np.all(audio[:3600]==0) and np.all(audio[-3600:]==0)
    assert -21 < metrics['lufs'] < -19
    assert metrics['duration_s']==len(audio)/24000
    assert path.read_bytes()[36:40]==b'data' # Python WAV has no metadata chunk.
    assert not list(tmp_path.glob('*.input.wav'))


def test_report_all_twenty_engine_locale_cells():
    report,summary=render({'created_at':'test','engines':{},'rows':[]})
    assert len(summary)==20
    for engine in tts_common.PORTS:
        for locale in tts_common.LOCALES:
            assert f'| {engine} | {locale} |' in report
    assert '실패: unsupported locale' in report


def test_reduced_engine_uses_first_full_sample_only():
    data={'created_at':'test','engines':{},'sampling_policy':{'cosyvoice3':{'levels':['full'],'repeats':1}},
          'rows':[{'engine':'cosyvoice3','locale':'en','normalize':level,'repeat':repeat,
                   'rtf':rtf,'ttfa_ms':1000,'failure':None} for level,repeat,rtf in [('full',0,2),('full',1,99),('raw',0,5)]]}
    report,summary=render(data)
    row=next(r for r in summary if r['engine']=='cosyvoice3' and r['locale']=='en')
    assert row['success']==1 and row['rtf_median']==2 and row['rtf_p90'] is None
    assert '축소 측정(표본 1)' in report and 'raw 비교 제외' in report
    assert len(data['rows'])==3
