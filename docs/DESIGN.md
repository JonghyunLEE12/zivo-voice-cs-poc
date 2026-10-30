# ZIVO 외국인 CS AI 음성 상담 PoC 설계서

- 작성일: 2026-10-30
- 상태: PoC 구현·실측 반영 (2026-10-30). 초기 설계와 이후 검증의 차이는 `docs/DEVIATIONS.md`, 최종 해석은 `reports/FINAL-REPORT.md`에 기록한다.
- 범위: 브라우저 음성 상담 데모(영어, 중국어 본토, 중국어 대만, 일본어 + 광둥어 선택), 가짜 예약 조회 도구, TTS 블라인드 비교 페이지, 지연과 발음 측정 리포트.
- 원칙: API 키 없이 전부 로컬 오픈소스. 모델 학습은 하지 않는다. 모델 다운로드 총합 20GB 이하.

## 0. 요약

| 구분 | 선정 | 한 줄 이유 |
|---|---|---|
| weak 병목 후보 1 | VoxCPM2 (openbmb, 2B, Apache-2.0) | 이산 토큰 대신 연속 잠재 표현을 생성하고 semantic teacher가 없다. 30개 언어(ja, yue 포함). mlx-audio로 MLX 실행 경로가 있다. |
| weak 병목 후보 2 | Fun-CosyVoice3-0.5B-2512 (Apache-2.0) | 토크나이저가 영어 SSL teacher 대신 다국어 지도학습(ASR 등 530K시간)으로 학습됐다. 이산화(FSQ)는 남아 있어 후보 1과 대조가 된다. |
| baseline | Qwen3-TTS-12Hz-1.7B-Base (Apache-2.0) | 16층 RVQ 이산 토큰 + WavLM teacher. WavLM Large는 영어 94K시간으로만 사전학습됐다. 토스 발표 슬라이드에도 병목 사례로 나온 모델이다. |
| 대조군(빠른 응답) | Kokoro-82M v1.0 (Apache-2.0) | 82M 경량 모델, en/zh/ja 지원, mlx-audio 지원. 보이스 클로닝은 없다. |
| STT | mlx-whisper large-v3-turbo (+ 선택: Qwen3-ASR-0.6B) | 로컬 MLX, 99개 언어, 언어 지정과 자동 감지 둘 다 된다. 대만/본토 구분은 앱 언어 설정이 기준이다. |
| LLM | Ollama `qwen3.5:35b-a3b` (설치됨) | 도구 호출과 thinking 끄기(`think:false`)를 지원한다. 추가 다운로드 없음. |
| 턴 결정 | 브라우저 Silero VAD + 서버 Smart Turn v3.2 (선택) | 토스 발표의 VAD + SemVAD 구조를 로컬로 대응한다. |
| 서버 | Python FastAPI + 엔진별 별도 프로세스 | 모델 생태계가 Python이고, CosyVoice3(PyTorch)와 mlx-audio의 의존성을 분리해야 한다. |

모델 다운로드 계획 합계: 필수 16.93GB, 선택 포함 18.50GB (8장 표). LLM은 이미 설치된 23GB를 그대로 쓴다.

---

## 1. TTS 후보 분류표

### 1.1 판정 기준

토스 발표가 짚은 Speech Tokenizer 병목 세 가지를 후보마다 판정한다 [S1].

- B1 토크나이저 학습 데이터 분포: 음성을 토큰(또는 잠재 표현)으로 바꾸는 모듈이 en/zh/ja 데이터를 충분히 봤는가.
- B2 이산화 정보 손실: TTS 모델이 예측하는 대상이 이산 토큰(VQ, RVQ, FSQ)인가, 연속 표현(mel, VAE latent)인가.
- B3 Semantic distillation teacher: 토크나이저가 영어 위주 SSL 모델(wav2vec 2.0, WavLM 등)을 teacher로 쓰는가.

판정 값: `회피`(구조상 해당 병목이 없음), `약함`(있지만 완화됨), `있음`, `해당 없음`(토크나이저 자체가 없음), `미확인`(근거를 찾지 못함). 근거 없는 칸은 추측하지 않고 `미확인`으로 둔다.

RTF 표기: 이 문서의 RTF는 `합성 시간 / 생성된 오디오 길이`(작을수록 빠름, 1 미만이면 실시간보다 빠름)로 통일한다. mlx-community VoxCPM2 카드는 반대 정의(`>1.0 = faster than realtime`)를 쓰므로 역수로 바꿔 적고 원래 값을 괄호에 남긴다 [S5].

### 1.2 기본 정보와 언어 지원

| # | 유형 | 모델 | 버전/날짜 | 라이선스 (상업 이용) | en | zh 본토 | zh 대만 억양 | ja | yue | 제로샷 클로닝 | 근거 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | (a) 연속 표현 | VoxCPM2 | 날짜 미확인 (카드 인용 연도 2026) | Apache-2.0 (가능) | O | O | 미확인 | O | O (粤语 명시) | O (짧은 참조 음성, 참조 전사 주면 고품질) | [S2] |
| 2 | (a) 연속 표현 | VoxCPM1.5 | 2025-12-05 | Apache-2.0 (가능) | O | O | 미확인 | 보장 안 됨 (zh/en 위주라고 명시) | 미확인 | O | [S4] |
| 3 | (a) 연속 표현 | F5-TTS v1 Base | 논문 2024-10-09, v1 체크포인트 날짜 미확인 | 가중치 CC-BY-NC-4.0 (불가, PoC 한정) | O | O | 미확인 | X (학습 데이터 zh/en만) | X | O (논문의 제로샷 설정) | [S13][S14] |
| 4 | (b) 다국어 ASR 지도학습 토큰 | Fun-CosyVoice3-0.5B-2512 | 2025-12 | Apache-2.0 (가능) | O | O | 미확인 (대만 보통화 언급 없음) | O | 부분 (Guangdong 방언 지시로 지원) | O (교차 언어 클로닝 포함) | [S6][S7] |
| 5 | (b) 지도학습 토큰, 대만 특화 | BreezyVoice-300M | 논문 2025-01 | Apache-2.0 (가능) | 미확인 | 미확인 | O (대만 보통화 특화가 목적) | 미확인 | 미확인 | 미확인 | [S24][S25] |
| 6 | (c) 영어 SSL teacher VQ | Qwen3-TTS-12Hz-1.7B-Base (0.6B 있음) | 기술 보고서 2026-01-22 | Apache-2.0 (가능) | O | O | 미확인 (Beijing, Sichuan 방언 화자만 명시) | O | 언급 없음 | O (3초 클로닝, 참조 전사 필요) | [S10][S11] |
| 7 | (c) SSL teacher VQ | Spark-TTS-0.5B | 논문 2025-03-03 | CC-BY-NC-SA-4.0 (불가) | O | O | 미확인 | X | X | 미확인 | [S16][S17] |
| 8 | 이산 토큰, teacher 미확인 | OmniVoice | 논문 2026-04-01 | 코드 Apache-2.0, 가중치 CC-BY-NC (불가) | O | O | 미확인 | O | O (학습 13.3K시간) | O (참조 전사 필요) | [S18][S19] |
| 9 | (d) 경량 대조군 | Kokoro-82M v1.0 | 2025-01-27 | Apache-2.0 (가능) | O | O (음성 등급 D) | X (본토 음성만) | O (음성 등급 C+ ~ C-) | X | X (프리셋 음성) | [S20][S21] |
| 10 | (d) 경량 대조군 | MeloTTS | 날짜 미확인 | MIT (가능) | O (악센트 5종) | O (영어 혼용) | 미확인 | O | X | 미확인 | [S23] |

### 1.3 구조와 병목 판정

| # | 모델 | VQ 여부 / 예측 대상 | 토크나이저 종류 | Semantic teacher와 학습 언어 | B1 데이터 분포 | B2 이산화 | B3 teacher | 근거 |
|---|---|---|---|---|---|---|---|---|
| 1 | VoxCPM2 | 연속. 25Hz VAE 잠재 표현을 패치 단위로 생성(LM 6.25Hz). FSQ는 TSLM 은닉 상태에 정규화 용도로만 씀(256차원, 9단계) | 토크나이저 없음(tokenizer-free). AudioVAE V2 (16kHz 입력, 48kHz 출력) | 없음 (논문에 HuBERT/WavLM/Whisper 언급 없음, TSLM은 텍스트 LLM MiniCPM-4로 초기화) | 미확인 (AudioVAE 학습 데이터 미기재, VoxCPM2의 언어별 데이터 분포 미기재) | 약함 | 회피 | [S2][S3] |
| 2 | VoxCPM1.5 | 1과 같은 계열 | 1과 같음 (44.1kHz) | 없음 | 있음 (약 180만 시간 대부분 zh/en, 다국어 한계 명시) | 약함 | 회피 | [S3][S4] |
| 3 | F5-TTS v1 | 연속. 100차원 log mel (24kHz), flow matching, Vocos 보코더 | 없음 (문자 입력, 중국어는 pinyin) | 없음 (WavLM은 평가용 화자 유사도에만 사용) | 해당 없음 (토크나이저 없음. 단 모델 학습 데이터가 Emilia zh/en 95K시간이라 ja 불가) | 회피 | 회피 | [S14] |
| 4 | Fun-CosyVoice3 | 이산. 25Hz FSQ 토큰을 LLM이 예측, DiT flow matching이 mel로 복원 | MinMo 음성 인코더에 FSQ 삽입, 지도 다중 과제 학습 | SSL teacher 없음. 대신 지도학습 530K시간(ASR 365K시간: zh, en, ja, ko, ru, fr, de / LID 85K / SER 48K / AED 21K / 화자 11K) | 약함 (ja 포함 다국어, 언어별 비율 미확인) | 있음 | 회피 | [S7] |
| 5 | BreezyVoice | 미확인 (CosyVoice에서 파생이라는 것만 확인) | 미확인 | 미확인 | 미확인 | 미확인 | 미확인 | [S24][S25] |
| 6 | Qwen3-TTS-12Hz | 이산. 12.5Hz, 16층 다중 코드북(첫 층 semantic, 나머지 15층 RVQ acoustic), 코드북 2048 | Qwen3-TTS-Tokenizer-12Hz (경량 causal ConvNet) | WavLM이 첫 코드북 층의 teacher. WavLM Large 사전학습 94K시간 = Libri-Light 60K + GigaSpeech 10K + VoxPopuli 영어 24K ("English-only audio"에 초점이라고 명시) | 미확인 (토크나이저 단독 학습 데이터 미확인. 모델 전체는 10개 언어 500만 시간 이상) | 있음 | 있음 (영어 전용 teacher) | [S11][S12] |
| 7 | Spark-TTS | 이산. semantic 단일 코드북 VQ 50Hz(8192), global 토큰은 FSQ | BiCodec | wav2vec 2.0 XLSR-53 (11, 14, 16층 평균). XLSR-53 자체는 53개 언어 다국어 SSL | 있음 (BiCodec 학습 3천 시간: LibriSpeech 960h + Emilia-CN 1K + Emilia-EN 1K) | 있음 | 있음 (SSL teacher. 단 영어 전용은 아님) | [S17] |
| 8 | OmniVoice | 이산. Higgs-audio 토크나이저의 8 코드북 acoustic 토큰을 diffusion LM 방식으로 예측 | Higgs-audio tokenizer | 미확인 (논문에 codec teacher 언급 없음) | 미확인 (토크나이저 학습 언어 미기재. TTS 학습 데이터는 en 206K, zh 111K, ja 36.9K, ko 8.6K, yue 13.3K시간) | 있음 | 미확인 | [S19] |
| 9 | Kokoro-82M | 연속 (StyleTTS 2 + iSTFTNet, 음소 입력) | 없음 (misaki G2P: zh는 jieba, ja는 pyopenjtalk + UniDic) | 미확인 | 해당 없음 (다만 zh/ja 학습 데이터가 "H hours" 수준으로 적다고 카드에 표기) | 해당 없음 | 미확인 | [S20][S21][S22] |
| 10 | MeloTTS | 연속 (VITS, VITS2, Bert-VITS2 기반) | 없음 | 미확인 | 해당 없음 | 해당 없음 | 미확인 | [S23] |

### 1.4 Apple Silicon 실행 경로와 속도

| # | 모델 | 실행 경로 | 공개 속도 수치 | 이 머신(M4 Pro) 실측 |
|---|---|---|---|---|
| 1 | VoxCPM2 | MLX: mlx-audio + `mlx-community/VoxCPM2-bf16 / -8bit / -4bit` [S5][S26]. 원본 PyTorch의 MPS 동작은 미확인 | Apple Silicon(칩 미기재, 7 timesteps): bf16 RTF 약 2.08 (카드 0.48x), 8bit 약 1.18 (0.85x), 4bit 약 1.11 (0.90x). 즉 실시간보다 느림. CUDA: RTX 4090 RTF 약 0.30, Nano-VLLM 약 0.13 [S2][S5] | RTF en/zh-CN/zh-TW/ja/yue = 1.794/1.753/1.805/1.799/1.800; TTFA 9,240~11,229ms (문장 전체), 최대 9.02GB [T7] |
| 2 | VoxCPM1.5 | mlx-audio 목록에 없음. PyTorch MPS 미확인 | RTX 4090 스트리밍 RTF 0.17 [S4] | 평가 제외: 최종 선정 4개 엔진에 포함되지 않음 |
| 3 | F5-TTS v1 | MLX: `lucasnewman/f5-tts-mlx` (저장소 표기 MIT, 원 가중치는 CC-BY-NC로 취급) [S15] | 논문 RTF 0.15 (GPU) [S14]. Apple 수치 미확인 | 평가 제외: 최종 선정 4개 엔진에 포함되지 않음 |
| 4 | Fun-CosyVoice3 | (1) PyTorch MPS: 업스트림 PR #1869 패치 필요. 2026-05-18 시점 open, 미병합. f0 predictor를 CPU float64로 돌리는 우회 포함 [S8] (2) CPU (3) MLX 변환본 `aufklarer/CosyVoice3-0.5B-MLX-bf16`(2.25GB)은 Swift 런타임(speech-swift)용 [S9] | 지연 150ms까지(bi-streaming, 하드웨어 미기재) [S6]. Mac RTF 미확인 | RTF en/zh-CN/zh-TW/ja/yue = 2.634/2.300/2.213/2.534/2.829; TTFA 3,752~4,554ms (스트리밍), 최대 13.89GB; full 1회 축소 [T7] |
| 5 | BreezyVoice | PyTorch, Apple 동작 미확인 | 미확인 | 평가 제외: 최종 선정 4개 엔진에 포함되지 않음 |
| 6 | Qwen3-TTS-12Hz | MLX: mlx-audio + `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16` (0.6B, 8bit 등 변형 있음) [S26] | 첫 패킷 97ms (CUDA, 카드) [S10]. Apple RTF 미확인 | RTF en/zh-CN/zh-TW/ja = 0.580/0.569/0.569/0.584; TTFA 299~341ms (스트리밍), 최대 8.96GB [T7] |
| 7 | Spark-TTS | MLX: mlx-audio 지원 [S26] | 미확인 | 평가 제외: 최종 선정 4개 엔진에 포함되지 않음 |
| 8 | OmniVoice | MLX: mlx-audio 지원 [S26] | RTF 0.025까지 (하드웨어 미기재) [S18] | 평가 제외: 최종 선정 4개 엔진에 포함되지 않음 |
| 9 | Kokoro-82M | MLX: mlx-audio (`mlx-community/Kokoro-82M-bf16`) 또는 PyTorch CPU [S26] | Apple RTF 미확인 | RTF en/zh-CN/zh-TW/ja = 0.045/0.044/0.044/0.043; TTFA 268~324ms (문장 전체), 최대 2.65GB [T7] |
| 10 | MeloTTS | PyTorch CPU ("CPU real-time inference" 주장, 수치 없음). mlx-audio는 EN만 [S23][S26] | 미확인 | 평가 제외: 최종 선정 4개 엔진에 포함되지 않음 |


[T7] `reports/T7-T8-REPORT.md`, 상세 `reports/bench-20261030.md`. 단독 TTS, Ollama 언로드, full 언어별 중앙값. 메모리는 RSS와 backend allocation 중 큰 값이며 전체 시스템 메모리가 아니다. CosyVoice3는 full 1회, 나머지는 3회 반복이다.

### 1.5 최종 선정과 이유

| 역할 | 선정 | 이유 | 탈락 후보와 이유 |
|---|---|---|---|
| weak 병목 1 | VoxCPM2 (MLX bf16) | B2 약함, B3 회피. 상업 이용 가능. en, zh, ja, yue를 한 모델이 지원해서 언어마다 모델을 바꾸지 않아도 된다. MLX 변환본이 공식 mlx-community에 있다. B1은 미확인이라 측정으로 확인한다. | VoxCPM1.5: ja 보장 안 됨. F5-TTS: ja 불가, 비상업. |
| weak 병목 2 | Fun-CosyVoice3-0.5B-2512 (PyTorch MPS) | B3를 "영어 SSL teacher 대신 다국어 지도학습 토크나이저"로 피한 (b) 유형의 대표. B2는 남아 있어서 1번(연속)과 비교하면 "이산화만의 효과"를 가늠할 수 있다. ja, Guangdong 방언, 교차 언어 클로닝 지원. | BreezyVoice: 구조 정보 미확인이 많아 대만 억양 보조 후보로만 둔다(10장). |
| baseline | Qwen3-TTS-12Hz-1.7B-Base (MLX bf16) | 토스가 짚은 병목 B2, B3가 모두 확인된 모델(RVQ + 영어 전용 WavLM teacher). en/zh/ja 지원, Apache-2.0, mlx-audio 지원. 크기(약 2B)가 VoxCPM2(2B)와 비슷해 크기 효과를 줄인다. | Spark-TTS: ja 불가, 비상업, teacher가 다국어 XLSR-53이라 "영어 teacher" 대조로 약하다. OmniVoice: teacher 미확인, 비상업. |
| 대조군 | Kokoro-82M (MLX) | 82M로 가장 빠를 것으로 기대되는 en/zh/ja 모델. 응답 속도 기준선. | MeloTTS: mlx-audio가 EN만 지원. Kokoro zh 품질(등급 D)이 너무 낮으면 MeloTTS(PyTorch CPU)로 바꾼다. |

광둥어: VoxCPM2(粤语 명시)와 CosyVoice3(Guangdong 방언)만 표시하고 나머지는 UI에서 "미지원"으로 회색 처리한다.

대만 억양: 어느 후보도 대만 보통화를 공식 명시하지 않았다(BreezyVoice 제외). 그래서 (1) 대만 화자 참조 음성으로 제로샷 클로닝하고 (2) 대만 출신 평가자의 블라인드 결과만 따로 집계해서 판단한다. 공개 근거가 없으므로 결과가 나오기 전까지 모든 모델의 대만 억양 칸은 `미확인`이다.

---

## 2. STT

### 2.1 모델

| 용도 | 모델 | 크기 | 스트리밍 | 언어 감지 | 근거 |
|---|---|---|---|---|---|
| 실시간 데모 기본 | mlx-whisper + `mlx-community/whisper-large-v3-turbo` | 1.61GB | 아니오. VAD로 자른 발화 단위 일괄 인식 | Whisper 언어 감지 (지정도 가능, `yue` 포함) | [S28] |
| 선택: 교차 검증, 광둥어/대만 보강 | mlx-audio + `mlx-community/Qwen3-ASR-0.6B-bf16` | 1.57GB | 스트리밍은 vLLM 백엔드만 지원이라 Mac에서는 일괄 인식 | LID 지원 (1.7B 기준 평균 97.9%, Whisper-large-v3 94.1%) | [S27][S26] |

선택 근거: Whisper는 MLX 구현이 오래 쓰였고 `initial_prompt`로 고유명사 편향을 줄 수 있다. Qwen3-ASR은 CV-zh-tw WER 3.77, Fleurs-yue 3.98(1.7B 기준)을 공개해서 대만/광둥어 확인용으로 둔다 [S27]. 실시간 스트리밍 STT는 쓰지 않는다. 발화 끝(VAD) 이후 한 번에 인식하므로 STT 시간이 그대로 턴 지연에 들어가고, 이 값을 측정 포인트로 남긴다.

### 2.2 언어 결정 규칙

1. 기본은 앱 언어 설정이다. 데모 UI 상단에서 `en | zh-CN | zh-TW | ja | yue(선택)`를 고른다. ZIVO 앱에 붙일 때는 앱의 locale(zh-Hans, zh-Hant)을 그대로 넘긴다.
2. STT에는 언어를 지정해서 넘긴다. `zh-CN`, `zh-TW`는 모두 Whisper `zh`, `yue`는 `yue`.
3. 자동 모드(선택)는 Whisper 언어 감지 확률이 0.8 이상인 다른 지원 언어가 2턴 연속 나오면 응답 언어를 바꾼다.
4. 대만/본토 구분은 음성으로 하지 않는다. 음성만으로 구분하는 신뢰할 근거를 찾지 못했다(미확인). 앱 설정이 `zh-TW`면 대만, 아니면 본토로 본다. 자동 모드에서 `zh`가 감지됐는데 앱 설정이 없으면 `zh-CN`으로 두고 UI에서 바꿀 수 있게 한다.
5. 문자 체계 정리: Whisper의 중국어 출력이 간체인지 번체인지 보장되지 않으므로 STT 결과를 OpenCC로 바꾼다. `zh-TW`는 `s2twp`(대만 어휘 포함), `zh-CN`은 `t2s`. 바꾸기 전 원문도 로그에 남긴다.
6. 고유명사 편향: Whisper `initial_prompt`에 해당 언어의 사전 표기(명동, 을지로3가역 등의 언어별 표기)를 넣는다(5장 사전 재사용).

### 2.3 예약 코드 역정규화

사용자가 "Z V four eight two one", "Z V 四八二一", "ゼットブイ よんはちにいち"처럼 말하면 STT 출력은 제각각이다. `normalize/codes.py`의 `parse_spoken_code(text, locale)`가 영문자와 숫자(한자 숫자, 가나 숫자 포함)를 모아 `ZV-\d{4}` 형태로 맞춘다. LLM 도구 인자도 서버에서 한 번 더 이 함수로 정리한다.

---

## 3. LLM

### 3.1 모델

- 기본: Ollama `qwen3.5:35b-a3b` (로컬에 23GB로 설치 확인. Ollama 라이브러리 페이지에는 같은 계열이 `qwen3.5:35b`(22GB) 태그로 올라 있다) [S31]. 도구 호출(tools)과 thinking을 지원한다고 표기돼 있다.
- thinking은 끈다. `/api/chat`에 `"think": false` [S32]. 음성 응답 지연에서 추론 토큰은 그대로 손해다.
- 스트리밍과 도구 호출을 같이 쓴다. Ollama는 2025-05-28부터 스트리밍 응답과 도구 호출을 함께 지원한다 [S33]. 이 모델에서 실제로 되는지는 T6에서 확인한다.
- `keep_alive: "30m"`로 모델을 메모리에 붙잡아 둔다. 시스템 프롬프트와 도구 정의를 매 턴 똑같이 앞에 두어 프롬프트 캐시를 재사용한다.
- 대안(다운로드 필요, 승인 후): 첫 토큰 지연이 1초를 넘으면 `qwen3.5:9b`(6.6~7.6GB) [S31]. 지금 계획에는 넣지 않는다.

### 3.2 프롬프트 구조

시스템 프롬프트는 영어로 쓰고 언어별 블록만 바꾼다. 파일: `server/llm/prompts/system.md.j2`.

```text
You are "ZIVO Voice Support", a spoken customer-support agent for ZIVO, a Korea travel app.
Today is {{ demo_today }} (Asia/Seoul).

LANGUAGE
- Reply ONLY in {{ language_name }}. {{ locale_rules }}

VOICE STYLE
- This text will be read aloud. 1-2 short sentences per reply, max {{ max_units }}.
- No markdown, no lists, no emojis, no URLs, no parentheses.
- Ask one question at a time. Polite spoken register: {{ register }}.
- Write dates, times, prices and codes in normal written form (digits are fine). A separate step converts them for speech.

FACTS
- Never invent reservation details. Use tools.
- To look up a booking you need the reservation code (format ZV-1234) AND the guest's last name. Ask for whichever is missing.
- Use place and hotel names exactly as given in tool results (field `name`).
- Call transfer_to_human when: the user asks for a person, the user is upset twice, the user asks for an exception to the policy, or lookup failed twice.
```

| locale | language_name | locale_rules | max_units | register |
|---|---|---|---|---|
| en | English | Use US English. | 35 words | friendly, polite |
| zh-CN | Simplified Chinese | 使用简体中文和中国大陆用语（出租车、地铁、酒店、预订）。 | 60 Chinese characters | 礼貌口语，称呼"您" |
| zh-TW | Traditional Chinese (Taiwan) | 只使用繁體中文和台灣用語（計程車、捷運、飯店、訂房）。不要使用簡體字。 | 60 Chinese characters | 禮貌口語，稱呼「您」 |
| ja | Japanese | 自然な話し言葉の日本語で、丁寧語（です・ます）を使う。 | 80 characters | 丁寧、カスタマーサポート |
| yue (선택) | Cantonese (written Hong Kong style) | 用繁體字書面粵語口語。 | 60 characters | 有禮貌 |

출력 후처리(안전망): `zh-TW` 응답은 OpenCC `s2twp`, `zh-CN` 응답은 `t2s`를 한 번 더 거친다. 바뀐 글자 수를 로그에 남겨 프롬프트 준수율로 본다. 길이 초과는 자르지 않고 로그에만 표시한다(자르면 문장이 깨진다).

### 3.3 도구 스키마 (Ollama `tools` 형식)

```json
[
  {
    "type": "function",
    "function": {
      "name": "lookup_reservation",
      "description": "Find a ZIVO reservation. Requires the reservation code and the guest's last name.",
      "parameters": {
        "type": "object",
        "properties": {
          "reservation_code": {"type": "string", "description": "Format ZV-1234"},
          "last_name": {"type": "string", "description": "Guest last name in any script"}
        },
        "required": ["reservation_code", "last_name"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "get_cancellation_policy",
      "description": "Explain the cancellation policy and the refund that applies today for a reservation that was already looked up.",
      "parameters": {
        "type": "object",
        "properties": {
          "reservation_code": {"type": "string"}
        },
        "required": ["reservation_code"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "transfer_to_human",
      "description": "Hand the conversation to a human ZIVO agent.",
      "parameters": {
        "type": "object",
        "properties": {
          "reason": {"type": "string", "enum": ["user_request", "upset_customer", "policy_exception", "lookup_failed", "other"]},
          "summary_ko": {"type": "string", "description": "One-sentence summary in Korean for the Korean agent"}
        },
        "required": ["reason", "summary_ko"]
      }
    }
  }
]
```

실제 취소 실행 도구는 만들지 않는다(정책 안내까지만). 도구 결과는 응답 언어로 된 `name`, 금액, 날짜를 담아 돌려준다. 예:

```json
{
  "found": true,
  "reservation_code": "ZV-4821",
  "type": "hotel",
  "name": "Hanbit Stay Myeongdong",
  "lexicon_id": "hotel_hanbit_myeongdong",
  "check_in": "2026-11-03",
  "check_out": "2026-11-05",
  "nights": 2,
  "total_krw": 286000,
  "status": "confirmed"
}
```

`transfer_to_human` 결과는 `{"queued": true, "estimated_wait_min": 3}`이고, 데모는 이후 "상담원 연결 중" 상태로 바꾸고 마이크 루프를 멈춘다.

도구 호출 사이 빈 시간 메우기: LLM 스트림에서 도구 호출이 감지되면 미리 합성해 둔 짧은 안내 음성("Let me check that for you." / "我帮您查一下。" / "我幫您查一下。" / "確認いたしますので、少々お待ちください。")을 바로 재생한다. 엔진별, 언어별로 서버 시작 시 1회 합성해 캐시한다. 이 재생 시작 시각도 측정 포인트(`t_filler_play`)로 남긴다.

### 3.4 가짜 데이터

`data/fake_reservations.json`. 데모 기준일 `DEMO_TODAY=2026-11-01`(일요일)로 고정해 정책 계산 결과가 매번 같게 한다. 호텔 이름은 모두 가상이며 실존 여부는 확인하지 않았다. 데모 밖에서 쓰지 않는다.

| 코드 | 성(검증용) | 대상 언어 | 상품 | 일정 | 금액 | 취소 정책 (DEMO_TODAY 기준 결과) |
|---|---|---|---|---|---|---|
| ZV-4821 | Carter | en | 호텔: 한빛 스테이 명동 (가상) | 2026-11-03(화) ~ 11-05, 2박 | ₩286,000 | 체크인 3일 전(10-31)까지 무료, 하루 전(11-02)까지 50%, 이후 환불 불가 → 오늘은 50%(₩143,000) |
| ZV-7305 | 王 / Wang | zh-CN | 공항 픽업 택시: 인천국제공항 T1 → 을지로 랜턴 호텔 (가상) | 2026-11-07(토) 19:30 | ₩78,000 | 24시간 전까지 무료, 이후 50% → 오늘은 무료 |
| ZV-5196 | 陳 / Chen | zh-TW | 호텔: 한강 문라이트 스위트 (가상) | 2026-11-10(화) ~ 11-12, 2박 | ₩412,000 | 3일 전(11-07)까지 무료, 하루 전까지 50% → 오늘은 무료 |
| ZV-2610 | 佐藤 / Sato | ja | 투어: 경복궁 한복 체험, 2인 | 2026-11-05(목) 10:00, 경복궁역 5번 출구 집합 | ₩130,000 | 2일 전(11-03)까지 무료, 이후 100% → 오늘은 무료 |
| ZV-9034 | 陳 / Chan | yue (선택) | 호텔: 한빛 스테이 명동 (가상) | 2026-11-20 ~ 11-22 | ₩300,000 | 호텔 정책과 같음 → 무료 |

정책 규칙은 `data/policies.json`에 상품 유형별로 두고 `get_cancellation_policy`가 날짜를 계산한다. 성 검증은 대소문자, 로마자/한자 표기를 모두 받는다(`last_name_aliases`).

---

## 4. 실시간 루프 구조

### 4.1 전체 그림

```text
[브라우저]
 마이크(getUserMedia: echoCancellation, noiseSuppression, autoGainControl = true)
   -> @ricky0123/vad-web (Silero VAD, 16kHz Float32)
   -> onSpeechEnd: PCM16 발화 전송 ─────────────┐
 재생 큐(AudioContext, 24kHz PCM16 청크) <────┐  │
 barge-in: 재생 중 onSpeechStart -> 즉시 stop  │  │
                                              │  │  WebSocket /ws/call
[로컬 서버: FastAPI, :8000]                     │  │
   (선택) Smart Turn v3.2 ONNX: 말이 끝났나? <───┼──┘
   STT (mlx-whisper, 인프로세스 스레드)          │
   LLM (Ollama /api/chat stream, tools)        │
   문장 분할기 -> 정규화(사전, 숫자) -> TTS 요청  │
   TTS 응답 PCM 청크 ──────────────────────────┘
        │ HTTP (localhost)
[TTS 엔진 서버들]  :8101 VoxCPM2 | :8102 CosyVoice3 | :8103 Qwen3-TTS | :8104 Kokoro
```

### 4.2 브라우저

- VAD: `@ricky0123/vad-web` [S30]. 기본값에서 시작하고, 발화 종료 판정 무음 길이(`redemption` 계열 옵션)를 UI 슬라이더로 둔다(기본 500ms 목표). 옵션 이름은 구현 시 문서(docs.vad.ricky0123.com)에서 확인한다(미확인).
- 전송: 발화 단위. `onSpeechEnd`의 16kHz Float32를 PCM16LE로 바꿔 보낸다. 실시간 스트리밍 STT를 쓰지 않으므로 프레임 단위 업로드는 하지 않는다.
- 재생: 서버에서 받은 24kHz PCM16 청크를 `AudioBufferSourceNode`로 이어 붙여 재생한다(이전 청크 종료 시각에 다음 청크 예약).
- barge-in: AI 음성 재생 중 VAD `onSpeechStart`(최소 발화 길이 필터 통과)가 오면 (1) 모든 재생 노드를 즉시 `stop()`, 큐 비움 (2) 서버에 `barge_in` 전송(재생된 ms 포함). 스피커 출력이 마이크로 들어가 생기는 오탐을 줄이려고 헤드폰 사용을 권장하고, 재생 중에는 VAD 임계값을 올리는 옵션을 둔다.
- 화면: 언어 선택, TTS 엔진 선택, 정규화 수준(raw/lex/full), 자막(STT 결과, LLM 응답), 턴별 지연 HUD, barge-in 횟수.

### 4.3 서버 처리 순서 (한 턴)

1. 발화 수신 → (선택) Smart Turn으로 "끝났나" 판정. 미완료면 최대 1.5초 더 기다리며 다음 발화와 합친다 [S29].
2. STT → OpenCC 정리 → `stt.result` 전송.
3. LLM 스트리밍 시작. 토큰을 문장 분할기에 넣는다.
4. 문장 분할기: 종결 부호(`. ! ? 。 ！ ？`, 일본어 `。`)에서 자른다. 숫자 안의 마침표(`3.5`), 코드(`ZV-4821`), 약어(`a.m.`)에서는 자르지 않는다. 첫 문장만 빨리 내보내려고 쉼표(`, ， 、`)에서도 자를 수 있게 하되 최소 길이(en 4단어, zh/ja 6자)를 둔다.
5. 문장마다: 정규화(5장) → TTS 엔진에 요청. 문장 i를 재생하는 동안 문장 i+1을 합성한다(큐 깊이 2).
6. 엔진이 스트리밍을 지원하면 청크를 받는 대로, 아니면 문장 전체 합성 후 보낸다. 엔진 어댑터 인터페이스는 항상 `AsyncIterator[bytes]`로 통일한다.
7. 도구 호출이면 안내 음성 재생 → 도구 실행 → 결과를 붙여 LLM 2차 스트림 → 3~6 반복.
8. barge-in 수신 시: LLM 스트림 태스크와 TTS 태스크를 `asyncio` 취소, 엔진 HTTP 연결을 끊는다(엔진 서버는 청크 사이에 연결 끊김을 확인하고 중단). 대화 기록에는 실제로 재생 시작된 문장까지만 assistant 메시지로 남긴다.

### 4.4 WebSocket 프로토콜

텍스트 프레임은 JSON, 바이너리 프레임은 오디오다. 바이너리 프레임 앞 8바이트 헤더: `u8 kind`(1=마이크 PCM16 16kHz, 2=TTS PCM16 24kHz), `u8 reserved`, `u16 chunk_seq`, `u32 turn_num` (리틀 엔디언).

클라이언트 → 서버

| type | 필드 | 설명 |
|---|---|---|
| `session.start` | `locale, tts_engine, normalize(raw/lex/full), stt(whisper/qwen3asr), turn_detect(vad/vad+smartturn)` | 세션 설정. 중간 변경 가능 |
| `utterance` | `turn_num, samples, t_vad_end` | 바로 뒤에 kind=1 바이너리 프레임 |
| `barge_in` | `turn_num, played_ms, t_client` | 재생 중단 알림 |
| `playback.started` | `turn_num, chunk_seq, t_client` | 첫 청크는 지연 측정에 쓴다 |
| `playback.finished` | `turn_num, t_client` | |

서버 → 클라이언트

| type | 필드 |
|---|---|
| `stt.result` | `turn_num, text, text_raw, lang_detected, lang_prob` |
| `llm.delta` | `turn_num, text` |
| `tool.call` / `tool.result` | `turn_num, name, args / result` |
| `tts.sentence` | `turn_num, seq, display_text, tts_text, engine, sample_rate` (바로 뒤에 kind=2 바이너리 청크들) |
| `turn.done` | `turn_num, marks{...}` |
| `turn.cancelled` | `turn_num, reason` |
| `error` | `code, message` |

### 4.5 턴 지연 측정 포인트

모든 서버 시각은 `time.perf_counter()` 기준 ms. 클라이언트 시각은 `performance.now()`이며 클라이언트 쪽 구간만 클라이언트 시계로 계산한다(시계 동기화 안 함).

| 포인트 | 위치 | 의미 |
|---|---|---|
| `c_speech_end_est` | 브라우저 | VAD가 판정한 실제 발화 끝 추정 = `c_vad_end - redemption 무음 길이` |
| `c_vad_end` | 브라우저 | VAD `onSpeechEnd` 시각 |
| `s_rx` | 서버 | 발화 수신 |
| `s_turn_decided` | 서버 | Smart Turn 판정 완료 (미사용 시 `s_rx`와 같음) |
| `s_stt_done` | 서버 | STT 완료 |
| `s_llm_first_token` | 서버 | LLM 첫 텍스트 토큰 |
| `s_tool_call` / `s_tool_done` | 서버 | 도구 호출 감지 / 결과 반환 |
| `s_first_sentence` | 서버 | 첫 문장 확정 |
| `s_tts_first_audio` | 서버 | 엔진에서 첫 오디오 바이트 수신 |
| `c_play_start` | 브라우저 | 첫 청크 재생 시작 |

리포트 지표: 사용자 체감 지연 `c_play_start - c_vad_end`(그리고 `- c_speech_end_est`), 서버 구간 `stt = s_stt_done - s_turn_decided`, `llm_ttft = s_llm_first_token - s_stt_done`, `first_sentence = s_first_sentence - s_llm_first_token`, `tts_ttfa = s_tts_first_audio - s_first_sentence`. 턴 로그는 `data/logs/turns-YYYYMMDD.jsonl`.

목표치(측정 전 가정): 대조군(Kokoro) 기준 `c_play_start - c_vad_end` 1.5초 이하. 실측은 T9에서 채운다.

통화 지표(토스 발표 대응): 세션별 barge-in 발생 여부, 턴당 사용자 발화 글자 수, 세션 길이. 데모 규모라 통계적 결론은 내지 않고 엔진별로 기록만 한다.

---

## 5. 한국어 고유명사 처리와 정규화

### 5.1 사전 형식

`data/lexicon.yaml`. 표시용(`display`)과 TTS 투입용(`tts`)을 나눈다. `tts`가 없으면 `display`를 쓴다. `aliases`는 LLM이나 STT가 낼 수 있는 변형 표기다.

```yaml
- id: place_myeongdong
  ko: 명동
  display: {en: Myeongdong, zh-CN: 明洞, zh-TW: 明洞, ja: 明洞, yue: 明洞}
  tts:     {en: Myung-dong, ja: ミョンドン}
  aliases: [Myeong-dong, Myungdong, Myeongdong, ミョンドン, 명동]
- id: station_myeongdong
  ko: 명동역
  display: {en: Myeongdong Station, zh-CN: 明洞站, zh-TW: 明洞站, ja: 明洞駅, yue: 明洞站}
  tts:     {en: Myung-dong Station, ja: ミョンドンえき}
- id: station_euljiro3ga
  ko: 을지로3가역
  display: {en: Euljiro 3-ga Station, zh-CN: 乙支路3街站, zh-TW: 乙支路3街站, ja: 乙支路3街駅, yue: 乙支路3街站}
  tts:     {en: Ul-jee-ro Sahm-ga Station, zh-CN: 乙支路三街站, zh-TW: 乙支路三街站, ja: ウルチロサムガえき, yue: 乙支路三街站}
  aliases: [Euljiro 3ga, Euljiro Samga, Eulji-ro 3-ga, 을지로 3가역]
- id: station_hongik
  ko: 홍대입구역
  display: {en: Hongik University Station, zh-CN: 弘大入口站, zh-TW: 弘大入口站, ja: 弘大入口駅, yue: 弘大入口站}
  tts:     {ja: ホンデイック えき}
- id: station_gyeongbokgung
  ko: 경복궁역
  display: {en: Gyeongbokgung Station, zh-CN: 景福宫站, zh-TW: 景福宮站, ja: 景福宮駅, yue: 景福宮站}
  tts:     {en: Gyung-bok-goong Station, ja: キョンボックンえき}
- id: station_chungmuro
  ko: 충무로역
  display: {en: Chungmuro Station, zh-CN: 忠武路站, zh-TW: 忠武路站, ja: 忠武路駅, yue: 忠武路站}
  tts:     {ja: チュンムロえき}
- id: airport_icn
  ko: 인천국제공항
  display: {en: Incheon International Airport, zh-CN: 仁川国际机场, zh-TW: 仁川國際機場, ja: 仁川国際空港, yue: 仁川國際機場}
  tts:     {ja: インチョンこくさいくうこう}
- id: rail_arex
  ko: 공항철도
  display: {en: AREX, zh-CN: 机场快线, zh-TW: 機場捷運, ja: 空港鉄道, yue: 機場快綫}
  tts:     {en: A-rex}
- id: station_seoul
  ko: 서울역
  display: {en: Seoul Station, zh-CN: 首尔站, zh-TW: 首爾站, ja: ソウル駅, yue: 首爾站}
- id: hotel_hanbit_myeongdong        # 가상
  ko: 한빛 스테이 명동
  display: {en: Hanbit Stay Myeongdong, zh-CN: 明洞韩光酒店, zh-TW: 明洞韓光飯店, ja: ハンビットステイ明洞, yue: 明洞韓光酒店}
  tts:     {en: Hahn-bit Stay Myung-dong, ja: ハンビットステイ ミョンドン}
- id: hotel_euljiro_lantern          # 가상
  ko: 을지로 랜턴 호텔
  display: {en: Euljiro Lantern Hotel, zh-CN: 乙支路灯笼酒店, zh-TW: 乙支路燈籠飯店, ja: 乙支路ランタンホテル, yue: 乙支路燈籠酒店}
  tts:     {en: Ul-jee-ro Lantern Hotel, ja: ウルチロ ランタンホテル}
- id: hotel_hangang_moonlight        # 가상
  ko: 한강 문라이트 스위트
  display: {en: Hangang Moonlight Suites, zh-CN: 汉江月光套房酒店, zh-TW: 漢江月光套房飯店, ja: 漢江ムーンライトスイート, yue: 漢江月光套房酒店}
  tts:     {en: Hahn-gahng Moonlight Suites, ja: ハンガン ムーンライトスイート}
- id: tour_hanbok_gyeongbokgung
  ko: 경복궁 한복 체험 투어
  display: {en: Gyeongbokgung Hanbok Experience Tour, zh-CN: 景福宫韩服体验之旅, zh-TW: 景福宮韓服體驗行程, ja: 景福宮韓服体験ツアー, yue: 景福宮韓服體驗團}
  tts:     {ja: キョンボックン ハンボク体験ツアー}
```

`en`의 `tts` 철자(`Myung-dong` 등)는 초기값이다. 맞는지는 T7에서 사전 적용 전/후 CER과 블라인드 청취로 정한다. 일본어는 한자 표기를 그대로 넣으면 음독(예: 明洞를 めいどう)으로 읽힐 위험이 있어 가타카나 독음을 기본으로 둔다.

### 5.2 정규화 단계

`server/normalize/pipeline.py`의 `normalize(text, locale, level)`.

| level | 단계 |
|---|---|
| `raw` | 아무것도 안 함 (LLM 출력 그대로) |
| `lex` | (1) 사전 치환: `display`와 `aliases`를 긴 것부터 찾아 해당 locale의 `tts`로 바꾼다 |
| `full` | `lex` + (2) 예약 코드 읽기 + (3) 금액 + (4) 날짜, 요일, 시각 + (5) 일반 숫자, 퍼센트 + (6) 기호 정리(괄호, 슬래시, 이모지 제거) |

언어별 규칙 예 (`full`):

| locale | 입력 | 출력 |
|---|---|---|
| en | `ZV-4821` | `Z V four eight two one` |
| en | `₩286,000` | `two hundred eighty-six thousand won` |
| en | `November 3rd`, `7:30 PM` | 그대로 (영어 TTS가 대체로 읽음, CER로 확인), `seven thirty p m` |
| zh-CN | `ZV-7305` | `Z V 七三零五` (전화번호식 `幺` 읽기는 옵션) |
| zh-CN | `78,000韩元` | `七万八千韩元` |
| zh-CN | `11月7日晚上19:30` | `十一月七日晚上七点半` |
| zh-TW | `412,000韓元` | `四十一萬兩千韓元` (양사 앞 2는 兩) |
| ja | `ZV-2610` | `ゼット ブイ に ろく いち ゼロ` |
| ja | `11月5日午前10時` | `じゅういちがつ いつか ごぜん じゅうじ` (1~31일 특수 독음 표 사용) |
| ja | `130,000ウォン` | `じゅうさんまん ウォン` |
| 모든 locale | `50%` | `fifty percent` / `百分之五十` / `百分之五十` / `ごじゅっパーセント` |

숫자 변환 라이브러리: 영어는 `num2words`, 중국어는 `cn2an`, 일본어는 자체 규칙(작은 표, 단위 테스트로 고정). 구현 시 라이브러리 라이선스를 확인한다.

사전 적용 전/후 비교: 턴 로그와 블라인드 매니페스트에 `display_text`, `tts_text_raw`, `tts_text_lex`, `tts_text_full`을 모두 남긴다. 리포트(7장)는 같은 엔진, 같은 문장을 `raw`와 `full`로 합성해 고유명사 적중률과 CER을 비교한다. 블라인드 페이지에도 "같은 엔진, raw 대 full" 쌍을 넣는다.

zh-TW와 Kokoro: Kokoro의 misaki 중국어 G2P가 번체를 처리하는지 미확인이라, Kokoro에 넣기 전에는 OpenCC `t2s`로 바꾼다. 이 경우 대만식 다음자(多音字) 독음은 반영되지 않는다는 한계를 리포트에 적는다.

---

## 6. 블라인드 비교 페이지

### 6.1 고정 문장 세트

`data/blind/sentences.yaml`. 언어별 8문장: 인사, 조회 결과(코드 포함), 금액/날짜, 취소 정책(퍼센트), 길 안내(역명), 고유명사(호텔), 사과(감정), 상담원 연결. 아래는 `raw` 텍스트이며, 합성은 `full` 정규화를 기본으로 한다.

en

1. Hello, this is ZIVO customer support. How can I help you today?
2. I found your booking. Your reservation number is ZV-4821, at Hanbit Stay Myeongdong.
3. You check in on Tuesday, November 3rd, for two nights, and the total is ₩286,000.
4. Free cancellation ended on October 31st, so a 50% fee of ₩143,000 applies until November 2nd.
5. From Incheon Airport, take AREX to Seoul Station, then Line 4 to Myeongdong Station.
6. Your driver will drop you at Euljiro Lantern Hotel, near Exit 10 of Euljiro 3-ga Station.
7. I'm so sorry for the trouble. Let me check that for you right away.
8. I'll connect you with a human agent now. Please stay on the line.

zh-CN

1. 您好，这里是ZIVO客服中心，请问有什么可以帮您？
2. 已经查到您的预订，预订号ZV-7305，11月7日晚上19:30从仁川国际机场出发。
3. 车费是78,000韩元，已经用信用卡付款了。
4. 接送开始前24小时以内取消的话，需要支付50%的费用。
5. 司机会在第1航站楼3号出口等您，然后送您到乙支路灯笼酒店。
6. 酒店离乙支路3街站10号出口走路大约3分钟。
7. 给您带来不便，真的非常抱歉，我马上帮您确认。
8. 我现在为您转接人工客服，请稍等，不要挂断电话。

zh-TW

1. 您好，這裡是ZIVO客服中心，請問有什麼可以幫您的嗎？
2. 已經查到您的訂房，訂單編號ZV-5196，11月10日入住漢江月光套房飯店。
3. 兩晚的總金額是412,000韓元，已經用信用卡付款了。
4. 11月7日以前取消都可以免費，之後要收取50%的手續費。
5. 從仁川國際機場搭機場捷運到首爾站，再轉搭地鐵4號線到明洞站。
6. 飯店就在弘大入口站附近，走路大約5分鐘。
7. 造成您的不便，真的很抱歉，我馬上幫您確認。
8. 我現在幫您轉接真人客服，請稍候，不要掛斷電話喔。

ja

1. お問い合わせありがとうございます。ZIVOカスタマーサポートです。
2. ご予約を確認しました。予約番号ZV-2610、11月5日午前10時の景福宮韓服体験ツアーですね。
3. お二人分で合計130,000ウォン、クレジットカードでお支払い済みです。
4. 11月3日までは無料でキャンセルできますが、それ以降は全額のキャンセル料がかかります。
5. 集合場所は景福宮駅の5番出口です。
6. 明洞駅から地下鉄4号線で忠武路駅まで行き、3号線に乗り換えて景福宮駅で降りてください。
7. ご不便をおかけして、大変申し訳ございません。すぐに確認いたします。
8. ただいま担当者におつなぎします。このまま少々お待ちください。

yue (선택, VoxCPM2와 CosyVoice3만)

1. 你好，呢度係ZIVO客服，有咩可以幫到你？
2. 你嘅訂單編號係ZV-9034，十一月二十號入住明洞韓光酒店。
3. 我而家幫你轉接真人客服，唔該等一等。

### 6.2 오디오 준비

`scripts/gen_blind_set.py`가 미리 합성한다(실시간 아님).

- 화자 통일: 클로닝 가능한 세 모델(VoxCPM2, CosyVoice3, Qwen3-TTS)은 언어별로 같은 참조 음성(`data/ref_voices/{locale}/ref.wav` + `ref.txt`, 10초 내외)을 쓴다. Kokoro는 성별이 같은 프리셋(en `af_heart`, zh `zf_xiaobei`, ja `jf_alpha`)을 쓰고, 화자가 다르다는 편향을 리포트에 적는다.
- 후처리 통일: 모두 24kHz 모노로 리샘플, 앞뒤 무음을 150ms로 맞추고, ffmpeg `loudnorm`으로 같은 라우드니스(-20 LUFS 목표)로 맞춘 WAV. VoxCPM2의 48kHz 장점은 없어지지만 "자연스러움" 비교에서 음질 단서를 빼기 위한 의도다.
- 문장당 시드 고정, 1회 합성. 실패(무음, 길이가 텍스트 대비 3배 초과 등)는 재합성하지 않고 매니페스트에 `failed`로 남겨 리포트의 실패율로 쓴다.
- 매니페스트 `data/blind/manifest.json`: `item_id, locale, sentence_id, engine, normalize, file, duration_s, sha256, seed`.

### 6.3 비교 방식

- 비교 쌍: (weak1 대 baseline), (weak2 대 baseline), (weak1 대 weak2), (각 후보 대 대조군), (같은 엔진 raw 대 full) 다섯 묶음.
- 서버가 쌍을 고른다. 평가자별로 아직 안 들은 (문장, 쌍) 중 전체 응답 수가 가장 적은 것을 먼저 준다. A/B 위치는 무작위.
- 블라인드 보장: 브라우저는 엔진 이름을 모른다. 오디오 URL은 서버가 발급한 무작위 토큰(`/api/blind/audio/{token}`)이고, 토큰과 파일 매핑은 서버 메모리와 결과 파일에만 있다. 파일 길이 차이 외의 단서(파일명, 샘플레이트, 헤더)는 없앤다.
- 화면: 문장 텍스트, A/B 재생 버튼(둘 다 끝까지 들어야 투표 활성화), 척도 3개 `A가 더 자연스러움 / 차이 없음 / B가 더 자연스러움`, 보조 체크 `A 발음 오류`, `B 발음 오류`, 메모.
- 평가자 정보: 별칭, 모국어(en, zh-CN, zh-TW, ja, yue, ko, 기타), 청취 장치(헤드폰/스피커). 대만 억양 판단은 모국어 zh-TW 평가자 결과만 따로 본다.

### 6.4 저장과 집계

- 저장: `data/blind/results/{rater}.jsonl`, 한 줄에 투표 하나. `{ts, rater, rater_native, device, locale, sentence_id, pair_kind, a:{engine,normalize,item_id}, b:{...}, choice: "A"|"same"|"B", pron_issue_a, pron_issue_b, listened_ms_a, listened_ms_b, comment}`.
- 집계 화면 `/blind/results`:
  - 쌍 × 언어별로 토스 발표와 같은 막대: "X가 더 자연스러움 / 차이 없음 / Y가 더 자연스러움" 비율 (A/B를 엔진 기준으로 다시 정렬).
  - 동점 제외 승률과 부호 검정 p값(양측), 응답 수.
  - 엔진별 발음 오류 표시율.
  - 필터: 언어, 평가자 모국어, 장치.
  - JSON/CSV 내보내기.
- 집계 로직은 `server/blind/aggregate.py`로 분리해서 단위 테스트한다.

---

## 7. 측정 리포트

`scripts/bench_tts.py` → `reports/bench-YYYYMMDD.json`, `scripts/report.py` → `reports/bench-YYYYMMDD.md`와 `/report` 페이지.

| 지표 | 정의 | 방법 |
|---|---|---|
| 로드 시간 | 엔진 프로세스 시작부터 첫 합성 가능까지 | 엔진 `/v1/info`의 `load_ms` |
| RTF | 합성 시간 / 오디오 길이 | 워밍업 1회 후 문장별 3회, 중앙값과 p90 |
| 첫 오디오 지연 (TTFA) | 요청부터 첫 PCM 바이트까지 | 스트리밍 지원 엔진은 첫 청크, 미지원은 문장 전체 합성 시간. 어느 쪽인지 표에 표시 |
| 메모리 | 엔진 프로세스 최대 사용량 | MLX는 `mx.get_peak_memory()`(API 이름은 구현 시 확인), PyTorch는 `torch.mps.driver_allocated_memory()`, 공통으로 psutil RSS. 통합 메모리라 RSS가 GPU 할당을 다 반영하지 않을 수 있다는 점을 적는다 |
| 발음 정확도 | TTS 출력을 STT로 다시 인식해 원문과 비교 | en은 WER, zh/yue는 CER, ja는 CER(표기)과 가나 CER(pyopenjtalk로 둘 다 가나로 바꿔 비교) |
| 고유명사 적중률 | 문장의 사전 항목이 STT 결과에 나오는 비율 | 사전 `display`와 `aliases` 매칭. `raw` 대 `full` 비교 |
| 실패율 | 무음, 잘림, 텍스트 대비 비정상 길이 | 길이/글자수 비율 임계값 |

CER/WER 정규화: 소문자화, 문장부호 제거, 중국어는 기준과 가설 모두 OpenCC `t2s`, 숫자는 양쪽 다 같은 정규화를 거친 뒤 비교. STT는 Whisper(필수)와 Qwen3-ASR(선택) 둘 다 돌려 한 STT의 편향을 드러낸다. 참조 텍스트는 정규화 `full` 결과가 아니라 `display_text` 기준으로 하고, STT 출력도 같은 역정규화(숫자 → 아라비아 숫자)를 거친다.

측정 조건: Ollama 모델을 내리고(`keep_alive: 0`) 엔진 하나씩 띄워 잰다. 전원 연결, 다른 무거운 앱 종료. 실시간 데모 상황(LLM과 동시 실행)의 수치는 턴 로그(4.5)에서 따로 본다.

리포트 표 형식: 행 = 엔진, 열 = 언어 × {RTF 중앙값, TTFA, 피크 메모리, CER/WER(Whisper), CER(Qwen3-ASR), 고유명사 적중률 raw → full, 실패율}. 측정 못 한 칸은 `실패: 사유`로 채운다.

---

### 7.1 구현 후 측정 결과 (2026-10-30)

- 단독 TTS 전체 20개 엔진×언어 칸은 `reports/bench-20261030.md`에 값 또는 실패 사유로 채웠다. CER/WER·가나 CER·고유명사·실패율·메모리를 포함한다. CosyVoice3 축소 조건은 `reports/T7-T8-REPORT.md`를 따른다.
- 실제 브라우저 Kokoro 첫 재생은 en 2971.9ms, zh-CN 4681.9ms, zh-TW 5361.6ms, ja 4001.3ms다. 목표 1.5초는 미달이다 (`reports/T10-REPORT.md`).
- Smart Turn ON/OFF 판정·대기·지연 및 통화 지표는 `reports/T11-REPORT.md`, `/report`, `/api/report/sessions`에 있다. 로그 revision은 최신 1개만 집계하고 시작·종료 기록이 없는 과거 통화의 길이는 추정하지 않는다.
- Qwen3-ASR은 선택 미설치, 대만 억양·원어민 청취 평가 미수집이다. 병목 구조와 발음의 인과관계는 검증하지 못했다. `reports/FINAL-REPORT.md`에 최종 해석과 다음 실험을 기록했다.

## 8. 디렉터리, 기술 스택, 실행 명령, 다운로드 크기

### 8.1 기술 스택 판단

- 서버는 Python FastAPI + uvicorn을 권장한다. 이유: STT/TTS/정규화 라이브러리가 모두 Python이고, WebSocket과 비동기 스트리밍을 기본 지원한다. Node 서버를 두면 모델 호출을 결국 Python 프로세스로 넘겨야 해서 층만 늘어난다.
- TTS 엔진은 엔진별 프로세스로 분리한다. mlx-audio 계열(VoxCPM2, Qwen3-TTS, Kokoro)은 한 uv 프로젝트에서 엔진 인자만 바꿔 띄우고, CosyVoice3는 PyTorch와 자체 의존성이 있어 별도 uv 프로젝트로 둔다. 장점: 의존성 충돌 방지, 엔진별 메모리 측정, 한 엔진이 죽어도 나머지 동작.
- 프런트엔드는 Vite + TypeScript(프레임워크 없음), pnpm. 빌드 결과를 FastAPI가 정적 파일로 서빙한다. 마이크는 `localhost`에서 보안 컨텍스트로 허용된다.
- Python 3.12를 기본으로 하고, CosyVoice3가 요구하는 버전은 구현 시 저장소 README로 확인한다(미확인).

### 8.2 디렉터리

```text
zivo-voice-cs-poc/
  docs/DESIGN.md
  README.md
  pyproject.toml                 # 메인 서버 (uv)
  server/
    app.py                       # FastAPI: /ws/call, /api/blind/*, /report, 정적 파일
    config.py                    # 포트, 엔진 URL, DEMO_TODAY, 경로
    pipeline/turn.py             # 한 턴 오케스트레이션, 취소 처리
    pipeline/sentence_splitter.py
    pipeline/latency.py          # 측정 포인트 기록, 턴 로그
    stt/base.py  stt/mlx_whisper_stt.py  stt/qwen3_asr_stt.py  stt/script.py (OpenCC)
    llm/ollama_client.py  llm/tools.py  llm/fake_db.py  llm/prompts/system.md.j2
    tts/client.py                # 엔진 HTTP 클라이언트, AsyncIterator[bytes]
    normalize/lexicon.py  normalize/codes.py  normalize/num_en.py  normalize/num_zh.py  normalize/num_ja.py  normalize/pipeline.py
    blind/api.py  blind/store.py  blind/aggregate.py
    turn_detect/smart_turn.py    # ONNX Runtime
  engines/
    PROTOCOL.md                  # 엔진 HTTP 계약
    mlx_engines/pyproject.toml   # mlx-audio
    mlx_engines/serve.py         # --engine voxcpm2|qwen3tts|kokoro --port
    cosyvoice3/pyproject.toml    # torch, CosyVoice 의존성
    cosyvoice3/serve.py
    cosyvoice3/vendor/           # 고정 커밋 + MPS 패치 (구현 시 결정)
  web/                           # Vite + TS
    src/call/  src/blind/  src/report/  src/shared/ws.ts
  data/
    lexicon.yaml  fake_reservations.json  policies.json
    ref_voices/{en,zh-CN,zh-TW,ja,yue}/ref.wav, ref.txt
    blind/sentences.yaml  blind/manifest.json  blind/audio/  blind/results/
    logs/
  scripts/
    fetch_models.py  check_env.py  smoke_tts.py  bench_tts.py  bench_stt.py
    gen_blind_set.py  aggregate_blind.py  report.py  ws_wav_client.py  dev.sh
  models/                        # HF_HOME (git 제외)
  reports/
  tests/
```

### 8.3 엔진 HTTP 계약 (요약, 상세는 `engines/PROTOCOL.md`)

- `GET /v1/info` → `{engine, model_id, locales, supports_streaming, native_sample_rate, load_ms, peak_mem_bytes}`
- `POST /v1/warmup`
- `POST /v1/synthesize` `{text, locale, voice: {ref_audio_path, ref_text} | {preset}, seed, stream: bool}` → `audio/L16; rate=24000; channels=1` chunked 응답. 헤더 `X-Synth-Ms`(비스트리밍), 엔진 쪽에서 24kHz 리샘플까지 끝내서 보낸다.
- 포트: 8101 VoxCPM2, 8102 CosyVoice3, 8103 Qwen3-TTS, 8104 Kokoro. 메인 서버 8000.

### 8.4 실행 명령 (구현 후 기준)

```bash
cd /Users/jongram/Desktop/global_alpha/test/zivo-voice-cs-poc

# 1) 환경 확인과 모델 받기
uv sync
uv run python scripts/check_env.py                 # ffmpeg, ollama, qwen3.5:35b-a3b, 디스크 여유
uv run python scripts/fetch_models.py --plan       # 받을 파일과 합계 출력 (20GB 상한 검사)
uv run python scripts/fetch_models.py --download --only stt,mlx,cosyvoice3

# 2) 엔진 서버 (각각 별도 터미널, 또는 scripts/dev.sh)
cd engines/mlx_engines && uv sync && uv run python serve.py --engine voxcpm2  --port 8101
cd engines/cosyvoice3  && uv sync && uv run python serve.py --port 8102
cd engines/mlx_engines && uv run python serve.py --engine qwen3tts --port 8103
cd engines/mlx_engines && uv run python serve.py --engine kokoro   --port 8104

# 3) 웹 빌드와 메인 서버
cd web && pnpm install && pnpm build && cd ..
uv run uvicorn server.app:app --port 8000
open http://localhost:8000          # 상담 데모
open http://localhost:8000/blind    # 블라인드 비교
open http://localhost:8000/report   # 측정 리포트

# 4) 오프라인 작업
uv run python scripts/gen_blind_set.py --locales en,zh-CN,zh-TW,ja
uv run python scripts/bench_tts.py --engines all --locales all --repeats 3
uv run python scripts/report.py
```

### 8.5 모델 다운로드 크기

Hugging Face API(`/api/models/{id}?blobs=true`)로 2026-10-30에 확인한 파일 크기다 [S34].

| 구분 | 저장소 | 받을 파일 | 크기(GB) | 필수 |
|---|---|---|---|---|
| STT | mlx-community/whisper-large-v3-turbo | 전체 | 1.61 | 필수 |
| STT | mlx-community/Qwen3-ASR-0.6B-bf16 | 전체 | 1.57 | 선택 |
| TTS weak1 | mlx-community/VoxCPM2-bf16 | 전체 | 4.96 | 필수 |
| TTS weak2 | FunAudioLLM/Fun-CosyVoice3-0.5B-2512 | `llm.pt`(또는 `llm.rl.pt` 중 하나) 2.03, `flow.pt` 1.33, `speech_tokenizer_v3.onnx` 0.97, `CosyVoice-BlankEN/` 0.99, `hift.pt` 0.08, `campplus.onnx` 0.03 (`*.batch.onnx`, `flow.decoder.estimator.fp32.onnx` 제외, 저장소 전체는 9.75) | 5.42 | 필수 |
| TTS baseline | mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16 | 전체 | 4.54 | 필수 |
| TTS 대조군 | mlx-community/Kokoro-82M-bf16 | 전체 | 0.39 | 필수 |
| 턴 결정 | pipecat-ai/smart-turn-v3 | `smart-turn-v3.2-cpu.onnx` | 0.01 | 필수 |
| VAD | @ricky0123/vad-web npm 패키지(Silero ONNX 포함) | npm | 미확인(수 MB 수준인지 구현 시 확인) | 필수 |
| G2P | pyopenjtalk + UniDic 사전 (Kokoro ja, 일본어 가나 CER) | pip + 사전 | 미확인 | 필수 |
| 합계 | | | 필수 16.93 / 선택 포함 18.50 (npm, UniDic 제외) | |

예비(대체 시에만 받음): `mlx-community/VoxCPM2-8bit` 3.23 (bf16이 너무 느릴 때 실시간 데모용), `lucasnewman/f5-tts-mlx` 4.04 (CosyVoice3가 Mac에서 안 될 때), `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16` 2.52 (지연 비교용). 예비를 받을 때는 합계 20GB를 넘지 않게 다른 항목을 지운다(예: CosyVoice3 5.42를 지우고 F5 4.04).

실행 메모리 예산(48GB): LLM 약 23GB + Whisper 약 1.6GB + 엔진 하나(최대 약 5~6GB). 엔진 네 개를 동시에 다 올리면 약 15GB가 더 들어 40GB 근처가 되므로, 데모는 선택한 엔진만 올리고 나머지는 지연 로드(`--lazy`)와 유휴 해제(`--idle-unload 600`)를 둔다.

---

## 9. 구현 task 분할

각 task는 앞 task의 산출물만 쓰고 혼자 검증할 수 있게 나눴다. "리뷰 권장"은 계약, 상태, 취소, 블라인드 보장처럼 테스트로 잡기 어려운 부분이다.

| ID | 제목 | 선행 | 내용 | 완료 기준 | 리뷰 권장 |
|---|---|---|---|---|---|
| T1 | 골격과 모델 받기 | 없음 | 8.2 디렉터리, uv 프로젝트들, `check_env.py`, `fetch_models.py`(`--plan`, `--download`, `allow_patterns`, 20GB 상한 검사), `.gitignore`에 `models/`, `data/logs/`, `data/blind/audio/` | `--plan` 출력 합계가 8.5 표와 일치하고 20GB 이하. `check_env.py`가 ffmpeg, ollama 모델 존재를 보고 | 아니오 |
| T2 | 정규화와 고유명사 사전 | 없음 | `data/lexicon.yaml`(5.1 전체), `normalize/*`(raw/lex/full), `parse_spoken_code`, 숫자 규칙 4개 언어 | pytest 통과. `scripts/normalize_demo.py`가 6.1 문장 전체의 raw/lex/full 표를 출력하고 5.2 예시 표의 기대값과 같음 | 아니오 |
| T3 | mlx 엔진 서버 | T1 | `engines/mlx_engines/serve.py`(VoxCPM2, Qwen3-TTS, Kokoro), 8.3 계약, 참조 음성 클로닝, 24kHz 리샘플, 연결 끊김 시 중단 | `scripts/smoke_tts.py --engine X --locale L`이 지원 언어마다 무음 아닌 WAV를 만들고 합성 시간을 출력. `/v1/info` 응답이 계약과 같음 | 예 (엔진 계약) |
| T4 | CosyVoice3 엔진 서버 | T1 | 별도 uv 프로젝트, MPS 경로(PR #1869 패치 적용 방식 결정), 실패 시 CPU, 같은 계약 | T3과 같은 스모크 통과(en, zh-CN, zh-TW, ja, yue). 반나절 안에 MPS/CPU 둘 다 안 되면 중단하고 10장 대안으로 escalation | 예 |
| T5 | STT 서비스 | T1 | `stt/*`(Whisper, 선택 Qwen3-ASR), 언어 지정/자동, OpenCC, `initial_prompt` 사전 주입, `bench_stt.py` | T3이 만든 4개 언어 WAV를 인식해 출력과 시간을 표로 남김. zh-TW 결과가 번체로 나옴 | 아니오 |
| T6 | LLM 에이전트 (텍스트만) | T2 | 프롬프트 템플릿, 도구 3개, 가짜 DB와 정책 계산, Ollama 스트리밍 + 도구 호출 + `think:false`, 텍스트 CLI | 시나리오 테스트: 4개 언어 × (조회 성공, 성 불일치, 취소 정책, 상담원 연결) 16개에서 올바른 도구 호출, 응답 언어/문자 체계 일치(zh-TW 번체, ja 가나 포함), 길이 기준 위반 로그 | 예 (도구 계약, 정책 계산) |
| T7 | TTS 벤치와 발음 리포트 | T2, T3, T4, T5 | `bench_tts.py`, `report.py`, 7장 지표 전부, raw 대 full 비교 | `reports/bench-*.md`에 엔진 × 언어 모든 칸이 값 또는 `실패: 사유`로 채워짐. 1.4 표의 "미측정" 칸을 채울 수치 확보 | 아니오 |
| T8 | 블라인드 세트와 비교 페이지 | T2, T3, T4 | `gen_blind_set.py`, `/blind` 페이지, 토큰 기반 오디오 제공, 결과 JSONL, `/blind/results` 집계 | 매니페스트 생성. 브라우저 응답에 엔진 이름이 없음(네트워크 탭 확인). 10표 저장 후 집계 화면 수치가 `aggregate.py` 단위 테스트 기대값과 같음 | 예 (블라인드 보장) |
| T9 | 실시간 서버 파이프라인 | T2, T3, T5, T6 | `/ws/call`, 4.3 순서, 문장 분할, 안내 음성 캐시, 취소, 턴 로그 | `scripts/ws_wav_client.py`로 4개 언어 WAV를 보내 오디오를 받고 턴 로그에 4.5 서버 포인트가 모두 기록됨. `barge_in` 후 200ms 안에 `turn.cancelled` 수신 | 예 (상태, 취소) |
| T10 | 브라우저 상담 데모 | T9 | `web/src/call`, VAD, 재생 큐, barge-in, 자막, 지연 HUD, 엔진/언어/정규화 선택 | Chrome에서 4개 언어 대화 1회씩 완료. barge-in 시 재생 정지까지 100ms 이하(클라이언트 로그). 턴 로그에 `c_vad_end`, `c_play_start` 기록 | 아니오 |
| T11 | 턴 결정과 통화 지표 | T9, T10 | Smart Turn v3.2 ONNX 토글, 세션 지표(barge-in, 발화량, 길이) | 토글 on/off 턴 로그 비교 표. 세션 지표가 `/report`에 엔진별로 나옴 | 아니오 |
| T12 | README와 최종 리포트 | 전부 | 실행 문서, 1.4/7장 실측값 반영, 미확인 항목 갱신 | 새 터미널에서 README만 보고 데모와 블라인드 페이지 실행 가능 | 아니오 |

병렬 가능: T2, T3, T4, T5는 T1 뒤에 동시에 진행 가능. T6은 T2 뒤. T7, T8, T9는 앞 단계 뒤에 동시 진행 가능.

---

## 10. 위험과 대안

| 위험 | 신호 | 대안 |
|---|---|---|
| VoxCPM2가 Mac에서 실시간보다 느림 | 공개 수치부터 bf16 RTF 약 2.08 [S5] | 블라인드 비교는 미리 합성이라 영향 없음. 실시간 데모에서는 8bit(RTF 약 1.18) 또는 4bit를 시도하고, 그래도 느리면 "weak 병목 체감용(느림)" 표시를 붙여 선택만 가능하게 둔다. inference timesteps를 낮추는 실험은 품질 변화와 함께 기록 |
| CosyVoice3가 MPS에서 안 돎 | PR #1869 미병합, float64 문제, 스트리밍 멈춤, 교차 언어 클로닝 실패 보고 [S8] | (1) PR 패치를 고정 커밋에 적용한 vendor 사본 (2) CPU 실행(속도는 블라인드 사전 합성에만 쓰면 됨) (3) speech-swift + MLX 변환본 [S9] (4) 그래도 안 되면 weak2를 F5-TTS(MLX, en/zh만, CC-BY-NC로 PoC 한정 표시)로 바꾸고 ja는 weak1만 비교 |
| Qwen3-TTS가 mlx-audio에서 클로닝 품질이 낮음 | 스모크에서 화자 불일치 | `ref_text`를 반드시 넣고, 그래도 문제면 0.6B와 비교해 원인 분리. baseline 대체: Spark-TTS(MLX, en/zh만, CC-BY-NC-SA) |
| Kokoro zh 품질이 대조군으로도 너무 낮음 | VOICES.md 등급 D [S21] | MeloTTS(MIT, PyTorch CPU)로 zh만 교체 |
| 비상업 라이선스 모델 사용 | F5-TTS, Spark-TTS, OmniVoice | 기본 선정은 모두 Apache-2.0. 대체로 비상업 모델을 쓰면 UI와 리포트에 "PoC 한정, 상업 이용 불가" 배지를 붙이고 서비스 후보에서 뺀다 |
| LLM 첫 토큰이 느림 | `llm_ttft` 1초 초과 | 도구 정의 축약, 대화 기록 최근 N턴만, `qwen3.5:9b` 다운로드(승인 필요) |
| LLM과 TTS가 GPU를 동시에 써서 지연 급증 | 데모 턴 로그 TTS 구간이 단독 벤치보다 크게 느림 | 문장 i+1 합성을 LLM 스트림 종료 뒤로 미루는 옵션, 대조군 엔진으로 실시간 데모 기본값 변경 |
| 실시간 자체가 불가 (전체 지연 3초 이상) | 턴 로그 | 안내 음성 즉시 재생, 첫 문장을 쉼표 단위로 더 짧게, 실시간 데모는 대조군 + 후보는 블라인드 비교로 역할 분리 |
| barge-in 오탐(스피커 에코) | 재생 중 사용자 발화 없이 중단 | 헤드폰 권장, 재생 중 VAD 임계값 상향, 최소 발화 길이 증가 |
| 대만 억양 평가 불가 | zh-TW 평가자 없음 | BreezyVoice(대만 특화, Apache-2.0)를 추가 후보로 검토(크기와 Mac 동작 미확인), 평가자 확보 전까지 zh-TW 억양 결론 보류 |
| 참조 음성 출처와 동의 | 클로닝용 참조 음성 필요 | 사내 원어민 직원 녹음(동의서) 우선. 외부 공개 코퍼스를 쓰면 라이선스를 매니페스트에 기록 |
| 디스크 부족 | 여유 약 41GB | `fetch_models.py`가 20GB 상한과 남은 디스크를 검사하고 초과 시 중단 |

### 구현 전에 정해야 할 것 (코디네이터 확인 필요)

1. 클로닝 참조 음성 출처: 사내 녹음(언어별 원어민, 10초) 가능 여부. 안 되면 각 모델의 프리셋 음성으로 시작하고 화자 차이 편향을 리포트에 적는다.
2. CosyVoice3 MPS 패치(PR #1869)를 vendor 사본으로 들여오는 것 허용 여부.
3. `pyopenjtalk` UniDic 사전, (필요 시) `brew install espeak-ng` 설치 허용 여부. misaki 영어는 espeak-ng 없이도 동작한다고 문서에 적혀 있어 기본 계획에는 넣지 않았다 [S22].
4. 광둥어를 블라인드 세트에 넣을지(현재 선택 3문장).

---

## 11. 미확인 항목 모음

- VoxCPM2: 정확한 공개 날짜, AudioVAE 학습 데이터, 언어별 학습 분포(B1), 대만 억양, PyTorch MPS 동작, mlx-audio 스트리밍 지원, mlx-community 수치를 잰 칩.
- Fun-CosyVoice3: 토크나이저 학습 데이터의 언어별 비율, 대만 억양, Mac RTF, 사용하는 보코더 이름, 요구 Python 버전.
- Qwen3-TTS: 토크나이저 단독 학습 데이터(B1), 광둥어, 대만 억양, Apple RTF, mlx-audio 스트리밍.
- Kokoro: semantic teacher/PL-BERT 등 보조 모델 여부(B3), misaki zh의 번체 처리, Apple RTF.
- MeloTTS: 공개 날짜, 클로닝, teacher.
- BreezyVoice: 구조, 지원 언어, 크기, Mac 동작.
- OmniVoice: Higgs-audio 토크나이저의 teacher와 학습 언어.
- F5-TTS v1 체크포인트 날짜.
- STT: mlx-whisper와 Qwen3-ASR의 M4 Pro 처리 시간, Qwen3-ASR의 간체/번체 출력 방식. 음성만으로 대만/본토 보통화를 구분하는 방법.
- `@ricky0123/vad-web` 라이선스와 기본 Silero 버전(저장소에 v5, v6 파일이 같이 있음), 옵션 이름.
- Ollama `qwen3.5:35b-a3b`의 라이선스 표기, 이 모델에서 스트리밍 + 도구 호출 동작, 첫 토큰 지연.
- pyopenjtalk UniDic 사전 크기.

---

### 11.1 구현으로 확인한 항목

- VoxCPM2 MLX: 문장 전체 합성 후 반환, Qwen3-TTS: 실제 incremental streaming, Kokoro: 문장 단위 반환 (`engines/mlx_engines/REPORT.md`).
- CosyVoice3: 고정 PR #1869 vendor로 MPS 및 CPU 실행 확인, Python 3.10.20, 단독 Mac RTF는 1.4 실측으로 갱신 (`engines/cosyvoice3/REPORT.md`, `reports/T7-T8-REPORT.md`).
- Kokoro zh-TW는 간체 G2P 경로이며 대만 다음자/억양은 미확인. 기존 일본어 사전 재사용, 추가 UniDic/espeak-ng 설치 없음 (`engines/mlx_engines/REPORT.md`).
- Whisper 처리시간은 T5/T9/T10에 실측. Qwen3-ASR은 미설치로 미확인 유지. vad-web 0.0.30의 v5, redemptionMs/minSpeechMs API 사용 확인 (`reports/T10-REPORT.md`).
- Ollama 스트리밍+도구 호출은 동작하지만 도구 선택 누락이 남음. 최종 품질은 T6.1 기본 13/16, 변형 포함 39/48, 대화 8/20이며 초기 T6 통과를 최종 품질로 사용하지 않음 (`reports/T6.1-REPORT.md`).
- Smart Turn v3.2 CPU 8,679,182 bytes 로컬 실행 확인. 턴 판정 개선의 정확도, 자연 고객 발화, 대만 억양은 미확인 유지 (`reports/T11-REPORT.md`).

## 12. 근거

- [S1] 토스 AIOC TTS 발표 정리 (로컬): `derek_lab/blog/src/content/insights/toss-ai-call-voice-tts.mdx`, 원본 https://www.youtube.com/watch?v=j41oq_xbTgs
- [S2] VoxCPM2 모델 카드: https://huggingface.co/openbmb/VoxCPM2
- [S3] VoxCPM 논문: https://arxiv.org/abs/2509.24650
- [S4] VoxCPM1.5 모델 카드: https://huggingface.co/openbmb/VoxCPM1.5
- [S5] mlx-community VoxCPM2 카드(Apple Silicon RTF): https://huggingface.co/mlx-community/VoxCPM2-8bit
- [S6] Fun-CosyVoice3 모델 카드: https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512
- [S7] CosyVoice 3 논문: https://arxiv.org/abs/2505.17589
- [S8] CosyVoice Apple Silicon(MPS) PR #1869: https://github.com/FunAudioLLM/CosyVoice/pull/1869
- [S9] CosyVoice3 MLX 변환본: https://huggingface.co/aufklarer/CosyVoice3-0.5B-MLX-bf16
- [S10] Qwen3-TTS 모델 카드: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
- [S11] Qwen3-TTS 기술 보고서: https://arxiv.org/abs/2601.15621
- [S12] WavLM 논문(사전학습 데이터 94K시간 영어): https://arxiv.org/abs/2110.13900
- [S13] F5-TTS 모델 카드: https://huggingface.co/SWivid/F5-TTS
- [S14] F5-TTS 논문: https://arxiv.org/abs/2410.06885
- [S15] F5-TTS MLX: https://huggingface.co/lucasnewman/f5-tts-mlx
- [S16] Spark-TTS 모델 카드: https://huggingface.co/SparkAudio/Spark-TTS-0.5B
- [S17] Spark-TTS 논문: https://arxiv.org/abs/2503.01710
- [S18] OmniVoice 모델 카드: https://huggingface.co/k2-fsa/OmniVoice
- [S19] OmniVoice 논문: https://arxiv.org/abs/2604.00688
- [S20] Kokoro-82M 모델 카드: https://huggingface.co/hexgrad/Kokoro-82M
- [S21] Kokoro 음성 목록과 등급: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
- [S22] misaki G2P: https://github.com/hexgrad/misaki
- [S23] MeloTTS: https://github.com/myshell-ai/MeloTTS
- [S24] BreezyVoice 모델 카드: https://huggingface.co/MediaTek-Research/BreezyVoice-300M
- [S25] BreezyVoice 논문: https://arxiv.org/abs/2501.17790
- [S26] mlx-audio (지원 모델 목록, OpenAI 호환 서버): https://github.com/Blaizzy/mlx-audio
- [S27] Qwen3-ASR 모델 카드: https://huggingface.co/Qwen/Qwen3-ASR-1.7B
- [S28] mlx-whisper: https://github.com/ml-explore/mlx-examples/tree/main/whisper , 모델 https://huggingface.co/mlx-community/whisper-large-v3-turbo , Whisper 라이선스(MIT) https://github.com/openai/whisper
- [S29] Smart Turn: https://github.com/pipecat-ai/smart-turn , 모델 https://huggingface.co/pipecat-ai/smart-turn-v3 (BSD-2-Clause)
- [S30] VAD 웹 패키지: https://github.com/ricky0123/vad
- [S31] Ollama qwen3.5 라이브러리: https://ollama.com/library/qwen3.5
- [S32] Ollama thinking 끄기: https://docs.ollama.com/capabilities/thinking
- [S33] Ollama 스트리밍 + 도구 호출: https://ollama.com/blog/streaming-tool
- [S34] 크기 확인에 쓴 API: `https://huggingface.co/api/models/{repo_id}?blobs=true` (2026-10-30 조회)
