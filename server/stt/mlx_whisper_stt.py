"""Local MLX Whisper, serialized on a worker thread for the FastAPI pipeline.

Cancelling an await discards the result, but does not interrupt an in-flight GPU
decode. The single worker prevents concurrent access to mlx-whisper's model cache.
No model downloads are initiated here: paths must already exist locally.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import partial
import importlib
import json
from pathlib import Path
import threading
import time

import numpy as np

from .base import AudioInput, LanguageState, LOCALE_LANGUAGE, STTResult, validate_locale
from .script import normalize_script

ROOT = Path(__file__).resolve().parents[2]
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='stt-whisper')
_LOCK = threading.Lock()


class MLXWhisperSTT:
    def __init__(self, model_path: str | Path | None = None):
        if model_path is None:
            paths = json.loads((ROOT / 'models/paths.json').read_text())
            model_path = paths['whisper']
        self.model_path = Path(model_path)
        if not self.model_path.is_absolute():
            self.model_path = ROOT / self.model_path
        if not (self.model_path / 'config.json').is_file():
            raise FileNotFoundError(f'Whisper model is not installed: {self.model_path}')

    def initial_prompt(self, locale: str) -> str:
        validate_locale(locale)
        from server.normalize.lexicon import initial_prompt
        return initial_prompt(locale)

    @staticmethod
    def _audio(audio: AudioInput, sample_rate: int) -> np.ndarray:
        if isinstance(audio, (str, Path)):
            from mlx_whisper.audio import load_audio
            waveform = load_audio(str(audio))  # ffmpeg: mono float32, 16 kHz
        else:
            if sample_rate != 16000:
                raise ValueError('PCM/array inputs must be mono 16000 Hz')
            if isinstance(audio, bytes):
                if len(audio) % 2:
                    raise ValueError('PCM16LE byte length must be even')
                waveform = np.frombuffer(audio, dtype='<i2').astype(np.float32) / 32768
            else:
                waveform = np.asarray(audio)
                if not np.issubdtype(waveform.dtype, np.floating):
                    raise ValueError('Array audio must be normalized floating point PCM')
                waveform = waveform.astype(np.float32)
        if waveform.ndim != 1 or not waveform.size or not np.isfinite(waveform).all():
            raise ValueError('Audio must be a nonempty finite mono waveform')
        if waveform.size > 16000 * 120:
            raise ValueError('Utterance exceeds the 120-second limit')
        if np.max(np.abs(waveform)) > 1.01:
            raise ValueError('Floating point PCM must be normalized to [-1, 1]')
        return waveform

    def transcribe_sync(self, audio: AudioInput, locale: str = 'en', *,
                        auto: bool = False, state: LanguageState | None = None,
                        sample_rate: int = 16000) -> STTResult:
        validate_locale(locale)
        started = time.perf_counter()
        if auto and state is None:
            raise ValueError('Automatic mode requires a per-session LanguageState')
        with _LOCK:
            import mlx.core as mx
            from mlx_whisper.audio import log_mel_spectrogram, pad_or_trim, N_FRAMES
            whisper = importlib.import_module('mlx_whisper.transcribe')
            waveform = self._audio(audio, sample_rate)
            detected, probability = None, None
            output_locale = state.locale if auto else locale
            language = LOCALE_LANGUAGE[output_locale]
            if auto:
                model = whisper.ModelHolder.get_model(str(self.model_path), mx.float16)
                mel = log_mel_spectrogram(waveform, n_mels=model.dims.n_mels)
                mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
                _, probabilities = model.detect_language(mel)
                detected = max(probabilities, key=probabilities.get)
                probability = float(probabilities[detected])
                language = detected
            # Bias toward the detected language when supported; response locale
            # remains independently guarded by the two-turn switching rule.
            prompt_locale = ({'en': 'en', 'ja': 'ja', 'yue': 'yue'}.get(language)
                             or (state.chinese_locale if auto and language == 'zh'
                                 else output_locale if language == 'zh' and output_locale.startswith('zh')
                                 else 'zh-CN' if language == 'zh' else None))
            prompt = self.initial_prompt(prompt_locale) if prompt_locale else ''
            decoded = whisper.transcribe(
                waveform, path_or_hf_repo=str(self.model_path), language=language,
                task='transcribe', initial_prompt=prompt or None, verbose=None,
                condition_on_previous_text=False, temperature=0.0,
            )
            if auto:
                output_locale = state.observe(detected, probability)
            raw = decoded['text'].strip()
            script_locale = state.chinese_locale if auto and language == 'zh' else output_locale
            text = normalize_script(raw, script_locale)
            from server.normalize.codes import parse_spoken_code
            code = parse_spoken_code(text, output_locale)
            return STTResult(text=text, text_raw=raw, locale=output_locale,
                             lang_detected=detected, lang_prob=probability,
                             language_mode='auto' if auto else 'specified',
                             language_used=language,
                             elapsed_ms=(time.perf_counter() - started) * 1000,
                             audio_duration_s=len(waveform) / 16000,
                             initial_prompt=prompt, reservation_code=code)

    async def transcribe(self, audio: AudioInput, locale: str = 'en', *,
                         auto: bool = False, state: LanguageState | None = None,
                         sample_rate: int = 16000) -> STTResult:
        # Mutate session state only after await succeeds, so a cancelled turn
        # cannot switch the next turn's response language in the background.
        state_copy = replace(state) if state else None
        result = await asyncio.get_running_loop().run_in_executor(
            _EXECUTOR, partial(self.transcribe_sync, audio, locale, auto=auto,
                               state=state_copy, sample_rate=sample_rate))
        if auto and state is not None:
            state.locale, state.pending_language, state.consecutive = (
                state_copy.locale, state_copy.pending_language, state_copy.consecutive)
        return result
