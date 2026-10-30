#!/usr/bin/env python3
"""Create isolated synthetic-audio browser acceptance fixture; never research votes."""
import argparse
import json
from pathlib import Path
import sys
import wave
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.blind.aggregate import aggregate
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=ROOT/'reports/blind-browser-fixture')
folder=parser.parse_args().output.resolve()
folder.mkdir(parents=True,exist_ok=True)
items=[]
for number in range(1,4):
    for engine in ['voxcpm2','cosyvoice3','qwen3tts','kokoro']:
        path=folder/'audio'/f'{engine}-{number}.wav';path.parent.mkdir(exist_ok=True)
        t=np.arange(8400)/24000
        pcm=(np.sin(2*np.pi*440*t)*1500).astype('<i2').tobytes()
        with wave.open(str(path),'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(pcm)
        items.append({'item_id':f'{engine}-{number}','engine':engine,'locale':'en','sentence_id':f'en-{number}',
                      'normalize':'full','status':'ok','display_text':f'Browser test sentence {number}.',
                      'duration_s':.35,'file':str(path)})
(folder/'manifest.json').write_text(json.dumps({'items':items}))
print(folder)
