# 🎙️ STT 서빙 (vLLM 통합형)

vLLM transcription 엔드포인트(`/v1/audio/transcriptions`)로 STT 모델을 OpenAI 호환 API로 노출.
Qwen3-ASR-1.7B와 Whisper-large-v3을 동시에 띄워 한국어 정확도를 비교한다.

> 모델 선정 근거 및 후보 비교는 [`MODEL_STUDY.md`](MODEL_STUDY.md) 참조.

---

## 📦 구성

| 인스턴스 | 모델 | GPU | 포트 | 무게 |
|---------|------|:---:|:----:|:----:|
| `qwen3_asr` | `Qwen/Qwen3-ASR-1.7B` | 0 | 7170 | 1.7B (~4GB BF16) |
| `whisper_v3` | `openai/whisper-large-v3` (baseline) | 1 | 7171 | 1.55B (~3GB BF16) |

엔드포인트: `POST http://localhost:<port>/v1/audio/transcriptions` (OpenAI 호환).

```
llm-serving/stt/
├── MODEL_STUDY.md       # 후보 모델 비교 / 시나리오 분석
├── README.md            # 본 문서
├── start.sh             # 기동/중지/상태 (configs/*.yaml 자동 순회)
├── configs/
│   ├── qwen3_asr.yaml   # Qwen3-ASR-1.7B 설정
│   └── whisper_v3.yaml  # Whisper-large-v3 설정
└── logs/                # 인스턴스 stdout/stderr (자동 생성)
```

---

## 🚀 사용법

```bash
cd llm-serving/stt

./start.sh              # 두 인스턴스 동시 기동
./start.sh status       # UP/DOWN 확인
./start.sh stop         # 전체 중지
./start.sh restart      # 재시작
```

상태 확인:

```bash
curl http://localhost:7170/v1/models   # Qwen3-ASR
curl http://localhost:7171/v1/models   # Whisper

curl http://localhost:7170/health
curl http://localhost:7171/health
```

---

## 🔬 추론 호출 예 (curl)

```bash
# Qwen3-ASR-1.7B
curl http://localhost:7170/v1/audio/transcriptions \
  -F "file=@samples/sample_ko.wav" \
  -F "model=Qwen3-ASR-1.7B" \
  -F "language=ko"

# Whisper-large-v3
curl http://localhost:7171/v1/audio/transcriptions \
  -F "file=@samples/sample_ko.wav" \
  -F "model=whisper-large-v3" \
  -F "language=ko"
```

OpenAI Python SDK도 호환:

```python
from openai import OpenAI

qwen = OpenAI(base_url="http://localhost:7170/v1", api_key="dummy")
whisper = OpenAI(base_url="http://localhost:7171/v1", api_key="dummy")

with open("samples/sample_ko.wav", "rb") as f:
    out_qwen = qwen.audio.transcriptions.create(
        model="Qwen3-ASR-1.7B", file=f, language="ko",
    )
print(out_qwen.text)
```

> `model` 식별자는 vLLM이 HF 모델 ID 마지막 segment를 자동으로 `served_model_name` 으로 사용한다.
> 정확한 이름은 `GET /v1/models` 응답으로 확인.

---

## ⚠️ 운영 주의

### GPU 점유 충돌 (LLM 인스턴스와 동시 운영 불가)

현재 `llm-serving/vllm/`의 LLM 인스턴스(Gemma 4 26B-A4B)가 **L40S [0,1] TP=2**로 두 GPU를 모두 점유한다. STT를 띄우려면 LLM을 먼저 중지해야 한다:

```bash
cd ../vllm && ./start.sh stop
cd ../stt  && ./start.sh
```

> 동시 운영(LLM + STT)이 필요해지면:
> - LLM을 GPU 0 단독(`gpus: [0]`, `tensor_parallel_size: 1`)으로 축소
> - STT 두 모델은 GPU 1 공유 (각자 `gpu_memory_utilization: 0.30~0.40`)
> - 또는 PRO 6000 96GB 운영 환경 셋업 후 분리

### 모델 다운로드

- 첫 실행 시 `/models/STT/<HF_ID>/` 경로로 자동 다운로드 (런처가 처리)
- Qwen3-ASR-1.7B / Whisper-large-v3 둘 다 Apache 2.0 / MIT 라이선스라 HF_TOKEN 불필요
- 모델 합계 ~6GB (Qwen3-ASR ~4GB + Whisper ~3GB BF16)

### 첫 기동 시간

- 모델 로딩 + CUDA 그래프 캡처에 1~3분 소요 (모델 크기 작아 LLM 대비 빠름)
- `./start.sh status`에서 UP 표시되면 추론 가능

---

## 🔍 트러블슈팅

### `Task transcription is not supported for model …`

vLLM이 모델을 transcription task로 인식 못 하는 경우:
- vLLM 버전 확인: `pip show vllm` (0.10+ 권장)
- 모델 로드 로그 확인: `tail -f logs/qwen3_asr.log` 에서 `model_executor` 초기화 단계 메시지 확인
- 임시 우회: config의 `task: transcription` 라인을 제거하고 자동 감지에 맡김

### OOM (CUDA out of memory)

- `gpu_memory_utilization` 을 0.3~0.4로 낮춤
- `max_num_seqs` 를 4 이하로 낮춤
- 같은 GPU에 다른 프로세스가 살아있는지 `nvidia-smi`로 확인

### 포트 충돌

- 기본 포트(7170/7171)가 점유 중이면 config의 `port` 변경
- 또는 기존 점유 프로세스 종료: `lsof -i :7170`

---

## 📋 다음 단계 (PoC)

- [ ] **한국어 테스트 셋 준비** (자체 데이터 또는 Zeroth-Korean / FLEURS Korean / KsponSpeech 일부)
- [ ] **`test_stt.py` 작성** — WER / RTF / latency / 정성 평가 (고유명사·숫자·전문용어)
- [ ] **두 모델 한국어 비교 결과 정리** (`MODEL_STUDY.md` 부록)
- [ ] **게이트웨이 통합** — vLLM 게이트웨이를 transcription 엔드포인트까지 라우팅하도록 확장 (모델별 단일 엔드포인트 노출)
- [ ] **`STT_OPS_GUIDE.md`** 작성 (PoC 결과 반영)
