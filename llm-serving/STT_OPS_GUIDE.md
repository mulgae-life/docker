# STT 운영 가이드 (운영자용)

> **대상**: STT 서버 운영자 (서버 기동·튜닝·트러블슈팅 담당)
> **메인 모델**: `mistralai/Voxtral-Mini-4B-Realtime-2602` (BF16, 4B params)
> **게이트웨이**: `:5017` → 인스턴스 `:7172` (GPU 2)
>
> 사용자(API 호출)용 가이드: [`STT_API_GUIDE.md`](STT_API_GUIDE.md)

vLLM 0.19.1 기반 STT 게이트웨이의 **시스템 구조 / 기동·중지 / 모델 관리 / 설정 / 트러블슈팅**을 다룹니다. § 번호는 사용자 가이드(`STT_API_GUIDE.md` §1~§5)와의 cross-reference 안정성을 위해 6부터 시작합니다.

---

## 📑 목차

6. [시스템 구조](#6-시스템-구조)
7. [기동·중지·재시작](#7-기동중지재시작)
8. [모델 다운로드·관리](#8-모델-다운로드관리)
9. [인스턴스·게이트웨이 설정](#9-인스턴스게이트웨이-설정)
10. [트러블슈팅](#10-트러블슈팅)
11. [QA 체크리스트](#11-qa-체크리스트)
12. [참고 자료](#12-참고-자료)

---

## 6. 시스템 구조

### 6.1 격리 페어 + 자동 디스커버리

vLLM 클러스터(LLM)와 동일한 패턴을 STT에도 도입했습니다. 모든 STT 인스턴스/게이트웨이는 `llm-serving/stt/` 하위에서 자기 완결적으로 동작합니다.

```
클라이언트
  │
  ▼
Gateway :5017            stt/gateways/5017.yaml
  │  (transcription / realtime / chat 라우팅)
  │  HealthChecker · Admission · LB
  │
  ▼
vLLM voxtral :7172       stt/instances/voxtral.yaml
  GPU 2, Voxtral-Mini-4B-Realtime-2602
```

자동 디스커버리: `gateways/5017.yaml`의 `discover_from: ../instances` → 그 디렉토리의 `*.yaml` 중 `gateway_port: 5017` 메타키를 가진 인스턴스만 backends에 등록.

### 6.2 LLM과의 책임 분리

| 레이어 | 디렉토리 | 책임 |
|--------|---------|------|
| LLM 서빙 | `llm-serving/vllm/` | Gemma/Qwen 등 chat 모델 (게이트웨이 :5015 / :5016) |
| STT 서빙 | `llm-serving/stt/` | Voxtral 등 음성 모델 (게이트웨이 :5017) |
| 공용 코드 | `llm-serving/vllm/{vllm_server_launcher.py, vllm_gateway.py}` | 두 디렉토리가 같은 launcher/gateway 파이썬을 재사용 |

> 게이트웨이 코드는 `vllm/vllm_gateway.py` 단일 출처. STT 게이트웨이는 chat/completions 외에도 `/v1/audio/transcriptions`, `/v1/realtime` 라우트를 자동 제공합니다.

### 6.3 Runtime json 격리

launcher가 `instances/.runtime/<name>.json`에 실제 listen 포트를 기록. 게이트웨이는 이 파일을 우선 참조해 backend port를 결정합니다.

```
stt/instances/.runtime/voxtral.json   ← STT 인스턴스 runtime
vllm/instances/.runtime/gemma.json    ← LLM 인스턴스 runtime
```

각 디렉토리에 격리되므로 STT/LLM이 같은 launcher 코드를 재사용해도 runtime 파일이 섞이지 않습니다 (`vllm_server_launcher.py:main`이 yaml dirname 기준으로 RUNTIME_DIR 동적 결정).

### 6.4 디렉토리 구조

```
llm-serving/stt/
├── README.md
├── MODEL_STUDY.md             # 후보 모델 비교 / 시나리오 분석
├── start.sh                   # 빠른 기동 (instances/+gateways/ 자동 순회)
├── instances/
│   └── voxtral.yaml           # gateway_port: 5017, 내부 :7172
│   └── (qwen3_asr/whisper_v3는 비교용, gateway_port 미지정 — 직접 노출)
├── gateways/
│   └── 5017.yaml              # discover_from: ../instances
└── logs/                      # 인스턴스/게이트웨이 stdout/stderr (자동 생성)
```

---

## 7. 기동·중지·재시작

### 7.1 명령 요약

```bash
cd llm-serving/stt

./start.sh up                       # 인자 없음 → 전체 적용 confirm 프롬프트 [y/N]
./start.sh up all                   # 전체 인스턴스 + 모든 게이트웨이 기동 (확인 없이)
./start.sh up voxtral               # instances/voxtral.yaml 단독 기동 (게이트웨이 미터치)
./start.sh up 5017                  # gateways/5017.yaml 단독 기동 (인스턴스 미터치)
./start.sh down                     # 인자 없음 → 전체 중지 confirm 프롬프트 [y/N]
./start.sh down all                 # 모든 인스턴스 + 게이트웨이 중지 (확인 없이)
./start.sh down voxtral             # 인스턴스 단독 중지
./start.sh down 5017                # 게이트웨이 단독 중지
./start.sh status                   # 상태 확인
./start.sh restart                  # 인자 없음 → 전체 재시작 confirm 프롬프트 [y/N]
./start.sh restart <name>           # 단일 대상 재시작 (내부적으로 down→up)
```

`<name>`이 `instances/<name>.yaml`이면 인스턴스, `gateways/<name>.yaml`이면 게이트웨이로 자동 라우팅. `all` 명시는 확인 없이 전체 적용. 같은 이름이 양쪽에 있으면 즉시 에러.

> ⚠️ **안전 정책**: 무인자 호출은 [y/N] 기본 No로 묻는다 (다른 모델/게이트웨이를 실수로 stop시키는 사고 방지). 자동화 스크립트/cron 등 비대화 환경에서는 prompt 띄울 곳이 없으므로 무인자 호출이 거부되며 `'all'` 또는 이름을 명시해야 한다.

### 7.2 상태 의미

```
[UP]      vLLM voxtral (GPU 2, :7172, → gw :5017, PID ...)
[STARTING] ... (PID 살아있음 / health 응답 없음 — 모델 로딩 중)
[STALE]   ... (runtime PID launcher 아님/죽음 — './start.sh down <name>'으로 정리)
[DOWN]    ...

[UP]      Gateway 5017 (:5017, ready 1/1)
[STARTING] Gateway 5017 (:5017, ready 0/1 — 백엔드 대기/웜업 중)
```

### 7.3 첫 기동 소요 시간

| 단계 | 소요 |
|------|------|
| 모델 다운로드 (최초 1회) | ~35초 (17GB, network 의존) |
| weight 로딩 | ~3초 (8.38 GiB BF16) |
| KV cache 프로파일링 | ~15초 |
| CUDA graph capture (PIECEWISE) | ~1초 |
| 게이트웨이 health probe | ~10초 (백엔드 ready 잡힌 직후) |
| **합계 (재기동)** | **~30초** (다운로드 제외) |

---

## 8. 모델 다운로드·관리

### 8.1 자동 다운로드

`instances/<name>.yaml`의 `model: <HF ID>` + `download_dir: /models/STT` 조합으로 launcher가 첫 기동 시 `snapshot_download`로 다운로드. Voxtral은 Apache 2.0 → HF_TOKEN 불필요.

```yaml
model: mistralai/Voxtral-Mini-4B-Realtime-2602
download_dir: /models/STT
# → /models/STT/mistralai/Voxtral-Mini-4B-Realtime-2602/ 에 저장
```

### 8.2 폐쇄망 운영

EC2 외부망 차단 환경에서는 외부망 PC에서 사전 다운로드 → S3 → `/models/STT/` 로 동기화:

```bash
# 외부망 PC
huggingface-cli download mistralai/Voxtral-Mini-4B-Realtime-2602 \
  --local-dir ./Voxtral-Mini-4B-Realtime-2602
aws s3 sync ./Voxtral-Mini-4B-Realtime-2602 \
  s3://hgi-ai-res/models/STT/mistralai/Voxtral-Mini-4B-Realtime-2602/

# 운영계 EC2
sudo aws s3 sync s3://hgi-ai-res/models/STT/ /models/STT/
```

### 8.3 의존성 (소중함)

Voxtral은 **`soundfile`, `soxr`, `librosa`** 가 vLLM serving 시 필수 (없으면 `EngineCore failed to start`):

```bash
pip install --user soundfile soxr librosa
```

운영계 컨테이너 재배포 시 동일 ImportError가 재발하지 않도록 `aws/requirements.txt`에 영구 등재 권장.

---

## 9. 인스턴스·게이트웨이 설정

### 9.1 메모리 핏 (instances/voxtral.yaml)

```yaml
gpus: [2]
gpu_memory_utilization: 0.35   # L40S 46GB × 0.35 = 16.1 GiB 예약
max_num_seqs: 1                # 단일 세션 PoC. 동시 N 세션 시 N으로 상향
max_model_len: 32768           # ≈ 43분 오디오 (1 token = 80ms)
```

L40S 46GB × util별 동작 (실측):

| util | 예약 | weight | KV 가용 | KV token | 권장 max_num_seqs |
|------|------|--------|---------|---------|---------|
| 0.35 (현재) | 16.1 GiB | 8.38 | 4.24 GiB | 2,160 | 1 (단일 세션 PoC) |
| 0.40 | 18.4 GiB | 8.38 | ~6.5 GiB | ~3,300 | 2~3 |
| 0.50 | 23.0 GiB | 8.38 | 10.9 GiB | 5,568 | 4 (max_concurrency 2.71x) |

> vLLM은 continuous batching이라 `max_num_seqs`는 동시 in-flight 상한일 뿐, 실제 동시 N 세션 처리 가능 여부는 KV cache 슬롯에 의해 제한됩니다. 부족하면 일부 시퀀스가 swap out(preemption) → 사실상 일부 순차화. 그래서 `gpu_memory_utilization`과 `max_num_seqs`는 **목표 동시 stream 수에 맞춰 함께** 조정합니다.

### 9.2 vLLM 컴파일 모드 (모델카드 권장)

```yaml
env:
  VLLM_DISABLE_COMPILE_CACHE: "1"   # launcher가 subprocess.env에 머지
compilation_config:
  cudagraph_mode: PIECEWISE         # 모델카드 권장
```

> `_LAUNCHER_KEYS`에 `env`가 포함되므로 vllm serve로 전달되지 않고 launcher가 환경변수로만 export.

### 9.3 게이트웨이 과부하 차단 (gateways/5017.yaml)

```yaml
overload:
  enabled: true
  max_inflight_requests: 1   # 인스턴스 max_num_seqs 와 일치
  max_queue_size: 4
  queue_timeout_seconds: 120
  retry_after_seconds: 10
```

429 + `Retry-After` 헤더로 클라이언트 재시도 유도. 동시 N 세션으로 확장 시 인스턴스/게이트웨이 둘 다 같이 N으로 상향.

### 9.4 STT 전용 게이트웨이 차이점 (vs LLM 게이트웨이)

| 키 | LLM(:5015) | STT(:5017) | 사유 |
|----|------------|------------|------|
| `warmup.enabled` | true | **false** | STT는 chat dummy 추론으로 웜업 불가 → /health 프로브만 |
| `prefix_cache_warmup.enabled` | true | **false** | STT는 시스템 프롬프트 캐싱 패턴과 무관 |
| `http_client.timeout_seconds` | 300 | **600** | audio 길이에 비례 (chat보다 길게) |
| `max_inflight_requests` | 2 | **1** | 단일 세션 PoC |

---

## 10. 트러블슈팅

| 증상 | 원인 / 해결 |
|------|-------------|
| `EngineCore failed to start` + `ImportError: soundfile` | Voxtral 의존성 미설치. `pip install --user soundfile soxr librosa` |
| 게이트웨이 `/health`가 503 + `ready: 0/1` | 백엔드 voxtral가 미기동 또는 로딩 중. `./start.sh status` → `[STARTING]`이면 1~2분 대기 |
| 게이트웨이 로그 `realtime 백엔드 연결 실패` | `gateways/5017.yaml`의 `discover_from`이 voxtral.yaml의 `gateway_port`와 일치하는지 확인. 디스커버리 결과는 게이트웨이 기동 로그(`logs/gateway_5017.log`)에 표시 |
| `[STALE]` 표시 | launcher가 SIGKILL/crash로 죽음. `./start.sh down voxtral`로 runtime 파일 정리 |
| GPU OOM | `gpu_memory_utilization` 낮춤(0.30~0.35). 또는 LLM 인스턴스가 같은 GPU 점유 중인지 `nvidia-smi`로 확인 |
| Transcription HTTP 504 | 600s 초과. 오디오를 더 짧게 분할하거나 `gateways/5017.yaml`의 `http_client.timeout_seconds`를 상향 |
| WebSocket close 4429 | 과부하 차단. `max_inflight_requests`/`max_queue_size` 또는 클라이언트 동시성 조정 |
| WebSocket close 4500 | 게이트웨이 → 백엔드 WS 연결 실패. 백엔드 vllm 로그(`logs/vllm_voxtral.log`) 확인 |
| 코드 수정 반영 안 됨 | `__pycache__` 캐시. `find /workspace -name __pycache__ -exec rm -rf {} +` 후 재기동 |

---

## 11. QA 체크리스트

게이트웨이 + 인스턴스 정상 기동 후:

```bash
# 1) 게이트웨이 health
curl -s -w "\n[%{http_code}]\n" http://localhost:5017/health
# {"status":"ok","ready":1,"total":1}  [200]

# 2) 모델 등록 확인
curl -s http://localhost:5017/v1/models | python3 -m json.tool

# 3) Smoke transcription (1초 사인파)
python3 -c "
import numpy as np, soundfile as sf
sr=16000; t=np.linspace(0,1,sr,endpoint=False)
sf.write('/tmp/sine_1s.wav', (0.1*np.sin(2*np.pi*440*t)).astype('float32'), sr)
"
curl -s -w "\n[%{http_code} (%{time_total}s)]\n" \
  http://localhost:5017/v1/audio/transcriptions \
  -F "file=@/tmp/sine_1s.wav" \
  -F "model=Voxtral-Mini-4B-Realtime-2602" \
  -F "language=ko" \
  -F "temperature=0"
# {"text":"","usage":{"type":"duration","seconds":1}}  [200]

# 4) Realtime WebSocket 핸드셰이크
python3 - <<'PY'
import asyncio, json, websockets
async def main():
    async with websockets.connect("ws://localhost:5017/v1/realtime?model=Voxtral-Mini-4B-Realtime-2602") as ws:
        msg = await ws.recv()
        print("session.created OK:", json.loads(msg)["type"])
asyncio.run(main())
PY
# session.created OK: session.created
```

위 4개가 모두 통과하면 5017 게이트웨이 + voxtral 백엔드는 운영 가능 상태입니다.

---

## 12. 참고 자료

- 사용자 호출 가이드: [`STT_API_GUIDE.md`](STT_API_GUIDE.md) §1~§5
- 배포 절차(로컬 → S3 → 운영계): [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md)
- 후보 모델 비교 / 시나리오: [`stt/MODEL_STUDY.md`](stt/MODEL_STUDY.md)
- LLM 운영 가이드(같은 페어 패턴): [`VLLM_OPS_GUIDE.md`](VLLM_OPS_GUIDE.md)
- Voxtral 모델카드: <https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602>
- OpenAI Audio API: <https://platform.openai.com/docs/api-reference/audio>
- OpenAI Realtime API: <https://platform.openai.com/docs/api-reference/realtime>
