# 🎙️ STT 모델 스터디 (2025-2026)

> **목적**: `llm-serving/stt/` 디렉토리 PoC 진입 전, 후보 모델 비교 및 선택 근거 정리.
> **갱신**: 2026-04-29 (초안)

---

## 1. 배경 / 운영 환경

| 항목 | 내용 |
|------|------|
| **개발 GPU** | NVIDIA L40S 46GB × 2 (Ada, SM 8.9) |
| **운영 GPU** | NVIDIA RTX PRO 6000 Blackwell Workstation 96GB (Blackwell, SM 10.x) — 셋업 예정 |
| **기존 인프라** | vLLM 0.19.1 + 게이트웨이 (`llm-serving/vllm/`) — Gemma 4 26B-A4B (MoE, FP8) 운영 중 |
| **통합 방향** | **vLLM 통합형** — 기존 게이트웨이가 LLM/STT 동시 라우팅. `llm-serving/stt/`는 vLLM 런처 변형으로 구성 |
| **언어 우선순위** | 한국어 ≫ 영어 ≫ 기타 |

### 1.1 vLLM 통합형 결정 근거

- 기존 vLLM 런처/게이트웨이/모니터링 자산 재사용 → 운영 단순성
- vLLM이 OpenAI 호환 `/v1/audio/transcriptions` 엔드포인트를 정식 지원 (2024 후반부터)
- LLM과 STT를 동일 게이트웨이 뒤에 두면 클라이언트 인터페이스 일원화
- 단점: 실시간 스트리밍 STT는 vLLM이 아직 약함 → 라이브 자막용은 별도 검토 (Voxtral Realtime 등)

---

## 2. 평가 기준

| 기준 | 가중치 | 설명 |
|------|:------:|------|
| **한국어 지원** | 🔴 필수 | 명시적 학습 + 가능하면 벤치 수치 확인 |
| **라이선스** | 🔴 필수 | 상업 사용 가능한 오픈 가중치 (Apache 2.0 / MIT 우선) |
| **vLLM 호환성** | 🔴 필수 | 공식 지원 또는 day-zero. NeMo 전용은 후순위 |
| **정확도 (WER)** | 🟡 높음 | FLEURS / Common Voice / 자체 한국어 셋 |
| **모델 크기 / VRAM** | 🟡 높음 | Gemma 4 26B-A4B와 GPU 공존 가능 여부 |
| **지연 (Latency)** | 🟢 중 | 시나리오에 따라 (배치 vs 실시간) |
| **유지보수 활성도** | 🟢 중 | 출시일·repo 활성도·이슈 응답성 |
| **부가 기능** | 🟢 중 | 디아라이제이션, 단어 단위 타임스탬프, 음성 출력 |

---

## 3. 후보 모델 매트릭스

> ⚠️ **검증 원칙**: 모든 셀은 **모델카드 본문에서 확인된 사실만 기재**합니다. 검색 엔진 요약은 모델카드와 자주 어긋남(예: 검색 요약은 "Voxtral 13개 언어 한국어 포함"이라 했지만 Small 24B 모델카드 본문은 8개 언어만 명시).

### 3.1 핵심 후보 — 한국어 모델카드 명시 + vLLM 호환

| 모델 | 출시 | 파라미터 | 한국어 (모델카드) | vLLM | 라이선스 | 비고 |
|------|------|---------|:----------------:|:----:|---------|------|
| **`mistralai/Voxtral-Mini-4B-Realtime-2602`** | 2026-02-03 | 4B (LM 3.4B + Audio 970M) | ✅ (FLEURS 표 13개: ar/de/en/es/fr/hi/it/nl/pt/zh/ja/**ko**/ru) | ✅ 공식 | Apache 2.0 | 실시간 STT 특화, <500ms latency, configurable 80ms~2.4s |
| **`Qwen/Qwen3-Omni-30B-A3B-Instruct`** | 2025-09 | 35B total, MoE A3B (active ~3B) | ✅ (음성 입력 19개에 명시) | ✅ | Apache 2.0 | omni-modal, 음성 입출력, 119 텍스트 / 19 음성 입력 / 10 음성 출력 |
| **`Qwen/Qwen3-ASR-1.7B`** | 2025-09 | 2B (1.7B 표기) | ✅ (30개 언어 + 22 중국 방언에 명시) | ✅ Day-zero | Apache 2.0 | ASR 전용 |
| **`Qwen/Qwen3-ASR-0.6B`** (자매) | 2025-09 | 0.6B | ✅ (1.7B와 동일 30개 언어) | ✅ Day-zero | Apache 2.0 | 초경량. 128 concurrency 시 2000x throughput |

### 3.2 보조 / Baseline / 비교군

| 모델 | 출시 | 한국어 (모델카드) | vLLM | 비고 |
|------|------|:----------------:|:----:|------|
| **`openai/whisper-large-v3-turbo`** | 2024-09 | ✅ (99개 언어 일반 명시) | ✅ 공식 | Baseline. 809M, MIT. FP8 양자화(`RedHatAI/whisper-large-v3-FP8-dynamic`) 존재 |
| **`openai/whisper-large-v3`** | 2023-11 | ✅ | ✅ | 1.55B, MIT. 정확도 최우선 baseline |
| **`mistralai/Voxtral-Small-24B-2507`** | 2025-07 | ❌ (모델카드 본문 8개: en/es/fr/pt/hi/de/nl/it — **한국어 미명시**) | ✅ | 후보 제외. 한국어는 학습 외 — 자체 측정 시 동작할 수 있으나 보장 없음 |
| **`mistralai/Voxtral-Mini-3B-2507`** | 2025-07 | ❌ (8개 동일) | ✅ | 후보 제외. 모델카드는 "3B"지만 metadata는 5B params 표기 |
| **NVIDIA Canary-Qwen 2.5B** | 2025-2026 | ❌ (영어 위주) | ⚠️ NeMo 우선 | OpenASR #1 (5.63% WER), SALM 아키텍처 |
| **IBM Granite 4.0 1B Speech** | 2026-03 | ❌ (영어/유럽 5개 + 일·중 번역) | ⚠️ 미확인 | OpenASR 상위 |
| **Cohere Transcribe** | 2026-04 | ⚠️ APAC 한·일·중·베 (검색 요약 기반, 모델카드 직접 확인 미수행) | ⚠️ 미확인 | 라이선스 / 오픈 가중치 여부 확인 필요 |
| **NVIDIA Parakeet TDT 0.6B v3** | 2025 | ❌ (유럽 25개) | ⚠️ NeMo | 한국어 학습 데이터 없음 |

### 3.3 한국어 fine-tuning 베이스 (도메인 특화 시)

| 모델 | 비고 |
|------|------|
| `kresnik/wav2vec2-large-xlsr-korean` | XLS-R 기반, KsponSpeech / Zeroth-Korean 학습 |
| `Kkonjeong/wav2vec2-base-korean` | 가벼운 wav2vec2 fine-tune |
| `sooftware/KoSpeech` | PyTorch 한국어 ASR 툴킷 (KsponSpeech 전처리 포함) |

> 📌 PoC 단계에선 사용하지 않음. 도메인(의료/법률/방언) 특화가 필요해진 단계에서 검토.

---

## 4. 후보 상세 (모델카드 본문 기준)

### 4.1 Voxtral 시리즈 (Mistral)

**HuggingFace `mistralai/` 라인업 (모델 인덱스 페이지에서 확인)**:
- `Voxtral-Small-24B-2507` — 2025-07, 24B, **언어 8개 (한국어 ❌)**, Apache 2.0
- `Voxtral-Mini-3B-2507` — 2025-07, 모델카드는 "3B"지만 metadata는 5B params, **언어 8개 (한국어 ❌)**, Apache 2.0
- `Voxtral-Mini-4B-Realtime-2602` — 2026-02-03, 4B (LM 3.4B + Audio 970M), **언어 13개 (한국어 ✅)**, Apache 2.0
- `Voxtral-4B-TTS-2603` — 2026-03, TTS (별개)

**한국어 STT 가능 모델은 `Voxtral-Mini-4B-Realtime-2602` 단 하나** (2026-04-29 시점, 모델카드 본문 기준).

**Voxtral Mini 4B Realtime 특징** (모델카드):
- LLM 백본(Ministral 계열) + 음성 인코더 통합
- 실시간 ASR 전용 — `<500ms` 전사 지연
- 80ms ~ 2.4s 사이 latency configurable (지연/정확도 trade-off)
- 480ms 지연에서 leading offline open-source transcription 모델과 동등
- 주 용도: private meeting transcription, live subtitle, 실시간 어시스턴트

**VRAM 추정** (BF16): 4B 가중치 ≈ 8~10GB → L40S 1장 여유, Gemma 4와 GPU 공존 가능. PRO 6000 시 LLM과 한 GPU 공유 가능.

**한계 / 미확인**:
- FLEURS Korean 정확한 WER 수치는 모델카드 표에 한국어 행이 있으나, 직접 본문 표를 fetch하지 않은 상태 — **PoC에서 자체 측정 권장**
- vLLM streaming transcription 성숙도는 vLLM 공식 문서 추가 확인 필요

### 4.2 Qwen3-Omni 30B-A3B (Alibaba)

**HuggingFace `Qwen/` 라인업**:
- `Qwen3-Omni-30B-A3B-Instruct` — 표준 (모델카드 fetch 완료)
- `Qwen3-Omni-30B-A3B-Thinking` — reasoning 강화
- `Qwen3-Omni-30B-A3B-Captioner` — 캡션 특화

**모델카드 본문 확인 사실**:
- **35B total params, MoE 아키텍처 (`qwen3_omni_moe`)** — "30B-A3B" 명칭은 마케팅, 실제 total 35B
- Apache 2.0
- **Speech Input 19개 언어 명시** (한국어 포함): English, Chinese, **Korean**, Japanese, German, Russian, Italian, French, Spanish, Portuguese, Malay, Dutch, Indonesian, Turkish, Vietnamese, Cantonese, Arabic, Urdu (+1)
- 텍스트 119개, 음성 출력 10개
- Any-to-Any multimodal (텍스트·이미지·오디오·비디오)

**VRAM 추정** (BF16): MoE 35B 풀 가중치 ≈ 70GB → PRO 6000 단독 가능. L40S 2장에 TP=2 + FP8 양자화 필수.

**고려 사항**:
- STT 전용으로는 오버스펙. 음성 출력·이미지·비디오까지 활용할 계획일 때 가치 극대화
- Gemma 4 26B-A4B와 동일한 MoE 운영 패턴 → 우리 vLLM 노하우 그대로 적용

### 4.3 Qwen3-ASR-1.7B / 0.6B (Alibaba)

**모델카드 본문 확인 사실**:
- **`Qwen3-ASR-1.7B`**: 2B params (1.7B 표기), Apache 2.0
- **`Qwen3-ASR-0.6B`** 자매 모델: 128 concurrency 시 2000x throughput 달성 (경량 고처리량용)
- **`Qwen3-ForcedAligner-0.6B`**: 타임스탬프 예측 전용
- 30개 언어 + 22개 중국 방언 (한국어 `ko` 명시)
- DashScope 클라우드 API 별도 제공 — **"Qwen3-ASR-Flash"라는 별도 모델은 존재하지 않음** (이전 검색 요약은 부정확)

**VRAM 추정** (BF16): 1.7B ≈ 4GB, 0.6B ≈ 1.5GB → L40S 1장에 LLM과 공존 여유. PoC 가장 빠르게 시작 가능.

**한계**:
- 1.7B 사이즈상 정확도 한계 가능성 — Voxtral Mini 4B Realtime / Qwen3-Omni와 한국어 정량 비교 필요

### 4.4 Whisper Large-v3 / turbo (OpenAI) — Baseline

**모델카드 본문 확인 사실** (`openai/whisper-large-v3-turbo`):
- **MIT 라이선스**, 809M params (Large 1550M에서 디코더 32→4층 축소)
- 99개 언어 multilingual (한국어 포함, 일반 명시)
- 모델카드의 paper 날짜는 Large 시리즈 arxiv (2022-12-06) 기준이며, **turbo 자체는 OpenAI가 2024-09 발표한 distillation 변형**
- Tasks: transcription, speech translation (to English), language detection, sentence/word-level timestamp

**역할**: PoC 비교 기준선. 신모델이 한국어에서 실제로 우위인지 정량 검증.

**한계**: 디아라이제이션·음성 출력 없음 — 순수 transcription만

---

## 5. 시나리오별 추천

> 한국어 STT 후보는 본문 검증 결과 **3개로 좁혀짐**: Voxtral Mini 4B Realtime / Qwen3-Omni 30B-A3B / Qwen3-ASR-1.7B (+ baseline Whisper).

### 5.1 시나리오 A: 회의록·인터뷰 등 **배치 변환 + 정확도 우선**

**1순위**: **Qwen3-Omni-30B-A3B-Instruct**
- 한국어 음성 입력 명시 + 35B (active 3B MoE)로 정확도 기대치 높음
- diarization은 모델 자체 미지원이라 별도 wav2vec2 alignment 필요할 수 있음
- PRO 6000 셋업 후 진행 권장

**2순위**: **Voxtral Mini 4B Realtime** (배치 입력으로도 사용 가능)
- 4B로 가볍고 한국어 명시
- 단, 모델 설계가 실시간 위주 — 배치 정확도는 Qwen3-Omni 대비 측정 필요

**Baseline 비교**: Whisper-large-v3 (BF16, 1.55B, 정확도) + turbo (속도)

### 5.2 시나리오 B: 음성 챗봇·라이브 자막 등 **실시간** 위주

**1순위**: **Voxtral-Mini-4B-Realtime-2602**
- 모델카드가 명시한 유일한 한국어 지원 실시간 특화 모델
- `<500ms` 전사 지연, 80ms~2.4s configurable
- L40S 1장에 LLM과 공존 가능

**고려**: vLLM streaming transcription 엔드포인트 성숙도 확인. 부족하면 별도 FastAPI 래핑 검토

### 5.3 시나리오 C: **omni-modal**까지 확장 (음성 입출력 + 멀티모달)

**1순위**: **Qwen3-Omni-30B-A3B-Instruct**
- ASR + 음성 출력 (10개 언어) + 이미지·비디오 이해까지 단일 모델
- MoE 운영 패턴 우리 Gemma 4 노하우 그대로 적용
- PRO 6000 단독 / L40S 2장 TP=2 + FP8

### 5.4 시나리오 D: **가장 가볍게 PoC만** 먼저

**1순위**: **Qwen3-ASR-0.6B** (또는 1.7B)
- 다운로드·기동·테스트 가장 빠름
- L40S 1장 일부 (수 GB)만 사용 → Gemma 4와 자유롭게 공존
- 정확도 부족하면 1.7B → Voxtral Mini 4B Realtime → Qwen3-Omni 순으로 업그레이드

---

## 6. PoC 단계 설계 (다음 작업)

### 6.1 사전 결정 (확정 / 대기)

- [x] **주 시나리오 확정** → **D (경량 PoC)**: Qwen3-ASR-1.7B + Whisper-large-v3 동시 서빙으로 한국어 비교 (2026-04-29)
  - 선택 근거: "비슷한 무게대(1.7B vs 1.55B)에서 한국어 정확도 비교 + 빠른 vLLM 통합 검증"
  - baseline은 **Whisper-large-v3** (turbo 아님 — 무게 매칭 우선)
- [ ] **PRO 6000 운영 환경 셋업 시점** 확인 — 후속 시나리오(A·C) 진행 시점 결정용
- [ ] **테스트용 한국어 오디오 샘플 셋** 준비 (자체 데이터 / Zeroth-Korean / FLEURS Korean / KsponSpeech 일부)

### 6.2 PoC 절차 (시나리오 D 기준)

1. **모델 다운로드** — `vllm/vllm_server_launcher.py` 가 첫 기동 시 자동 다운로드 (`/models/STT/<HF_ID>/`)
2. **vLLM 기동 설정** — `configs/{qwen3_asr,whisper_v3}.yaml` (모델별 분리, 각자 GPU/포트/`task: transcription` 명시) ✅ 완료
3. **인스턴스 동시 기동** — `start.sh` 가 `configs/*.yaml` 순회하여 모델별 단일 GPU 인스턴스 기동 ✅ 완료
4. **벤치 스크립트** — `test_stt.py` (예정):
   - 한국어 샘플 (다양한 길이/화자/잡음)에 대해 WER, RTF, latency 측정
   - 정성 평가 (고유명사·숫자·전문용어)
5. **결과 비교** — Qwen3-ASR-1.7B vs Whisper-large-v3 한국어 정량 비교
6. **게이트웨이 통합** (후속) — `vllm_gateway_config.yaml` 확장하여 transcription 엔드포인트 라우팅 추가

### 6.3 디렉토리 구조 (구현 완료)

```
llm-serving/stt/
├── MODEL_STUDY.md            # 본 문서
├── README.md                 # 운영 가이드 (사용법 / 트러블슈팅)  ✅
├── start.sh                  # 인스턴스 기동/중지/상태             ✅
├── configs/
│   ├── qwen3_asr.yaml        # Qwen3-ASR-1.7B (GPU 0, :7170)       ✅
│   └── whisper_v3.yaml       # Whisper-large-v3 (GPU 1, :7171)     ✅
├── logs/                     # 인스턴스 stdout/stderr (gitignore)
├── samples/                  # 테스트 오디오 (gitignore, 예정)
└── test_stt.py               # 한국어 벤치 (예정)
```

> 설계 변경 사항 (초안 대비):
> - **`stt_server_launcher.py` 미작성** → `vllm/vllm_server_launcher.py` 그대로 재사용 (HF 다운로드 / 오프라인 모드 / 임시 config 처리 등 자산 재활용)
> - **`stt_config.yaml` 단일** → `configs/*.yaml` 모델별 분리 (이질 모델 2종 동시 서빙용)
> - **`STT_OPS_GUIDE.md`** → `README.md` 로 통일 (PoC 단계라 가이드 분리 불필요)

### 6.4 고려해야 할 운영 이슈

- **오디오 입력 크기 제한**: vLLM transcription 엔드포인트 max audio length / chunk size 정책 확인
- **샘플링 레이트**: 16kHz 표준 — 입력 자동 리샘플링 필요 여부
- **포맷**: WAV/OGG/MP3/FLAC 지원 (vLLM 공식)
- **GPU 분할**: Gemma 4와 GPU 공존 시 `gpu_memory_utilization` 조정 — Qwen3-ASR-0.6B/1.7B 또는 Voxtral Mini 4B Realtime은 LLM과 한 GPU 공존 가능, Qwen3-Omni 30B-A3B는 GPU 분리 권장 (PRO 6000 단독 또는 L40S 2장 TP=2)

---

## 7. 참고 (Sources)

### ✅ 본 문서의 사실 진술이 직접 본문 fetch로 검증된 모델카드 (2026-04-29)
- [`mistralai/Voxtral-Small-24B-2507`](https://huggingface.co/mistralai/Voxtral-Small-24B-2507) — 라이선스/언어 8개(한국어 ❌)
- [`mistralai/Voxtral-Mini-4B-Realtime-2602`](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602) — 라이선스/언어 13개(한국어 ✅)/4B 구성/지연
- [`mistralai/Voxtral-Mini-3B-2507`](https://huggingface.co/mistralai/Voxtral-Mini-3B-2507) — 언어 8개(한국어 ❌)/실제 5B
- [`mistralai/`](https://huggingface.co/mistralai) — Voxtral 라인업 인덱스
- [`Qwen/Qwen3-Omni-30B-A3B-Instruct`](https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct) — 35B total/Apache 2.0/19개 음성 입력(한국어 ✅)
- [`Qwen/Qwen3-ASR-1.7B`](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) — 30개 언어(한국어 ✅)/0.6B 자매 모델/Flash 별도 모델 부재
- [`openai/whisper-large-v3-turbo`](https://huggingface.co/openai/whisper-large-v3-turbo) — MIT/809M/99개 언어

### 보조 (검색 요약 기반 — PoC 전 본문 재검증 권장)
- [Voxtral 발표 - Mistral AI](https://mistral.ai/news/voxtral)
- [Voxtral Transcribe 2 - Mistral AI](https://mistral.ai/news/voxtral-transcribe-2)
- [Voxtral 논문 (arXiv:2507.13264)](https://arxiv.org/html/2507.13264v1)
- [Qwen3-Omni GitHub](https://github.com/QwenLM/Qwen3-Omni)
- [Qwen3-Omni 기술 보고서 (arXiv:2509.17765)](https://arxiv.org/html/2509.17765v1)
- [Qwen3-ASR 기술 보고서](https://arxiv.org/html/2601.21337v2)
- [RedHatAI/whisper-large-v3-FP8-dynamic (HF)](https://huggingface.co/RedHatAI/whisper-large-v3-FP8-dynamic)

### 벤치마크 / 리더보드
- [HuggingFace Open ASR Leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard)
- [Open ASR Leaderboard 블로그](https://huggingface.co/blog/open-asr-leaderboard)
- [Northflank STT 2026 벤치마크](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Gladia 오픈소스 STT 2026](https://www.gladia.io/blog/best-open-source-speech-to-text-models)

### vLLM STT 지원
- [vLLM Speech-to-Text Transcription 문서](https://docs.vllm.ai/en/latest/contributing/model/transcription/)
- [vLLM Whisper 예제](https://docs.vllm.ai/en/v0.7.0/getting_started/examples/whisper.html)
- [vLLM OpenAI Transcription Client 예제](https://docs.vllm.ai/en/latest/examples/online_serving/openai_transcription_client/)

### 비교 / 추가 참고
- [NVIDIA Parakeet TDT 0.6B v3 (HF)](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) — 한국어 미지원 확인
- [IBM Granite 4.0 1B Speech (HF)](https://huggingface.co/ibm-granite/granite-4.0-1b-speech)
- [Cohere Transcribe 발표](https://aitoolly.com/ai-news/article/2026-04-01-cohere-launches-transcribe-a-new-open-source-state-of-the-art-speech-recognition-model-for-enterpris)
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Modal - Whisper variants 비교](https://modal.com/blog/choosing-whisper-variants)

---

## 8. 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-04-29 (초안) | 후보 모델 매트릭스 + 시나리오별 추천 + PoC 설계 초안 (검색 요약 기반) |
| 2026-04-29 (1차 정정) | **모델카드 본문 직접 fetch 검증 후 대규모 정정**. (1) Voxtral Small 24B / Mini 3B → 모델카드 본문은 한국어 미명시 (8개 언어만) → 후보군에서 비교군으로 강등. (2) "Qwen3-ASR-Flash" 별도 모델 미존재 확인 → DashScope API 명칭. Qwen3-ASR-0.6B 자매 모델 추가. (3) Qwen3-Omni 파라미터 35B total로 정정. (4) Whisper turbo 라이선스/파라미터/출시 정정. (5) 시나리오 A·D 추천 모델 변경. (6) Sources를 본문 검증 / 검색 요약 두 그룹으로 분리. **교훈**: 검색 엔진 요약은 모델카드 본문과 자주 어긋남 → 사실 진술은 반드시 본문 fetch로 확정. |
| 2026-04-29 (2차 정정) | 자기 일관성 보강. (1) Qwen3-ASR-0.6B를 시나리오 D 1순위로 추천하면서 보조 표에 두던 분류 모순 해결 → **3.1 핵심 후보로 승격** (1.7B와 같은 시리즈/라이선스/언어 지원). (2) 6.4 GPU 분할 가이드에서 "Voxtral Small 24B"(비교군)를 우리 후보 라인업(Qwen3-Omni 30B-A3B)으로 교체. |
| 2026-04-29 (PoC 진입) | 시나리오 D 확정 + 인프라 구현 반영. (1) §6.1 시나리오 확정 (Qwen3-ASR-1.7B + Whisper-large-v3 비교, baseline은 turbo 아닌 large-v3 — 무게 매칭). (2) §6.2 PoC 절차를 actual 진행 상태로 갱신 (인프라 ✅, 벤치/게이트웨이 통합 예정). (3) §6.3 디렉토리 구조를 actual로 갱신 (`stt_server_launcher.py` 미작성/`vllm` 런처 재사용, `configs/` 모델별 분리, `STT_OPS_GUIDE.md` → `README.md`). |
