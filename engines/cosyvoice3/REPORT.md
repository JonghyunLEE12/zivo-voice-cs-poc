# T4 CosyVoice3 엔진 구현 및 검증

작성: 2026-10-30, Apple M4 Pro / macOS, Python 3.10.20.
상태: **완료** — 설계 9장 T4의 5개 언어 non-silent WAV 스모크와 HTTP 계약 검증 통과.

## 변경 파일

- `engines/cosyvoice3/serve.py`: 공통 FastAPI 계약, 8102, `--device auto|mps|cpu`, lazy load, idle unload, bounded queue, disconnect cancellation, serialized inference.
- `engines/cosyvoice3/backend.py`: T1 모델/참조 매니페스트, 로컬 모델 전용, 제로샷 참조 전사, yue Guangdong 지시, 토큰/청크 중단, 24kHz PCM16LE.
- `engines/cosyvoice3/vendor/`: PR #1869 head `0cb2c1e3334c177b62685e8dbef687b971b85687`, Matcha `dd9105b34bf2be2230f4aa1e4769fb586a3c824e`, 원 라이선스, SOURCE.json 및 변경 설명.
- `engines/cosyvoice3/pyproject.toml`, `uv.lock`, `uv.toml`: 추론 의존성과 재현 가능한 lock, 구형 패키지 build dependency 조정.
- `engines/cosyvoice3/test_server.py`, `probe.py`, `check_stream.py`, `README.md`, `REPORT.md`.
- `engines/cosyvoice3/artifacts/`: 5개 언어 WAV+JSON, CPU probe WAV, streaming 검증 JSON. 실행/설치 로그는 같은 task 디렉터리.
- `docs/DEVIATIONS.md`: T4 변경 한 줄 추가. 다른 워커의 코드/공용 계약/모델/참조 파일은 수정하지 않음.

## 실행 명령과 실제 결과

프로젝트 루트에서 서버:

```sh
cd engines/cosyvoice3
uv sync --locked --offline
uv run python serve.py --device mps --port 8102 --lazy --idle-unload 600
```

`uv sync --locked --offline`: exit 0, 144 packages resolved / 125 packages checked.

별도 터미널, 같은 디렉터리에서 각 언어 스모크:

```sh
for locale in en zh-CN zh-TW ja yue; do
  uv run python ../../scripts/smoke_tts.py --engine cosyvoice3 --locale "$locale" --output "$PWD/artifacts/cosyvoice3-$locale.wav"
done
```

5/5 통과. 전부 mono / 24000 Hz / PCM16. `X-Synth-Ms`와 Content-Type 검증 통과.
영어는 최초 lazy load를 포함한 cold request이고 나머지는 모델이 로드된 상태이다.

| 언어 | 오디오 초 | X-Synth 초 | RMS | peak | 결과 |
|---|---:|---:|---:|---:|---|
| en | 2.36 | 18.730 | 0.001296 | 0.007965 | 통과 |
| zh-CN | 3.68 | 8.447 | 0.011367 | 0.152649 | 통과 |
| zh-TW | 4.00 | 7.087 | 0.010361 | 0.110077 | 통과 |
| ja | 1.52 | 7.423 | 0.001693 | 0.012177 | 통과 |
| yue | 3.12 | 7.924 | 0.013867 | 0.123322 | 통과 |

`GET /v1/info`: engine=cosyvoice3, model_id=FunAudioLLM/Fun-CosyVoice3-0.5B-2512,
locales=[en, zh-CN, zh-TW, ja, yue], supports_streaming=true,
native_sample_rate=24000, load_ms=9724.151.
5개 언어 완료 시 max observed memory=5,819,056,128 bytes.
메모리는 RSS와 MPS driver allocation 중 큰 값의 관측 최대치이며 두 값을 더하지 않는다.

```sh
uv run python check_stream.py
```

실제 HTTP streaming: 7.20초 / 345,600 bytes, 8개 수신 block(네트워크 분할 포함),
TTFA=5958.694ms, 전체=19069.832ms.
첫 block 뒤 연결을 끊은 후 active_requests=0 및 cancelled_requests 증가까지 78.196ms.
이 값은 한 번의 관측값이며, in-flight native kernel의 즉시 선점은 보장하지 않는다.
취소 후 `POST /v1/warmup`: ok=true, 5036.281ms, 2.36초 오디오.
후속 일반 요청: HTTP 200, 113,280 bytes, X-Synth-Ms=5011.561.
스트리밍/후속 요청까지 관측 최대 메모리=6,434,439,168 bytes.

```sh
uv run python -m pytest -q test_server.py
```

**4 passed, 2 warnings in 0.85s**. 계약/입력 오류/503, 취소 후 잠금 해제와 후속 요청,
참조 경로 제한/zh-TW 공유, 활성 요청 중 idle unload 금지를 검증했다.
경고는 상위 pytest asyncio 설정 및 Starlette의 httpx deprecation이다.

독립 원본 모델 probe:

```sh
COSYVOICE_DEVICE=mps uv run python probe.py
COSYVOICE_DEVICE=cpu uv run python probe.py
```

- MPS: load 8.679s, 오디오 4.88s, peak 0.015158, 합성 19.162s, exit 0.
- CPU: load 4.880s, 오디오 4.04s, peak 0.011041, 합성 11.701s, exit 0.
- 원본 probe는 서버와 별개이며 같은 seed로 통제한 반복 벤치가 아니므로 CPU/MPS 속도 우열 근거로 쓰면 안 된다.
- CPU는 영어 1문장만 검증했고, T4 필수 5개 언어는 MPS 서버에서 검증했다.

## 구현 결정과 중단/재개 기록

[PR #1869](https://github.com/FunAudioLLM/CosyVoice/pull/1869)의 고정 head를 사용했다.
PR의 CPU float64 f0 우회를 유지하고 ONNX speech tokenizer는 명시적 CPU provider로 설정했다.
`COSYVOICE_DEVICE` override를 추가하여 CPU 선택을 실제로 적용한다.
upstream detached LLM thread를 쓰지 않고 취소 가능한 token loop와 upstream token2wav를 결합한다.
waveform은 모델 native 24kHz이므로 별도 resample이 필요하지 않으며 PCM16LE로 변환한다.
서버 내부 정규화는 끄고 앱의 raw/lex/full 입력을 보존한다.

설치 중 scipy 1.15.3의 macOS Mach-O 로딩 오류로 scipy 1.12.0을 고정했고,
구형 whisper/pyworld/lightning의 pkg_resources 요구 때문에 setuptools<81을 적용했다.

시작 디스크는 약 11GiB였다. 진행 중 167MiB로 떨어져 5GB 기준에 따라 중단하고
escalation(msg_50cc837f8c91) 및 ask(msg_da51b4524c1f)를 보냈다.
T4 모델 프로세스는 당시 wget 누락으로 이미 종료되어 있었고 추가 합성은 하지 않았다.
이후 여유가 21GiB로 회복된 것을 여러 번 확인하고 원래 승인 범위의 단일 probe를 재개했으며,
합성 검증 종료 시 약 136GiB였다. 공간 변화의 원인은 이 task에서 확인하지 않았다.
추가 모델 다운로드는 **0 bytes**; 기존 T1 snapshot만 사용했다.
Orca 런타임 재시작으로 ask 연결이 끊긴 뒤 동일 message ID로 재개했고,
중단 사유가 해소된 사실과 실제 성공 결과를 코디네이터에게 status로 전달했다.

## 한계와 남은 작업

- T4 완료 기준의 미완 항목은 없음. 서버는 검증 후 종료하여 모델 메모리를 반환했다.
- **대만 억양 참조 없음**: zh-TW는 cmn_hans_cn 공통 참조이며 억양 적합성은 미확인.
- 무음 아님만 검사했으며 발음 정확도/자연스러움/화자 일치도는 평가하지 않았다. T7/T8에서 수행한다.
- 영어/일본어 공통 참조의 녹음 레벨이 낮아 출력 레벨도 낮다. 원본은 바꾸지 않았으며 블라인드 공통 후처리는 후속 task 범위다.
- 측정은 concurrent worker 환경의 실행 스모크다. T7의 1회 워밍업+3회 반복 및 단일 엔진 통제 벤치를 대체하지 않는다.
- MPS 실패 시 자동으로 백엔드를 바꾸지 않고 HTTP 503을 내며 `--device cpu`로 재기동한다. 측정 경로 혼합을 막기 위해 명시적으로 선택한다.
