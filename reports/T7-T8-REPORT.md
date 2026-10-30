# T7 / T8 구현 및 검증 리포트

**완료 — 사용자 결정에 따른 CosyVoice3 축소 측정을 반영했다.** T7은 723개 측정 행을 보존하고 629개를 주분석에 사용했으며, Whisper 252개 WAV와 일본어 가나 CER 56개를 채점했다. T8은 full 기준 134/134개 오디오를 생성·검증했고 FastAPI `/blind`, `/blind/results` 및 `/report`가 동작한다. 본 작업의 모델 프로세스는 모두 종료했고 코디네이터에 모델 창을 반환했다(`msg_29498d262781`).

세부 결과: [전체 벤치 표](bench-20261030.md) · [오디오 검증](blind-audio-validation.json) · [변경 파일 목록](t7-t8-files.json)

## 완료 기준과 실제 결과

| 기준 | 실행 / 증거 | 결과 |
|---|---|---|
| T7 엔진 × 언어의 모든 측정 칸 | `scripts/report.py`, `bench-20261030.md` | 4엔진 × 5언어 = 20행; 지원 언어는 실측값, 미지원·선택 미설치는 명시적 사유 |
| 워밍업 및 반복 | `bench-20261030.json` | Kokoro 192, Qwen3-TTS 192, VoxCPM2 210행; 각 문장 raw/full × 3회 |
| CosyVoice3 사용자 결정 | `bench-cosy-reduced.log`, `sampling_policy` | full 문장당 1회 × 35문장; 기존 full repeat=0 19개 재사용 + 16개 추가 |
| 기존 Cosy 행 보존 | `bench-20261030.json` | Cosy 129행 보존; 35개 full repeat=0만 주분석, 나머지 94행은 기록 보존 |
| T7 발음 채점 | `bench-whisper.log`, `bench-kana.log` | Whisper 252/252, 일본어 가나 CER 56/56; 채점 예외 0 |
| T8 매니페스트 | `data/blind/manifest.json` | 134개: Kokoro 32, Qwen 32, Vox 35, Cosy 35; 생성 실패 0, 실패 재합성 없음 |
| T8 오디오 형식·후처리 | `blind-audio-validation.json` | 전부 24kHz / mono / PCM16, 앞뒤 150ms 무음, SHA-256 일치, 메타데이터 없는 WAV 헤더 |
| T8 라우드니스 | 같은 검증 JSON | 최종 −20.29~−19.86 LUFS, 최대 true peak −1.0dBTP |
| 블라인드 HTTP | `blind-http-acceptance.json` | 실제 데이터의 12개 응답 검사; 엔진 이름·오디오 파일명·ETag 노출 0; 비공개 경로/잘못된 토큰 404 |
| 실제 브라우저 재생 | `blind-live-acceptance.json`, `blind-live.png` | 실제 생성 A/B를 끝까지 재생; A만 종료 시 비활성, 둘 다 종료 시 투표 활성; 운영 투표 추가 0 |
| 브라우저 네트워크 | `blind-live-network.json` | 실제 세트 검증의 9개 네트워크 기록에서 엔진 식별자 0 |
| 10표 저장·집계 일치 | `blind-browser-acceptance.json`, `blind-results.png` | 별도 합성음 fixture에서 실제 브라우저로 10표 저장, `aggregate.py` 결과와 일치 |
| 부호검정·방향 보정 | `tests/test_blind.py` | 6승/2무/2패 → 동점 제외 승률 0.75, 양측 p=0.2890625; A/B 반전에도 동일 |
| 자동 검증 | `t7-t8-regression.log`, `t7-t8-tests.log` | 전체 204 passed; 최종 T7/T8 전용 12 passed; Vite/TypeScript 빌드 성공 |

10표는 `reports/blind-browser-fixture-final`의 **0.35초 합성음 자동화 테스트**다. 운영 세트에는 사람의 평가가 아직 0표이며, 자연스러움이나 대만 억양의 우열을 이 테스트 표로 판단하지 않았다. 이전 개발 fixture도 `reports/` 아래에 분리되어 있다.

## 속도와 메모리 실측

RTF = 합성 시간 / 오디오 길이. 아래 RTF는 언어별 full 중앙값이다. CosyVoice3는 **축소 측정(문장당 표본 1)**이며, raw/full 주비교와 3회 반복 p90을 산출하지 않았다. 다른 세 엔진의 중앙값·p90, 로드 시간, 언어별 메모리와 발음 값은 `bench-20261030.md`에 있다.

| 엔진 | en RTF | zh-CN | zh-TW | ja | yue | 언어별 TTFA 중앙값 범위 | 최대 관측 메모리 |
|---|---:|---:|---:|---:|---:|---|---:|
| Kokoro | 0.045 | 0.044 | 0.044 | 0.043 | 미지원 | 268–324ms, 문장 전체 | 2.65GB |
| Qwen3-TTS | 0.580 | 0.569 | 0.569 | 0.584 | 미지원 | 299–341ms, 스트리밍 | 8.96GB |
| VoxCPM2 | 1.794 | 1.753 | 1.805 | 1.799 | 1.800 | 9,240–11,229ms, 문장 전체 | 9.02GB |
| CosyVoice3 | 2.634 | 2.300 | 2.213 | 2.534 | 2.829 | 3,752–4,554ms, 스트리밍 | 13.89GB |

엔진이 보고한 최신 로드 시간: Kokoro 1,194ms, Qwen 4,083ms, Vox 2,721ms, Cosy 10,264ms. 엔진 계약에 따라 import/모델 초기화 시간이며 HTTP supervisor 시작 시간은 제외한다. 메모리는 RSS와 backend allocation 중 큰 값의 관측 최대치이며 둘을 더하지 않는다. 위 최대치는 모든 보존 행을 기준으로 했고, Cosy의 재사용 행에는 축소 이전 장시간 배치의 누적 high-watermark가 남아 있다.

이 수치는 Ollama를 내린 단독 TTS 측정이다. T9의 STT/LLM/TTS 전체 턴 지연과는 별도다. 장시간 배치에서 Vox 9.02GB, Cosy 13.89GB가 관측되어 설계의 대형 엔진 약 5–6GB 예상보다 컸으며, T9 코디네이터에 전달했다(`msg_ed7004e6e8f7`).

## 발음 측정 결과

Whisper의 full 결과(문장별 오류율 평균, en은 WER, 나머지는 CER):

| 엔진 | en | zh-CN | zh-TW | ja | yue |
|---|---:|---:|---:|---:|---:|
| Kokoro | 6.2% | 4.7% | 5.7% | 10.0% | 미지원 |
| Qwen3-TTS | 8.7% | 2.1% | 7.2% | 7.0% | 미지원 |
| VoxCPM2 | 7.9% | 1.2% | 3.4% | 7.3% | 24.4% |
| CosyVoice3 (축소) | 9.5% | 2.4% | 3.4% | 44.9% | 37.3% |

- 영어 고유명사 적중률은 Vox raw 6/9 → full 9/9, Qwen 8/9 → 9/9, Kokoro 9/9 → 9/9였다.
- full의 효과는 언어·엔진별로 달랐다. 일본어의 엄격한 display/aliases 적중률은 Vox 5/6 → 2/6, Kokoro 6/6 → 2/6, Qwen 3/6 → 3/6이었다.
- 일본어 `景福宮駅` full 음성은 Whisper가 Vox에서 `京北区駅`, Kokoro에서 `京牧訓駅`으로 적었다. 지명 독음을 다른 한자로 전사하는 현상과 실제 발음 문제를 이 지표만으로 분리할 수 없다. `pyopenjtalk`도 원문 한국 지명 한자를 일본어 독음으로 잘못 읽을 수 있다.
- Cosy 일본어 full의 높은 CER은 원어민 검토가 필요한 관측이다. 예를 들어 `集合場所は景福宮駅の5番出口です。`에 대한 Whisper 원문은 `地公罵送は京牧群へ 金の5番で 口です`였다. 원본 WAV와 전사는 보존했다.
- 합성 실패율은 HTTP 오류, RMS/peak 무음 검사, 길이 휴리스틱으로 계산했다. 발음 오류는 WER/CER 및 고유명사 열에서 별도로 기록했다.

참조는 `display_text`를 유지하고 STT 원문에 소문자/문장부호 제거, 중국어 t2s, 숫자·코드 역정규화를 적용했다. `text_raw`, 문자 체계 정리 후 `text`, 비교용 canonical 텍스트, 일본어 가나 결과를 JSON에 남겼다. Whisper의 사전 prompt 주입과 숫자 역정규화의 한계가 있으므로 이 점수는 청취 평가의 보조 지표다. Cosy의 이미 생성된 raw 19개도 채점했지만 축소 정책의 주비교에는 포함하지 않았다.

## 블라인드 보장과 저장

- 서버만 `engine`, 파일 경로, item ID와 토큰 매핑을 가진다. 공개 응답은 문장, 무작위 assignment/audio token, 길이 및 익명 집계만 허용한다.
- 오디오 토큰은 세션 소유권을 확인하고 투표 뒤 폐기한다. 오디오는 `FileResponse` 대신 바이트 응답으로 전달하여 파일명·ETag·수정 시각을 노출하지 않는다.
- 공개 집계와 JSON/CSV도 지속되는 임의 `Voice N` 번호와 그 번호 기준 순서를 사용한다. 실제 엔진 이름은 운영자 `scripts/aggregate_blind.py --reveal`에서만 제공한다.
- 평가자별 미평가 (문장, 엔진 쌍) 중 누적 응답이 가장 적은 쌍을 선택하고 A/B를 무작위 배치한다. full 세트의 가능한 비교는 총 195쌍이다.
- 별칭은 해시 파일명으로 바꿔 JSONL에 저장한다. 서버 시간과 제출 청취 길이를 검사하고, 브라우저는 두 `ended` 이벤트 뒤 투표를 활성화한다. 별칭은 인증 수단이 아니며 실제 사람이 주의 깊게 들었는지 증명하는 장치는 아니다.
- 재생 중 언어 변경 뒤에도 오래된 응답이나 오디오 오류가 화면을 덮지 않도록 정리했고, 실제 브라우저에서 확인했다(`blind-cleanup-acceptance.json`).

T8은 별도 nonstreaming full 합성 뒤 공통 후처리를 적용했다. T7은 스트리밍 지원 엔진의 incremental 경로를 측정하고 원본 WAV를 STT로 채점했다. 두 단계의 파형이 바이트 단위로 같다는 가정은 하지 않는다. 최종 라우드니스 보정은 기존 WAV에 공통 gain을 적용한 것으로 TTS 재합성은 하지 않았다. 최종 해시와 LUFS는 매니페스트 및 `blind-audio-validation.json`을 기준으로 한다.

## 재현 명령

프로젝트 루트에서 실행한다. 모델 창은 다른 워커와 먼저 조정하며 서버를 외부에 노출하지 않는다.

```sh
# T7: 기본 세 엔진 3회, Cosy는 사용자 결정에 따른 full 1회
.venv/bin/python scripts/bench_tts.py --engines kokoro,qwen3tts,voxcpm2 --locales all --repeats 3
.venv/bin/python scripts/bench_tts.py --engines cosyvoice3 --locales all --levels full --repeats 1 --resume
# 위 명령 종료 후 별도 STT 프로세스
.venv/bin/python scripts/bench_tts.py --score --resume
engines/mlx_engines/.venv/bin/python scripts/score_kana.py --input reports/bench-20261030.json
.venv/bin/python scripts/report.py

# T8: full만, 엔진 하나씩
.venv/bin/python scripts/gen_blind_set.py --engines kokoro,qwen3tts,voxcpm2,cosyvoice3 --locales en,zh-CN,zh-TW,ja,yue
.venv/bin/python scripts/finalize_blind_audio.py
pnpm --dir web build
.venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
# /blind, /blind/results, /report
.venv/bin/python scripts/check_blind_http.py --url http://127.0.0.1:8000
.venv/bin/python -m pytest tests --basetemp reports/test-temp -q
```

실제 마지막 웹 검증은 T9의 8000 포트와 충돌하지 않도록 **8018**에서 했으며 검증 서버도 종료했다. `server/app.py`의 T9 `/ws/call` 연동도 유지했다. 세부 실행·데이터 관리 안내는 `server/blind/README.md`에 있다. 이미 사람이 투표한 세트는 오디오/매니페스트/결과를 함께 보존한 뒤 새로 생성해야 한다.

## 환경, 중단 이력, 남은 범위

M4 Pro / 48GB / macOS 27.0.1 / AC 전원에서 기존 로컬 모델만 사용했다. 추가 모델 다운로드 0 bytes, 모델 디렉터리 약 16GiB. 같은 언어에서는 세 cloning 모델이 같은 FLEURS 참조를 사용했고 출처·CC-BY-4.0·전사는 기존 `data/refs/MANIFEST.json`에 있다. **대만 억양 참조 없음**: zh-TW는 본토 Mandarin을 공유하며, Kokoro는 별도 프리셋 화자와 간체 G2P를 쓴다.

초기 Qwen 측정 도중 다른 워커가 Ollama를 올려 안전 중단했다. 그 메타데이터/로그는 `bench-interrupted-20261030.json`, `bench-interrupted-run.log`에 보존하고 최종 비교에서 제외했다. 이후 코디네이터의 모델 창 해제 뒤 Qwen을 새로 측정했다. Cosy는 사용자 결정 `msg_6198f30429ba`에 따라 SIGINT로 안전 종료 후 full 1회로 재개했으며, 기존 행은 삭제하지 않았다.

코디네이터의 최종 가드는 OS critical pressure level 4, 시작 대비 swap +4GiB 초과, 디스크 <10GB이다. warning level 2와 free percentage는 기록만 하며, TTS/STT는 2초 주기로 감시했다. 전체 모델 작업 종료 시 pressure level 1, swap used 21,727.06MiB였고 이번 실행 창의 시작 값보다 감소했다. 기준 변경 및 측정 축소는 `docs/DEVIATIONS.md`에 기록했다.

승인된 T7/T8 완료 기준에서 미완 항목은 없다. 선택 항목 Qwen3-ASR은 미설치로 명시했고, 원어민 자연스러움·대만 억양 평가는 아직 수집하지 않았다. 모델 구조의 병목이 품질 차이의 원인이라는 인과적 결론은 내리지 않았다.

## 변경·생성 파일

- 구현: `scripts/{tts_common,bench_tts,report,gen_blind_set,finalize_blind_audio,score_kana,aggregate_blind,check_blind_http,blind_browser_fixture}.py`.
- 서버: `server/blind/{api,store,aggregate,metrics}.py`, `server/blind/README.md`, `server/app.py`의 blind/report 경로.
- 웹: `web/src/blind/{index.html,results.html,style.css,listen.ts,results.ts}`, `web/vite.blind.config.ts`, `web/package.json`, 생성된 `web/dist-blind/`.
- 테스트: `tests/test_blind.py`, `tests/test_tts_offline.py`; 변경 근거 `docs/DEVIATIONS.md`.
- 데이터/증거: `reports/bench-20261030.{json,md}`, 벤치 WAV 252개, `data/blind/manifest.json`, 블라인드 WAV 134개, 라우드니스/HTTP/브라우저/pytest/빌드 검증 결과 및 PNG.
- 전체 경로 목록은 `reports/t7-t8-files.json`에 있다. 정규화·LLM 모듈과 참조 음성은 수정하지 않았다.
