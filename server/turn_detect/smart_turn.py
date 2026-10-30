"""Local Smart Turn v3.2 CPU. Preprocessing follows pipecat smart-turn/inference.py."""
import asyncio
import json
import math
from pathlib import Path
from time import perf_counter
import numpy as np
from server.config import ROOT


class SmartTurn:
    def __init__(self, path=None):
        if path is None:
            try:
                paths = json.loads((ROOT/'models/paths.json').read_text())
                path = ROOT / paths['smartturn'] / 'smart-turn-v3.2-cpu.onnx'
            except (OSError, ValueError, KeyError) as exc:
                raise ValueError('Smart Turn 미적용: local model path is unavailable') from exc
        self.path = Path(path)
        if not self.path.is_file() or self.path.stat().st_size > 200_000_000:
            raise ValueError('Smart Turn 미적용: local v3.2 CPU model missing or exceeds 200MB')
        self.session = None
        self.lock = asyncio.Lock()

    def predict(self, pcm):
        import onnxruntime as ort
        from transformers import WhisperFeatureExtractor
        started = perf_counter()
        if self.session is None:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            self.session = ort.InferenceSession(str(self.path), sess_options=opts, providers=['CPUExecutionProvider'])
            self.extractor = WhisperFeatureExtractor(chunk_length=8)
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        audio = audio[-128000:]
        audio = np.pad(audio, (128000-len(audio), 0))
        features = self.extractor(audio, sampling_rate=16000, return_tensors='np',
                                  padding='max_length', max_length=128000,
                                  truncation=True, do_normalize=True).input_features.astype(np.float32)
        probability = float(self.session.run(None, {'input_features': features})[0].reshape(-1)[0])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError('Invalid Smart Turn probability')
        return {'model': 'smart-turn-v3.2-cpu', 'probability': probability,
                'complete': probability > .5, 'inference_ms': (perf_counter()-started)*1000}

    async def decide(self, pcm):
        async with self.lock:
            task = asyncio.create_task(asyncio.to_thread(self.predict, pcm))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # A cancelled await does not stop the native CPU call. Keep the
                # lock until it settles so a subsequent fragment cannot race init.
                await task
                raise
