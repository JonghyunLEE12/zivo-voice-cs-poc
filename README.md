# ZIVO 외국인 음성 상담 PoC

Apple Silicon에서 API 키 없이 Whisper → Ollama → TTS 상담을 실행하고, 네 TTS 엔진의 발음·속도 및 익명 청취 결과를 비교합니다. 실예약 변경은 하지 않습니다. [최종 결과](reports/FINAL-REPORT.md), [설계](docs/DESIGN.md), [변경 사유](docs/DEVIATIONS.md)를 함께 참고하세요.

## 저장소 안내

토스 AIOC 발표에서 본 Speech Tokenizer 병목(학습 데이터 분포, 이산화, 영어 semantic teacher)이 영어, 중국어(본토/대만), 일본어 CS에도 보이는지 공개 TTS 모델로 확인한 PoC입니다. 모델은 학습하지 않았고, 여기 있는 코드는 엔진 서버, 정규화와 고유명사 사전, LLM 상담 에이전트, 실시간 WebSocket 루프, 브라우저 데모, 블라인드 비교와 벤치입니다.

| 엔진 | 역할 | 라이선스 |
|---|---|---|
| VoxCPM2 | weak 병목 1 (연속 잠재, teacher 없음) | Apache-2.0 |
| Fun-CosyVoice3-0.5B | weak 병목 2 (다국어 지도학습 토크나이저) | Apache-2.0 |
| Qwen3-TTS-12Hz-1.7B | baseline (RVQ + 영어 WavLM teacher) | Apache-2.0 |
| Kokoro-82M | 대조군 (경량) | Apache-2.0 |

- 저장소에 없는 것: 모델 가중치(`models/`), 가상환경, 블라인드/벤치 음성, 브라우저 프로필, 테스트 임시 폴더. 모델은 아래 `fetch_models.py`로 받고, 음성은 `gen_blind_set.py`와 `bench_tts.py`로 다시 만듭니다.
- 참조 음성 `data/refs/`는 Google FLEURS(CC-BY-4.0) 발화이며 출처는 `data/refs/MANIFEST.json`에 있습니다.
- CosyVoice3의 Apple Silicon(MPS) 실행은 업스트림 미병합 PR #1869를 고정 커밋으로 vendor한 사본을 씁니다(`engines/cosyvoice3`).
- 보고서의 일부 로그에는 작업 당시 로컬 절대 경로가 남아 있습니다. 아래 명령의 경로는 클론한 위치로 바꿔 쓰면 됩니다.
- 정리 글: https://jonghyunlee12.github.io/derek_lab/blog/multilingual-voice-cs-poc/

## 1. 새 터미널 환경 준비

M4 Pro / RAM 48GB에서 검증했습니다. `uv`, `pnpm`, `ffmpeg`, Ollama가 필요합니다. Ollama에 기존 `qwen3.5:35b-a3b`가 있어야 하며 아래 명령은 새 Ollama 모델을 받지 않습니다. 앱이 꺼져 있으면 Ollama 앱을 먼저 실행하거나 별도 터미널에서 `ollama serve`를 실행합니다.

```sh
cd zivo-voice-cs-poc   # 클론한 위치
source scripts/env.sh
uv sync --locked
uv run python scripts/check_env.py
uv run python scripts/fetch_models.py --plan
uv run python scripts/fetch_models.py --download --only stt,mlx,cosyvoice3
uv run python scripts/fetch_refs.py
(cd engines/mlx_engines && uv sync --locked)
(cd engines/cosyvoice3 && uv sync --locked)
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
```

메인/MLX는 Python 3.12, CosyVoice3는 Python 3.10입니다. 루트 package.json이 pnpm 10.34.5를 고정하며 Corepack은 첫 실행 때 해당 버전을 프로젝트 캐시에 받습니다. `source scripts/env.sh`는 다운로드·런타임·임시 캐시를 프로젝트 안으로 지정하므로 **새 터미널마다** 실행하세요. CosyVoice3의 고정 vendor 소스는 포함되어 있습니다. Kokoro 일본어 사전이 없는 새 환경에서만 `(cd engines/mlx_engines && uv run python -m unidic download)`를 실행합니다. 기존 검증 환경에서는 추가 사전/espeak-ng 설치가 필요 없었습니다.

`--plan`은 실제 파일 크기와 20GB 상한을 확인합니다. 다운로드는 중단 후 같은 명령으로 재개하며 크기·LFS 해시를 확인합니다. 모델은 `models/hub` 한 곳에만 보관하고 `models/paths.json`으로 찾습니다. Smart Turn v3.2 CPU도 기본 다운로드에 포함되며 기존 파일은 **8,679,182 bytes**입니다. 선택 Qwen3-ASR은 이번 PoC에서 사용하지 않습니다. 모델·환경 다운로드는 오래 걸릴 수 있으므로 해당 터미널을 유지하세요.

클로닝 세 엔진은 언어별 동일 FLEURS 참조를 사용합니다. 출처·CC-BY-4.0·원문·해시는 `data/refs/MANIFEST.json`에 있습니다. **대만 억양 참조 없음**: zh-TW는 본토 Mandarin 참조를 공유하며 Kokoro는 프리셋 화자입니다.

## 2. 엔진 하나와 메인 서버 실행

**48GB에서도 네 TTS 엔진을 동시에 로드하지 마세요.** LLM과 함께 사용하는 실시간 기본값은 Kokoro입니다. 단독 TTS 벤치 최대 관측 메모리는 Kokoro 2.65GB, Qwen3-TTS 8.96GB, VoxCPM2 9.02GB, CosyVoice3 13.89GB로, 설계 초기의 대형 엔진 5~6GB 추정보다 큽니다. `--lazy`는 첫 요청 때 로드할 뿐 여러 모델의 동시 사용을 안전하게 만들지 않습니다.

터미널 A, Kokoro만 실행:

```sh
cd /Users/jongram/Desktop/global_alpha/test/zivo-voice-cs-poc
source scripts/env.sh
uv run --project engines/mlx_engines python engines/mlx_engines/serve.py --engine kokoro --port 8104 --lazy --idle-unload 600
```

터미널 B, 메인 서버:

```sh
cd /Users/jongram/Desktop/global_alpha/test/zivo-voice-cs-poc
source scripts/env.sh
uv run uvicorn server.app:app --host 127.0.0.1 --port 8000
```

엔진을 바꿀 때 통화를 종료하고 터미널 A를 Ctrl+C로 닫은 뒤 아래 중 **하나만** 실행합니다. 브라우저 선택만 바꾸어 서버가 자동 실행되지는 않습니다.

```sh
# VoxCPM2
uv run --project engines/mlx_engines python engines/mlx_engines/serve.py --engine voxcpm2 --port 8101 --lazy --idle-unload 600
# Qwen3-TTS
uv run --project engines/mlx_engines python engines/mlx_engines/serve.py --engine qwen3tts --port 8103 --lazy --idle-unload 600
# CosyVoice3: MPS 실패 시 --device cpu로 명시적으로 재시작
(cd engines/cosyvoice3 && uv run python serve.py --port 8102 --device mps --lazy --idle-unload 600)
```

| 서비스 | 포트 | 확인 |
|---|---:|---|
| 메인 서버 | 8000 | `/`, `/ws/call`, `/blind`, `/blind/results`, `/report` |
| VoxCPM2 | 8101 | `/v1/info` |
| CosyVoice3 | 8102 | `/v1/info` |
| Qwen3-TTS | 8103 | `/v1/info` |
| Kokoro | 8104 | `/v1/info` |
| Ollama | 11434 | `/api/ps` |
| T11 자동 검증 전용 | 8011 | 검증 스크립트가 시작·정리 |

자원 중단 기준은 `sysctl -n kern.memorystatus_vm_pressure_level`이 **4**, `sysctl vm.swapusage`의 시작 대비 사용 swap **+4GiB 초과**, 디스크 여유 **10GiB 미만**입니다. level 2와 free%는 관찰값입니다. 수동 실행 중 기준에 도달하면 메인·엔진을 Ctrl+C로 종료하고 `ollama stop qwen3.5:35b-a3b`를 실행하세요. 자동 벤치/실모델 검증 스크립트는 가드를 적용하고 소유 프로세스를 정리합니다. 과거 swap 잔량 자체를 새 증가량으로 판단하지 않습니다.

## 3. 상담 데모

<http://localhost:8000/>에서 언어, 실행 중인 엔진, 정규화 `full`을 고르고 마이크를 시작합니다. 헤드폰을 권장합니다. 세션 준비 중 첫 안내 음성 합성·모델 로드로 시간이 걸릴 수 있습니다. 답변 중 말하면 재생을 멈춥니다. Smart Turn은 `VAD + Smart Turn v3.2`를 선택하고 설정 적용으로 켭니다. 미완료 판정은 최초 발화 수신부터 최대 1.5초 내 추가 발화를 병합한 뒤 STT로 보냅니다. 모델이 없으면 미적용 오류가 표시되므로 `VAD만`으로 실행할 수 있습니다.

예시: 영어로 “Please find my booking Z V four eight two one, surname Carter.”를 말합니다. 본토 중국어 예약은 `ZV-7305 / 王`, 대만은 `ZV-5196 / 陳`, 일본어는 `ZV-2610 / 佐藤`입니다. 기준일은 `DEMO_TODAY=2026-11-01`이며 정확한 시간까지 고정하려면 서버 시작 전에 `export DEMO_NOW=2026-11-01T12:00:00+09:00`를 지정합니다.

마이크 없는 확인은 <http://localhost:8000/?dev=1> → WAV 모드 연결 → `reports/t9/inputs/en.wav` 파일 선택으로 가능합니다. 다른 세 언어 WAV도 같은 폴더에 있습니다. 이 입력은 VAD를 우회하며 자연 고객 발화가 아닙니다. 화면 첫 음성 지연은 안내음 포함, 답변 지연은 실제 답변 기준입니다. 현재 실모델 브라우저 첫 재생은 2.97~5.36초로 목표 1.5초를 넘습니다. 예약번호·이름 인식과 LLM 상담원 도구 누락도 남아 있습니다.

통화 종료 후 <http://localhost:8000/report>에서 통화 길이, 3분 이상 비율, 끼어들기 발생 콜 비율, 콜당 고객 문자 수, 턴 수, 첫 재생 p50/p90 및 상담원 전환을 봅니다. 새 세션만 시작·종료 로그가 있고 과거 턴 로그에서 통화 길이를 추정하지 않습니다. JSON은 `/api/report/sessions`, 원자료는 `data/logs/{turns,sessions}-YYYYMMDD.jsonl`입니다. 공개 서버로 노출하거나 실제 고객 개인정보를 입력하지 않는 로컬 데모입니다.

## 4. 블라인드 페이지

기존 `data/blind/manifest.json`과 `data/blind/audio/`가 있으면 TTS 서버 없이 메인 서버만으로 <http://localhost:8000/blind>를 실행합니다. 별칭·모국어·청취 장치를 입력하고 A와 B를 둘 다 끝까지 들은 뒤 투표합니다. 공개 페이지와 `/blind/results`는 익명 Voice 번호를 사용합니다. 실제 엔진별 집계는 운영자 CLI에서만 공개합니다.

```sh
uv run python scripts/aggregate_blind.py --reveal
```

운영 세트는 full만 134개입니다. 사람의 원어민 청취 결과는 아직 수집되지 않았습니다. 테스트 fixture 10표를 사람 평가로 해석하지 마세요. 오디오를 새로 만들기 전 기존 manifest/audio/results를 함께 별도 보존하세요. 다음 명령은 엔진을 하나씩 자체 시작하며 수동 엔진 서버를 먼저 종료해야 합니다.

```sh
ollama stop qwen3.5:35b-a3b
uv run python scripts/gen_blind_set.py --engines kokoro,qwen3tts,voxcpm2,cosyvoice3 --locales en,zh-CN,zh-TW,ja,yue
uv run python scripts/finalize_blind_audio.py
uv run python scripts/check_blind_http.py --url http://127.0.0.1:8000
```

## 5. 벤치와 리포트 재생성

메인/엔진 수동 서버와 다른 모델 작업을 종료하고 전원에 연결합니다. 아래는 단독 TTS 벤치입니다. CosyVoice3는 승인된 축소 조건인 full 1회만 사용합니다. `--resume`은 기존 측정을 재사용하므로 완전히 새 측정이 필요하면 기존 날짜별 결과와 WAV를 먼저 보존하세요.

```sh
ollama stop qwen3.5:35b-a3b
uv run python scripts/bench_tts.py --engines kokoro,qwen3tts,voxcpm2 --locales all --repeats 3
uv run python scripts/bench_tts.py --engines cosyvoice3 --locales all --levels full --repeats 1 --resume
uv run python scripts/bench_tts.py --score --resume
engines/mlx_engines/.venv/bin/python scripts/score_kana.py --input reports/bench-20261030.json
uv run python scripts/report.py
uv run python scripts/report_sessions.py
```

가나 채점의 날짜 파일은 새 실행 날짜에 맞춥니다. `report.py`는 저장된 벤치 JSON에서 Markdown을 재생성하며 `/report`가 최신 날짜 보고서와 세션 집계를 읽습니다. 세션 CLI는 `reports/session-metrics.json`을 저장합니다. [최종 보고서](reports/FINAL-REPORT.md)는 출처를 붙인 해석 문서로 자동 재작성하지 않습니다.

T11 ON/OFF 실모델 재현은 기존 Ollama 모델 + Whisper + Kokoro만 사용합니다. 아래 플래그는 다른 작업과 모델 창을 독점했음을 확인한 후 사용합니다. 스크립트가 8011/8104 서버를 시작하고, 워밍업 제외, 4언어 각 ON/OFF 및 barge-in 1회를 실행한 뒤 종료·Ollama 언로드합니다. 가상 playback ACK이므로 실제 스피커 지연이 아닙니다.

```sh
uv run python scripts/validate_smart_turn.py --model-window-released
uv run python scripts/report_sessions.py
uv run python -m pytest tests -q --basetemp reports/readme-tests
```

이미 실행한 메인/엔진에 WAV 하나만 전송하려면:

```sh
uv run python scripts/ws_wav_client.py reports/t9/inputs/en.wav --locale en --engine kokoro --turn-detect vad+smartturn --output reports/manual-smart
```

T10 Chrome 검증은 `scripts/check_call_browser_live.py`와 [T10 보고서](reports/T10-REPORT.md), T7 조건과 발음 해석은 [T7/T8 보고서](reports/T7-T8-REPORT.md)에 있습니다. 종료 시 서버 터미널들을 Ctrl+C로 닫고 `ollama stop qwen3.5:35b-a3b`로 LLM 메모리를 해제합니다.
