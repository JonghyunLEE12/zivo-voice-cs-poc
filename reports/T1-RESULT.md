# T1 골격·환경·모델·참조 음성 완료 보고

실행일: 2026-10-30. 결과: **성공**. 작업 루트: `/Users/jongram/Desktop/global_alpha/test/zivo-voice-cs-poc`.

## 완료 기준과 실행 증거

| 기준 | 실행 명령 | 실제 결과 |
|---|---|---|
| 메인 uv 환경 | `uv sync` / `uv add 'mlx-whisper>=0.4' 'onnxruntime>=1.20'` | Python 3.12.13, 패키지 66개 |
| MLX 별도 uv 환경 | `cd engines/mlx_engines && uv sync` | Python 3.12.13, 패키지 117개 |
| CosyVoice 별도 uv 환경 | `cd engines/cosyvoice3 && uv sync` (프로젝트 내부 UV_PYTHON_INSTALL_DIR 지정) | Python 3.10.20, 패키지 38개 |
| 환경 검사 | `uv run python scripts/check_env.py --json` | 8/8 PASS: Python, Apple Silicon, ffmpeg, Ollama, uv, pnpm, 모델, 디스크 |
| 모델 계획 | `uv run python scripts/fetch_models.py --plan` | 설계 근사치 합계 16.93 GB / 실파일 16.943466 GB, 20 GB 이하 |
| 실제 다운로드 | `uv run python scripts/fetch_models.py --download --only stt,mlx,cosyvoice3` | 6개 저장소, 168개 파일, 16,943,466,035 bytes |
| 캐시 재실행·무결성 | 같은 `--download` 명령 재실행 | 누락 0 bytes, 168개 크기 일치, LFS 130개 SHA256 일치, 미완성 파일 0 |
| 안전장치 | `uv run pytest tests/test_fetch_models.py -q` | 7 passed in 0.11s: 20 GB, 기존 모델 합산, 디스크 여유, 부분 재개, 제외 패턴 |
| 참조 음성 | `uv run python scripts/fetch_refs.py` | 4개 원본 WAV + 전사 + 매니페스트, 10개 호환 심볼릭 링크 검증 |
| 웹 골격 | `cd web && pnpm install && pnpm build` | 성공, Vite 6.4.5, 3 modules, 빌드 402 ms |
| 일본어 사전 | `cd engines/mlx_engines && uv run python -m unidic download` | UniDic 3.1.0+2021-08-31, 추출 파일 합계 811,662,881 bytes |
| 엔진 환경 직접 검사 | MLX 배열 연산, `torch.backends.mps.is_available()`, `pyopenjtalk.g2p()`, `fugashi.Tagger()` | MLX `[2,3]`, MPS True, 일본어 가나 생성·9 토큰 분석 성공 |

Ollama는 기존 `qwen3.5:35b-a3b` 23,869,191,742 bytes를 조회만 했으며 새 모델을 받지 않았다. ffmpeg는 `/opt/homebrew/bin/ffmpeg`이다. `mlx-audio=0.6.3`, `mlx=0.32.4`, `mlx-whisper=0.4.3`, `onnxruntime=1.31.1`; CosyVoice bootstrap 환경의 `torch=2.8.0`, MPS 가용성은 True이며 실제 CosyVoice 합성 검증은 T4 범위다.

## 받은 모델

| 모델 키 | 파일 수 | bytes | GB (10^9) | 고정 revision |
|---|---:|---:|---:|---|
| whisper | 4 | 1,613,979,758 | 1.613980 | `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb` |
| voxcpm2 | 11 | 4,960,140,841 | 4.960141 | `a7fb80d9056983135a54ca3f71fcc5a637ccddf5` |
| cosyvoice3 | 16 | 5,427,042,708 | 5.427043 | `29e01c4e8d000f4bcd70751be16fa94bf3d85a18` |
| qwen3tts | 14 | 4,544,212,739 | 4.544213 | `a6eb4f68e4b056f1215157bb696209bc82a6db48` |
| kokoro | 122 | 389,410,807 | 0.389411 | `a71e4d38b236d968966a2002c4c895dbd12b1c3c` |
| smartturn | 1 | 8,679,182 | 0.008679 | `f766f81d3cfdf7737ac64aad813d91bbfd56bf93` |

모든 실파일은 `models/hub/models--*/blobs`에 한 번씩 저장되고 snapshot은 링크다. 엔진이 바로 사용할 snapshot 상대 경로는 `models/paths.json`, 파일별 크기·SHA256·allow_patterns는 `reports/models-plan.json`에 있다. `llm.pt`만 받았으며 `llm.rl.pt`, `speech_tokenizer_v3.batch.onnx`, `flow.decoder.estimator.fp32.onnx` 3개는 부재를 검증했다. Smart Turn CPU v3.2는 필수여서 `--only stt,mlx,cosyvoice3`에도 포함한다. 선택 Qwen3-ASR 및 대체 모델은 받지 않았다.

HF 캐시 부속 로그·메타데이터까지 포함한 실제 파일 합계는 16,968,107,975 bytes (16.968108 GB)이며 20 GB 이하이다. 최종 디스크 여유는 11,512,614,912 bytes (11.513 GB)이다. 후속 작업에서 추가 모델을 받기 전 동일한 상한 검사를 사용해야 한다.

`@ricky0123/vad-web=0.0.30`이 포함한 Silero ONNX는 v5 2,327,524 bytes와 legacy 1,807,522 bytes, 합계 4,135,046 bytes이다. UniDic 외에 pyopenjtalk 0.4.1의 Open JTalk 사전도 프로젝트 MLX 가상환경에 설치했고 `ニホンゴノオンセーヲカクニンシマス。`를 실제 출력했다. brew espeak-ng는 설치하지 않았다.

## FLEURS 공통 참조

출처: [Google FLEURS](https://huggingface.co/datasets/google/fleurs), 테스트 split, [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/), attribution Google FLEURS / FLEURS contributors. 원본 archive와 metadata URL 및 원문 전사는 `data/refs/MANIFEST.json`에 있다. 전체 데이터셋을 저장하지 않고 tar.gz 응답을 스트리밍해 필요한 원본 WAV만 남겼다.

| locale | config | 초 | 20 ms RMS 90/10분위 대비 dB | 클리핑 비율 | 원본 파일 |
|---|---|---:|---:|---:|---|
| en | en_us | 11.46 | 29.02 | 0.0 | `10167324587744183095.wav` |
| zh-CN | cmn_hans_cn | 8.46 | 25.22 | 0.0 | `10388181945260949729.wav` |
| ja | ja_jp | 11.76 | 31.08 | 0.0 | `10401481013809636276.wav` |
| yue | yue_hant_hk | 10.26 | 33.94 | 0.0 | `10115432746173245930.wav` |

4개 모두 여성·16 kHz·모노, 원본 float WAV 그대로다. 앞뒤 절단, 리샘플, 음량 변경을 하지 않았다. 영어/일본어 원본 레벨이 낮으므로 단순 peak만으로 탈락시키지 않고 RMS > 0.0001, 20 ms 프레임 RMS 분위 대비 ≥20 dB, 클리핑 <0.01%로 선별했다. 이 수치는 객관적인 선별값이며 주관적 청취 평가를 대신하는 SNR 측정값은 아니다.

**대만 억양 참조 없음.** `zh-TW`는 `cmn_hans_cn` 참조를 `zh-CN`과 공유한다. 세 클로닝 모델은 같은 언어에서 같은 참조를 사용하며 Kokoro는 여성 프리셋을 사용한다. 설계 호환 경로 `data/ref_voices/{locale}/ref.wav`, `ref.txt`는 `data/refs/`로 연결한 심볼릭 링크다.

설치한 로컬 Whisper로 4개 참조를 다시 인식했다. 실행시간은 en 4.464s, zh-CN 0.674s, ja 0.720s, yue 0.784s이며 첫 호출에 로딩 시간이 포함된다. 상세 원문/인식문은 `reports/t1-ref-asr.json`; 영어 고유명사 및 광둥어 일부 문자 등 차이가 있어 원문 전사를 ASR 출력으로 덮어쓰지 않았다. 이 확인은 T5/T7 벤치마크 결과가 아니다.

## 생성·변경 파일

- 프로젝트: `.gitignore`, `.python-version`, `pyproject.toml`, `uv.toml`, `uv.lock`, `README.md`.
- 실행·검증: `scripts/check_env.py`, `scripts/fetch_models.py`, `scripts/fetch_refs.py`, `scripts/env.sh`, `tests/test_fetch_models.py`.
- 서버 골격: `server/config.py`, 서버 하위 디렉터리·패키지 초기화 파일 및 빈 디렉터리 마커. 병행 작업자가 구현한 정규화/LLM 파일은 T1 산출물로 계산하지 않았다.
- 엔진: `engines/PROTOCOL.md`, MLX/CosyVoice 각각 `pyproject.toml`, `.python-version`, `uv.toml`, `uv.lock`, 독립 `.venv`와 vendor 디렉터리 골격.
- 웹: `web/package.json`, `.npmrc`, `pnpm-lock.yaml`, `tsconfig.json`, `index.html`, `src/main.ts`, call/blind/report/shared 디렉터리 골격.
- 참조: `data/refs/MANIFEST.json`, en/zh-CN/ja/yue 각각 `ref.wav`·`ref.txt`, `data/ref_voices/` 호환 링크 10개.
- 다운로드: `models/hub/`, `models/paths.json` (ignore 대상).
- 문서·증거: `docs/DEVIATIONS.md`, `reports/models-plan.json`, `reports/t1-acceptance.json`, `reports/t1-env.json`, `reports/t1-ref-asr.json`, `reports/t1-*.log`, `reports/t1-*.err`, 본 보고서.

## 차이와 남은 범위

T1 필수 완료 항목의 미완/실패는 없다. 초기 TSV 따옴표 파싱 문제는 QUOTE_NONE으로, 과도한 원본 음량 기준은 프레임 RMS 대비 검사로 수정했고 최종 실행은 성공했다. 설치가 끝나기 전 실행한 첫 웹 빌드는 tsc 미생성으로 실패했으나 설치 완료 후 재실행은 통과했다.

설계 표의 16.93 GB는 근사치이고 현재 정확한 합계는 0.013466 GB 더 크므로 둘을 별도로 출력한다. [CosyVoice 공식 README](https://github.com/FunAudioLLM/CosyVoice#install)의 Python 3.10 지정과 참조 경로 변경은 `docs/DEVIATIONS.md`에 기록했다. MPS 패치 vendor 반입·CosyVoice 추가 의존성과 실제 합성은 T4, MLX HTTP 합성 서버는 T3, STT 서비스는 T5, 실제 UI/파이프라인은 후속 task 범위다. 현재 web 페이지는 골격이며 상담 데모가 구현됐다고 주장하지 않는다.
