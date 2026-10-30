"""Pinned CosyVoice3 inference with cooperative token/chunk cancellation."""
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
LOCALES = ['en', 'zh-CN', 'zh-TW', 'ja', 'yue']
MODEL_ID = 'FunAudioLLM/Fun-CosyVoice3-0.5B-2512'
for key, value in {
    'HF_HOME': ROOT / 'models', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
    'PYTORCH_ENABLE_MPS_FALLBACK': '1', 'MPLCONFIGDIR': BASE / '.cache/matplotlib',
    'NUMBA_CACHE_DIR': BASE / '.cache/numba',
}.items():
    os.environ.setdefault(key, str(value))
sys.path[:0] = [str(BASE / 'vendor/CosyVoice'), str(BASE / 'vendor/Matcha-TTS')]


class Backend:
    def __init__(self, device='auto', threads=4):
        self.device = device
        self.threads = threads
        self.model = None
        self.load_ms = 0.0
        self.peak_mem_bytes = 0
        self.refs = json.loads((ROOT / 'data/refs/MANIFEST.json').read_text())
        self.cache = {}

    def resolve_voice(self, locale, voice):
        if locale not in LOCALES:
            raise ValueError('Unsupported locale')
        if voice and voice.get('preset'):
            raise ValueError('CosyVoice3 requires a reference, not a preset')
        if voice and voice.get('ref_text') and not voice.get('ref_audio_path'):
            raise ValueError('ref_audio_path is required with ref_text')
        if voice and voice.get('ref_audio_path'):
            path = Path(voice['ref_audio_path'])
            path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_relative_to(ROOT):
                raise ValueError('Reference must be inside the project')
            text = voice.get('ref_text', '')
            if not text.strip():
                raise ValueError('ref_text is required with ref_audio_path')
        else:
            locale = self.refs['locale_reference'][locale]
            ref = next(r for r in self.refs['references'] if r['locale'] == locale)
            path, text = ROOT / ref['audio_path'], ref['raw_transcription']
        if not path.is_file():
            raise ValueError('Reference audio does not exist')
        import soundfile as sf
        info = sf.info(path)
        if not 0 < info.duration <= 30 or info.samplerate < 16000:
            raise ValueError('Reference must be 0–30 seconds and at least 16 kHz')
        return str(path), text

    def load(self):
        if self.model is not None:
            return
        started = time.perf_counter()
        import torch
        from cosyvoice.cli.cosyvoice import CosyVoice3
        if self.device == 'auto':
            self.device = 'mps' if torch.backends.mps.is_available() else 'cpu'
        if self.device == 'mps' and not torch.backends.mps.is_available():
            raise RuntimeError('MPS requested but unavailable')
        os.environ['COSYVOICE_DEVICE'] = self.device
        torch.set_num_threads(self.threads)
        path = ROOT / json.loads((ROOT / 'models/paths.json').read_text())['cosyvoice3']
        if not path.is_dir():
            raise RuntimeError('Local CosyVoice3 snapshot missing; automatic downloads disabled')
        self.model = CosyVoice3(str(path), load_trt=False, load_vllm=False, fp16=False)
        self.load_ms = (time.perf_counter() - started) * 1000
        self.measure_memory()

    def measure_memory(self):
        import psutil
        rss = psutil.Process().memory_info().rss
        import torch
        gpu = torch.mps.driver_allocated_memory() if self.device == 'mps' else 0
        self.peak_mem_bytes = max(self.peak_mem_bytes, rss, gpu)
        return rss, gpu

    def unload(self):
        self.model = None
        self.cache.clear()
        gc.collect()
        import torch
        if self.device == 'mps':
            torch.mps.empty_cache()

    def generate(self, text, locale, voice, seed, stream, cancel: threading.Event):
        import numpy as np
        import torch
        from cosyvoice.utils.common import set_all_random_seed
        self.load()
        path, ref_text = self.resolve_voice(locale, voice)
        instruction = 'You are a helpful assistant.'
        if locale == 'yue':
            instruction += ' 请用广东话表达。'
        prompt = instruction + '<|endofprompt|>' + ref_text
        key = hashlib.sha256((path + prompt).encode()).hexdigest()
        # Cache only the five shared references, not arbitrary user paths.
        cacheable = Path(path).parent.parent == ROOT / 'data/refs'
        with torch.inference_mode():
            if key in self.cache:
                inputs = dict(self.cache[key])
                inputs['text'], inputs['text_len'] = self.model.frontend._extract_text_token(text)
            else:
                inputs = self.model.frontend.frontend_zero_shot(text, prompt, path, 24000, '')
                if cacheable:
                    if len(self.cache) >= 5:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[key] = {k: v for k, v in inputs.items() if k not in ('text', 'text_len')}
            if cancel.is_set():
                return
            set_all_random_seed(seed)
            model = self.model.model
            device = model.device
            request_id = str(uuid.uuid4())
            model.hift_cache_dict[request_id] = None
            tokens = []
            offset, hop, silent = 0, 25, 0
            prompt_tokens = inputs['flow_prompt_speech_token']
            pad = (-prompt_tokens.shape[1]) % hop
            def length(x):
                return torch.tensor([x.shape[1]], dtype=torch.int32, device=device)
            def render(final):
                end = len(tokens) if final else offset + hop + (pad if offset == 0 else 0) + model.flow.pre_lookahead_len
                audio = model.token2wav(
                    token=torch.tensor([tokens[:end]], dtype=torch.int32),
                    prompt_token=prompt_tokens, prompt_feat=inputs['prompt_speech_feat'],
                    embedding=inputs['flow_embedding'], token_offset=offset,
                    uuid=request_id, stream=stream and not final, finalize=final,
                ).detach().float().cpu().numpy().reshape(-1)
                if not np.isfinite(audio).all():
                    raise RuntimeError('Non-finite audio from model')
                self.measure_memory()
                return np.clip(np.rint(audio * 32768), -32768, 32767).astype('<i2').tobytes()
            token_generator = model.llm.inference(
                text=inputs['text'].to(device), text_len=length(inputs['text']),
                prompt_text=inputs['prompt_text'].to(device), prompt_text_len=length(inputs['prompt_text']),
                prompt_speech_token=inputs['llm_prompt_speech_token'].to(device),
                prompt_speech_token_len=length(inputs['llm_prompt_speech_token']),
                embedding=inputs['llm_embedding'].to(device), uuid=request_id,
            )
            try:
                for token in token_generator:
                    if cancel.is_set():
                        return
                    silent = silent + 1 if token in model.silent_tokens else 0
                    if silent > 5:
                        continue
                    tokens.append(token)
                    current_hop = hop + (pad if offset == 0 else 0)
                    if stream and len(tokens) - offset >= current_hop + model.flow.pre_lookahead_len:
                        pcm = render(False)
                        offset += current_hop
                        hop = min(100, hop * 2)
                        if cancel.is_set():
                            return
                        yield pcm
                if not cancel.is_set() and tokens:
                    pcm = render(True)
                    if not cancel.is_set():
                        yield pcm
            finally:
                token_generator.close()
                model.hift_cache_dict.pop(request_id, None)
