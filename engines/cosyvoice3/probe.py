import os,sys,json,time
from pathlib import Path
BASE=Path(__file__).resolve().parent; ROOT=BASE.parents[1]
os.environ['HF_HOME']=str(ROOT/'models');os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['PYTORCH_ENABLE_MPS_FALLBACK']='1'
os.environ['MPLCONFIGDIR']=str(BASE/'.cache/matplotlib');os.environ['NUMBA_CACHE_DIR']=str(BASE/'.cache/numba')
sys.path[:0]=[str(BASE/'vendor/CosyVoice'),str(BASE/'vendor/Matcha-TTS')]
import torch,soundfile as sf
from cosyvoice.cli.cosyvoice import CosyVoice3
p=ROOT/json.loads((ROOT/'models/paths.json').read_text())['cosyvoice3']
torch.set_num_threads(4)
t=time.perf_counter(); m=CosyVoice3(str(p)); print('LOADED',m.model.device,time.perf_counter()-t,flush=True)
text='Hello, this is ZIVO customer support. How can I help you today?'
ref=(ROOT/'data/refs/en/ref.txt').read_text()
t=time.perf_counter()
for o in m.inference_zero_shot(text,'You are a helpful assistant.<|endofprompt|>'+ref,str(ROOT/'data/refs/en/ref.wav'),stream=False,text_frontend=False):
 a=o['tts_speech'].numpy().reshape(-1);sf.write(str(BASE/('probe-'+os.environ.get('COSYVOICE_DEVICE','auto')+'.wav')),a,m.sample_rate);print('AUDIO',len(a)/m.sample_rate,float(abs(a).max()),time.perf_counter()-t,flush=True)
