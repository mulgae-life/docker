# vLLM 서버 운영 가이드

> **현재 운영 모델**: `Qwen/Qwen3.6-35B-A3B-FP8` — 35B 파라미터 중 3B만 활성화하는 Hybrid MoE 모델.
> **엔드포인트**: 게이트웨이 `http://3.38.195.121:5015/v1` / 직접 연결 시 `:7070`.
> **인프라**: AWS L40S 46GB × 2장, `tensor_parallel_size: 2`.
> **vLLM 버전**: 0.19.0.

이 문서는 **로컬/온프레미스에서 vLLM으로 SLM(Small Language Model)을 띄우고** chatbot-poc에 연결하는 방법을 설명합니다.
처음 보는 분은 [1. 빠른 시작](#1-빠른-시작-5분) → [2. 시스템 구조](#2-시스템-구조) 순서로 읽으시면 됩니다.

---

## 📑 목차

1. [빠른 시작 (5분)](#1-빠른-시작-5분)
2. [시스템 구조](#2-시스템-구조)
3. [서버 기동·중지](#3-서버-기동중지)
4. [모델 준비 (다운로드)](#4-모델-준비-다운로드)
5. [설정 파일](#5-설정-파일)
6. [API 사용법 (개발자용)](#6-api-사용법-개발자용)
7. [모델 관리](#7-모델-관리)
8. [Qwen3.6 고급 기능](#8-qwen36-고급-기능)
9. [트러블슈팅 & 운영 주의](#9-트러블슈팅--운영-주의)
10. [QA 테스트](#10-qa-테스트)
11. [참고 자료](#11-참고-자료)

---

## 1. 빠른 시작 (5분)

> 🔰 **전제 조건**
> - 레포를 클론하면 `vllm_config.yaml`·`vllm_gateway_config.yaml`·`start.sh`가 모두 포함되어 있습니다. 별도 초기 세팅 없이 바로 기동 가능합니다.
> - 필요한 것: GPU 가용(기본 프로필은 L40S 46GB × 2), Python 3.10+ 환경, vLLM 0.19.0 설치.
> - 모델(`Qwen/Qwen3.6-35B-A3B-FP8`, ~35GB)은 첫 기동 시 자동으로 다운로드됩니다. 미리 받아두려면 [4장 모델 준비](#4-모델-준비-다운로드) 참고.

### 1.1 이미 서버가 떠 있는 경우 — 호출만 해보기

```bash
# 서버 상태 확인
curl http://3.38.195.121:5015/health

# 추론 테스트 (가장 간단한 요청)
curl http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-35B-A3B-FP8",
    "messages": [{"role":"user","content":"안녕"}],
    "max_tokens": 50
  }'
```

### 1.2 서버 기동

```bash
cd /workspace/chatbot-poc/scripts/vllm

./start.sh          # vLLM + 게이트웨이 한 번에 기동
./start.sh status   # 상태 확인
./start.sh stop     # 전체 중지
./start.sh restart  # 재시작
```

`start.sh`는 `vllm_config.yaml`·`vllm_gateway_config.yaml`을 읽어 GPU 배치·포트·모델 경로를 자동으로 계산합니다.

### 1.3 chatbot-poc에서 사용하기

`.env`에 아래 값을 넣으면 LangChain `ChatOpenAI`로 곧바로 호출됩니다.

```env
PROVIDER=huggingface
HF_BASE_URL=http://3.38.195.121:5015/v1   # 게이트웨이 포트 (:5015)
CHAT_MODEL=Qwen3.6-35B-A3B-FP8
RERANKER_MODEL=Qwen3.6-35B-A3B-FP8
```

> ⚠️ **`PROVIDER`는 단일 스택** — Chat과 Embedding이 함께 전환됩니다. 임베딩은 OpenAI를 유지하면서 Chat만 vLLM으로 쓰려면 provider를 분리해야 합니다.

---

## 2. 시스템 구조

### 2.1 전체 구성도

```
chatbot-poc (.env)
  PROVIDER=huggingface
  HF_BASE_URL=http://3.38.195.121:5015/v1
            │
            ▼
┌──────────────────────────────────────────────┐
│ Gateway (:5015)                              │
│   • 로드밸런싱  • 헬스체크  • CUDA 웜업      │
└──────────────────────────────────────────────┘
            │
   ┌────────┴────────┐
   ▼                 ▼
vLLM #1 (:7070)    vLLM #2 (:7071)  ← GPU 4장 확장 시 자동 추가
GPU 0·1 TP=2       GPU 2·3 TP=2     base_port + 1
```

- **현재 운영 구성**: GPU 2장만 사용하므로 vLLM 인스턴스 1개(`:7070`)만 동작.
- **게이트웨이 포트(:5015)** 만 외부에 개방합니다. vLLM 포트(:7070+)는 내부 전용.

### 2.2 구성요소 역할

| 구성요소 | 역할 | 파일 |
|---------|------|------|
| **vLLM 서버** | 실제 모델 추론 수행 (GPU 점유) | `vllm_server_launcher.py` |
| **Gateway** | 클라이언트 요청을 vLLM들에 분배 + 헬스체크 + 웜업 | `vllm_gateway.py` |
| **start.sh** | 둘 다 한 번에 기동·중지하는 오케스트레이터 | `start.sh` |
| **vllm_config.yaml** | vLLM 모델·GPU·포트 설정 (**단일 소스**) | `vllm_config.yaml` |
| **vllm_gateway_config.yaml** | 게이트웨이 헬스체크·웜업 설정 | `vllm_gateway_config.yaml` |

### 2.3 왜 게이트웨이가 있나요?

- **단일 엔드포인트**: 클라이언트는 `:5015` 하나만 알면 됩니다. 내부에서 vLLM 인스턴스가 몇 개든 상관없습니다.
- **헬스체크**: 죽은 인스턴스는 라우팅 풀에서 자동 제외.
- **CUDA 웜업**: 첫 요청 지연(cold start)을 제거.
- **프리픽스 캐시 웜업**: 시스템 프롬프트를 미리 KV 캐시에 적재 → TTFT(첫 토큰 대기시간) 감소.

> 게이트웨이 없이도 vLLM에 직접 붙을 수 있습니다. 그때는 `HF_BASE_URL=http://3.38.195.121:7070/v1` 처럼 vLLM 포트를 가리키면 됩니다. (부하분산·웜업 혜택은 사라집니다.)

---

## 3. 서버 기동·중지

### 3.1 권장 방법 — start.sh

가장 쉽고 안전한 방법입니다. `vllm_config.yaml`의 `gpus` / `tensor_parallel_size` 조합만 보면 인스턴스 수·포트가 자동으로 결정됩니다.

```bash
cd /workspace/chatbot-poc/scripts/vllm

./start.sh         # 기동
./start.sh stop    # 중지
./start.sh status  # 상태
./start.sh restart # 재시작
```

### 3.2 GPU 배치 규칙

`gpus`와 `tensor_parallel_size`(TP) 조합으로 인스턴스가 결정됩니다.

| `gpus` | `tensor_parallel_size` | 인스턴스 수 | 배치 결과 |
|--------|------------------------|-----------|----------|
| `[0]` | 1 | 1 | GPU 0 → :7070 |
| `[0, 1]` | 1 | **2 (DP)** | GPU 0 → :7070, GPU 1 → :7071 |
| **`[0, 1]`** | **2** | **1 (TP) ← 현재 운영** | GPU 0,1 → :7070 |
| `[0, 1, 2, 3]` | 2 | 2 | GPU 0,1 → :7070, GPU 2,3 → :7071 |
| `[0, 1, 2, 3]` | 4 | 1 | GPU 0,1,2,3 → :7070 |

> **TP vs DP**
> - **TP (Tensor Parallel)**: 한 모델을 여러 GPU에 쪼개 얹음. 큰 모델을 돌릴 수 있지만 GPU 간 통신 비용 있음.
> - **DP (Data Parallel)**: GPU마다 똑같은 모델을 올림. 동시 처리량이 늘지만 GPU당 메모리가 충분해야 함.
> - Qwen3.6-35B-A3B-FP8은 FP8 가중치 ~35GB이므로 L40S(46GB) 1장으로는 KV cache 확보가 빠듯해 **TP=2**로 운영합니다.

### 3.3 기동 순서 (start.sh 내부)

1. `vllm_config.yaml` 파싱 → GPU·포트 계산
2. vLLM 인스턴스들 백그라운드 기동 (`vllm_server_launcher.py`)
3. 게이트웨이 기동 (`vllm_gateway.py`)
4. 게이트웨이가 각 백엔드 `/health` 폴링 대기
5. CUDA 웜업 → 프리픽스 캐시 웜업
6. 최소 1대 준비 완료 시 `:5015`에서 요청 수신 시작

### 3.4 인스턴스 동적 추가/제거

게이트웨이를 **재시작하지 않아도** 인스턴스를 자유롭게 관리할 수 있습니다.

- **추가**: 런처로 새 인스턴스를 띄우면 게이트웨이 헬스체크가 자동 감지 → 웜업 후 라우팅 풀에 투입.
- **제거**: 죽은 인스턴스는 헬스체크 실패 → 자동 제외.
- **복구**: 복구 감지 → 웜업 재실행 → 풀 복귀.

`backend_count`(기본 8)를 넉넉히 두면 해당 포트 범위를 게이트웨이가 감시합니다.

### 3.5 개별 기동 (start.sh 없이)

```bash
cd /workspace/chatbot-poc/scripts/vllm

# 1. vLLM 인스턴스 기동 (포트는 vllm_config.yaml의 port 자동)
python vllm_server_launcher.py -g 0,1 --port 7070

# 추가 인스턴스가 필요하면
python vllm_server_launcher.py -g 2,3 --port 7071

# 2. 게이트웨이 기동
python vllm_gateway.py

# 백그라운드 실행
mkdir -p logs && nohup python vllm_gateway.py > logs/gateway.log 2>&1 &
```

### 3.6 더 로우레벨한 방법 — vllm serve 네이티브

vLLM v0.18.0+는 YAML config를 네이티브로 지원합니다. config의 `model`이 HF ID라서, 네이티브 실행 시 모델 로컬 경로를 positional 인자로 직접 넘겨야 합니다.

```bash
cd /workspace/chatbot-poc/scripts/vllm

# 기본
CUDA_VISIBLE_DEVICES=0,1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  vllm serve /models/LLM/Qwen/Qwen3.6-35B-A3B-FP8 --config vllm_config.yaml

# 백그라운드
mkdir -p logs && CUDA_VISIBLE_DEVICES=0,1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  nohup vllm serve /models/LLM/Qwen/Qwen3.6-35B-A3B-FP8 \
  --config vllm_config.yaml > logs/server.log 2>&1 &

# 일부 옵션만 CLI로 덮어쓰기
CUDA_VISIBLE_DEVICES=0,1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  vllm serve /models/LLM/Qwen/Qwen3.6-35B-A3B-FP8 \
  --config vllm_config.yaml --port 8000 --max-num-seqs 5
```

### 3.7 3가지 기동 방법 비교

| | `vllm serve` 네이티브 | Python 런처 | **`start.sh` (권장)** |
|---|---|---|---|
| 모델 경로 | 전체 경로 직접 지정 | HF ID → 로컬 경로 자동 | config에서 자동 |
| 모델 자동 다운로드 | ❌ 사전 다운로드 필요 | ✅ 자동 또는 `--download-only` | ✅ 자동 |
| CUDA 설정 | `CUDA_VISIBLE_DEVICES=0,1` 직접 | `-g 0,1` 또는 환경변수 | config `gpus` 키 자동 |
| HF 오프라인 플래그 | 환경변수 직접 | 자동 적용 | 자동 적용 |
| 다중 인스턴스 | 수동 | 수동 | config 기반 자동 |
| 게이트웨이 | 별도 기동 필요 | 별도 기동 필요 | 함께 기동 |
| 중지 방법 | `kill` | `kill` | `./start.sh stop` |

### 3.8 보안 주의

게이트웨이 자체에는 **인증 기능이 없습니다**. 공개망에 직접 노출하지 말고 AWS Security Group, 방화벽, nginx 등으로 접근을 제한하세요.

vLLM에 `--api-key`를 설정한 경우:

- **클라이언트 → 게이트웨이**: `Authorization` 헤더를 백엔드에 패스스루.
- **게이트웨이 내부 요청**: 웜업·모델 감지 등 내부 요청은 `backend_api_key` 설정값을 사용.

```yaml
# vllm_gateway_config.yaml
backend_api_key: "your-secret-key"   # vLLM --api-key 값과 일치
```

---

## 4. 모델 준비 (다운로드)

### 4.1 자동 다운로드 (기본)

처음 기동할 때 로컬에 모델이 없으면 `vllm_server_launcher.py`가 `huggingface_hub.snapshot_download` API로 자동 다운로드합니다. 별도 명령이 필요 없습니다.

> Qwen3.6 / Gemma 4 모두 Apache 2.0(또는 Gemma 라이선스)이라 **HF 토큰이 필요 없습니다**. Llama처럼 gated 모델은 `HF_TOKEN=hf_xxx`를 환경변수로 넘기세요.

### 4.2 오프라인 이관용 사전 다운로드 (폐쇄망 운영)

```bash
cd /workspace/chatbot-poc/scripts/vllm

# 1) config의 model / download_dir 그대로 사용
python vllm_server_launcher.py --download-only

# 2) Gated 모델
HF_TOKEN=hf_xxx python vllm_server_launcher.py --download-only

# 3) 모델 override
python vllm_server_launcher.py --download-only -m Qwen/Qwen3.6-35B-A3B-FP8

# 4) 다른 config 사용
python vllm_server_launcher.py --download-only -c ./vllm_config.yaml
```

> `--download-only`는 내부적으로 `download_model()`을 호출하므로 **실제 서빙과 동일한 경로 규칙**을 씁니다. 안전한 표준 방식입니다.

### 4.3 운영 규칙

- 네트워크가 되는 환경에서 `--download-only`로 먼저 받는다.
- 폐쇄망으로 모델 디렉토리를 이관한다.
- 실제 서빙은 항상 `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` (런처·start.sh는 자동 적용).
- 다운로드 경로는 반드시 `{download_dir}/{HF repo_id}` 레이아웃을 지킬 것. 예:
  - config: `model: Qwen/Qwen3.6-35B-A3B-FP8`, `download_dir: /models/LLM`
  - 실제 경로: `/models/LLM/Qwen/Qwen3.6-35B-A3B-FP8`

### 4.4 다운로드 확인

```bash
MODEL_DIR="/models/LLM/Qwen/Qwen3.6-35B-A3B-FP8"

test -f "$MODEL_DIR/config.json"              # 모델 config
test -f "$MODEL_DIR/tokenizer_config.json"    # 토크나이저
find "$MODEL_DIR" -maxdepth 1 \( -name '*.safetensors' -o -name '*.bin' \)  # 가중치 파일
```

### 4.5 Python 런처 전용 옵션

| 옵션 | 설명 |
|------|------|
| `-g, --gpu` | `CUDA_VISIBLE_DEVICES` (예: `-g 0`, `-g 0,1`) |
| `-m, --model` | HF 모델 ID 또는 경로 (config override) |
| `-c, --config` | 설정 파일 경로 (기본: `vllm_config.yaml`) |
| `--online` | 서빙 시 HF Hub 접근 허용 (기본: 오프라인) |
| `--download-only` | 다운로드만 수행, 서버는 실행 안 함 |

그 외 인자는 모두 `vllm serve`에 그대로 전달됩니다.

---

## 5. 설정 파일

### 5.1 설정 우선순위

```
CLI 인자  >  vllm_config.yaml  >  vLLM 기본값
```

### 5.2 vllm_config.yaml 주요 설정

| 키 | 현재 값 | 설명 |
|----|---------|------|
| `model` | `Qwen/Qwen3.6-35B-A3B-FP8` | HF 모델 ID (런처가 download_dir과 조합) |
| `download_dir` | `/models/LLM` | 모델 로컬 저장 루트 |
| `served_model_name` | `Qwen3.6-35B-A3B-FP8` | API에서 부를 이름 (.env `CHAT_MODEL`과 일치) |
| `dtype` | `auto` | 활성화 dtype (auto 시 모델 config 따름) |
| `quantization` | *(미설정)* | FP8 사전 양자화 체크포인트라 자동 감지. BF16 모델만 `fp8`로 온라인 양자화 |
| **서버 설정** | | |
| `host` | `0.0.0.0` | 바인드 주소 (로컬만: `localhost`) |
| `port` | `7070` | vLLM 포트 (게이트웨이 사용 시 내부 포트) |
| `gpus` | `[0, 1]` | 사용할 GPU 번호 목록 |
| `tensor_parallel_size` | `2` | GPU 병렬 수 |
| `gpu_memory_utilization` | `0.9` | GPU 메모리 사용률. Mamba-hybrid KV 추정 오차 대비 0.95→0.9로 보수 운영 |
| **추론** | | |
| `max_model_len` | `262144` | 컨텍스트 길이 (Qwen3.6 네이티브 262K) |
| `max_num_seqs` | `5` | 동시 처리 시퀀스 상한. 멀티모달에서는 encoder cache 용량과 맞춤 |
| `max_num_batched_tokens` | `163840` | 배치당 최대 토큰. ⚠️ `encoder_cache_size`로도 복제됨 (scheduler.py:235). 이미지 1장 ≈ 16,384 tokens → 약 10장 분량 |
| `seed` | `42` | 재현 가능한 추론용 |
| **Thinking** | | |
| `default_chat_template_kwargs` | `{enable_thinking: false}` | 서버 기본 Thinking. Qwen3.6는 기본 ON이지만 챗봇용으로 OFF 덮어씀 |
| `reasoning_parser` | `qwen3` | Qwen 3/3.5/3.6 공통 (`<think>...</think>`) |
| **멀티모달 안정성** | | |
| `async_scheduling` | `false` | scheduler ↔ worker race 방지. 멀티모달 안정성 우선 (TPS 5~15% 감수) |
| `mm_encoder_tp_mode` | `data` | 비전 인코더를 TP split 대신 DP 처리 (Qwen3.5/3.6 공식 레시피) |
| `mm_processor_cache_type` | `shm` | 전처리된 MM 입력을 프로세스 간 shm FIFO로 공유 — IPC 중복 제거 |
| `language_model_only` | `false` | 이미지 입력 허용. 텍스트 전용 운영 시 `true` |
| ~~`disable_chunked_mm_input`~~ | *(절대 설정 금지)* | Qwen3.6 Mamba-hybrid 비호환. 자세한 이유는 [9.3](#93-disable_chunked_mm_input이-qwen36에-금지인-이유) |
| **캐시** | | |
| `enable_prefix_caching` | `true` | 시스템 프롬프트 KV 재사용. Mamba-hybrid에서 `mamba_cache_mode='align'` 자동 활성 |
| `kv_cache_dtype` | `auto` | FP8 모델이면 BF16 KV. `fp8_e4m3`로 바꾸면 KV 용량 2배 |
| **Tool Calling** | | |
| `enable_auto_tool_choice` | `true` | Tool call 자동 파싱 |
| `tool_call_parser` | `qwen3_xml` | Qwen3.6는 `qwen3_xml`/`qwen3_coder` 선택 가능. 현재는 범용 호환 |

자세한 설명은 `vllm_config.yaml` 내 한국어 주석에 있습니다.

### 5.3 vllm_gateway_config.yaml 주요 설정

| 키 | 현재 값 | 설명 |
|----|---------|------|
| `vllm_config` | `vllm_config.yaml` | vLLM 설정 파일 경로 (포트 자동 감지) |
| `gateway.port` | `5015` | 클라이언트 접근 포트 (방화벽 오픈 대상) |
| `backend_count` | `8` | 감시할 최대 인스턴스 수 |
| `backend_api_key` | *(미설정)* | vLLM `--api-key` 설정 시 내부 요청에 사용 |
| `health_check.interval_seconds` | `10` | 헬스체크 폴링 간격 |
| `health_check.unhealthy_threshold` | `3` | 연속 N회 실패 → unhealthy |
| `warmup.enabled` | `true` | CUDA 웜업 |
| `warmup.boot_poll.timeout_seconds` | `300` | 서버 기동 최대 대기 (모델 로딩 포함) |
| `prefix_cache_warmup.enabled` | `true` | 프리픽스 캐시 웜업 |
| `prefix_cache_warmup.system_prompt` | *(보험 챗봇용)* | 웜업 시 KV에 적재할 시스템 프롬프트 |

### 5.4 .env 설정 (chatbot-poc 측)

```env
PROVIDER=huggingface
HF_BASE_URL=http://3.38.195.121:5015/v1    # 게이트웨이 포트
CHAT_MODEL=Qwen3.6-35B-A3B-FP8              # served_model_name과 일치 필수
RERANKER_MODEL=Qwen3.6-35B-A3B-FP8          # 리랭킹도 같은 모델 쓸 때
```

> **`CHAT_MODEL`은 반드시 `served_model_name`과 일치**해야 vLLM이 요청을 받습니다. 모델 교체 시 함께 바꾸세요.

### 5.5 📝 참고: YAML `bool false` 전달 제약 (런처 내부)

> 이건 런처 사용자가 직접 신경 쓸 일은 없지만, 런처 동작 원리가 궁금한 분을 위해 남겨둡니다.

vLLM의 YAML 파서(`vllm/utils/argparse_utils.py:501-504`)는 `key: true`만 `--key` 플래그로 변환하고 `key: false`는 아무것도 하지 않습니다. 기본값이 `None`인 필드(예: `async_scheduling`)는 YAML에 `false`로 적어도 CLI로 전달되지 않아 auto-enable 로직에 의해 `True`로 뒤집힙니다.

런처는 `async_scheduling: false`를 감지하면 `--no-async-scheduling` CLI 플래그를 직접 주입하여 이 제약을 우회합니다. 덕분에 YAML에 `false`로 써도 실제로 `false`가 적용됩니다.

---

## 6. API 사용법 (개발자용)

### 6.1 Base URL & 엔드포인트 요약

Base URL: `http://3.38.195.121:5015/v1`

| 메서드 | 경로 | 용도 |
|--------|------|------|
| GET | `/health` | 서버 살아있는지 확인 |
| GET | `/v1/models` | 로드된 모델 목록 |
| POST | `/v1/chat/completions` | **채팅 추론 (메인 API)** |
| GET | `/server-status` | (게이트웨이 전용) 백엔드 상태 대시보드 |

> vLLM은 OpenAI Chat Completions API와 **100% 호환**됩니다. 기존 OpenAI SDK, LangChain `ChatOpenAI`, `curl`을 그대로 쓸 수 있습니다.
> 인증(API Key)은 기본적으로 **필요 없습니다**. vLLM에 `--api-key`를 설정한 경우에만 `Authorization: Bearer <key>` 헤더 필요.

### 6.2 헬스체크 & 모델 목록

```bash
curl http://3.38.195.121:5015/health
# → 200 OK

curl http://3.38.195.121:5015/v1/models
```

응답:

```json
{
  "object": "list",
  "data": [
    {
      "id": "Qwen3.6-35B-A3B-FP8",
      "object": "model",
      "owned_by": "vllm",
      "root": "Qwen/Qwen3.6-35B-A3B-FP8",
      "max_model_len": 262144
    }
  ]
}
```

### 6.3 가장 간단한 요청 (비스트리밍)

```bash
curl http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-35B-A3B-FP8",
    "messages": [
      {"role": "system", "content": "간결하게 답변해."},
      {"role": "user", "content": "대한민국의 수도는?"}
    ],
    "max_tokens": 50,
    "temperature": 0
  }'
```

응답:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "model": "Qwen3.6-35B-A3B-FP8",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "서울입니다."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 25, "completion_tokens": 5, "total_tokens": 30}
}
```

### 6.4 주요 요청 파라미터

| 파라미터 | 필수 | 기본값 | 설명 |
|---------|:----:|--------|------|
| `model` | O | — | `served_model_name`과 일치 |
| `messages` | O | — | 대화 메시지 배열 |
| `max_tokens` | — | 모델 한계 | 최대 생성 토큰 수 |
| `temperature` | — | 모델별 | 0이면 결정적. Qwen3.6 Thinking: 1.0, 코딩: 0.6 |
| `top_p` | — | 0.95 | Nucleus sampling |
| `top_k` | — | 모델별 | Qwen3.6: 20, Gemma 4: 64 |
| `presence_penalty` | — | 0 | Qwen3.6 Thinking 권장 1.5 (반복 붕괴 방지) |
| `stream` | — | false | SSE 스트리밍 |
| `stream_options` | — | — | `{"include_usage": true}`면 스트리밍에서도 usage 반환 |
| `tools` | — | — | Tool Calling |
| `chat_template_kwargs` | — | — | 템플릿 인자 (Thinking, preserve_thinking 등) |
| `seed` | — | — | 재현 가능한 출력 |
| `stop` | — | — | 생성 중단 토큰 |

> `temperature`/`top_k`/`top_p` 기본값은 모델의 `generation_config.json`에서 자동 적용됩니다. 이유 없이 오버라이드하지 않아도 됩니다.

### 6.5 messages 배열 구조

```json
[
  {"role": "system",    "content": "시스템 프롬프트"},
  {"role": "user",      "content": "사용자 메시지"},
  {"role": "assistant", "content": "이전 응답"},
  {"role": "user",      "content": "후속 질문"}
]
```

| role | 설명 |
|------|------|
| `system` | 모델의 역할·톤 지시 (선택, 1개 권장) |
| `user` | 사용자 입력 |
| `assistant` | 모델의 이전 응답 (멀티턴) |
| `tool` | Tool 실행 결과 (Tool Calling 시) |

### 6.6 `finish_reason` 해석

| 값 | 의미 |
|----|------|
| `stop` | 자연 종료 (EOS 토큰 생성) |
| `length` | `max_tokens` 도달로 잘림 |
| `tool_calls` | Tool 호출 요청 |

### 6.7 스트리밍 (SSE)

```bash
curl http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-35B-A3B-FP8",
    "messages": [{"role": "user", "content": "안녕"}],
    "max_tokens": 50,
    "stream": true,
    "stream_options": {"include_usage": true}
  }'
```

응답 (Server-Sent Events):

```
data: {"id":"chatcmpl-abc","choices":[{"delta":{"role":"assistant","content":""},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"안녕"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"하세요"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[],"usage":{"prompt_tokens":14,"completion_tokens":8,"total_tokens":22}}

data: [DONE]
```

- 각 청크는 `data: ` 접두사 + JSON.
- `choices[].delta.content`에 새로 생성된 토큰 텍스트.
- `data: [DONE]`이 스트림 종료 신호.
- `stream_options.include_usage: true`를 넣으면 마지막 청크에 usage가 따라옵니다.

### 6.8 Tool Calling (함수 호출)

vLLM은 모델의 tool call 출력을 OpenAI 호환 JSON으로 자동 파싱합니다. `vllm_config.yaml`에 `enable_auto_tool_choice: true` + `tool_call_parser`가 설정돼 있어야 합니다(현재 기본 `qwen3_xml`).

**1단계 — Tool 정의 + 사용자 질문**:

```bash
curl http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-35B-A3B-FP8",
    "messages": [{"role": "user", "content": "서울 날씨 알려줘"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "지정한 도시의 현재 날씨를 조회합니다.",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string", "description": "도시 이름"}
          },
          "required": ["city"]
        }
      }
    }],
    "max_tokens": 200
  }'
```

**응답 — 모델이 Tool 호출을 결정**:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "chatcmpl-tool-abc",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"city\": \"서울\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

**2단계 — Tool 실행 결과를 다시 모델에 전달**:

```bash
curl http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-35B-A3B-FP8",
    "messages": [
      {"role": "user", "content": "서울 날씨 알려줘"},
      {
        "role": "assistant",
        "content": null,
        "tool_calls": [{
          "id": "call_1",
          "type": "function",
          "function": {"name": "get_weather", "arguments": "{\"city\": \"서울\"}"}
        }]
      },
      {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "{\"temperature\": 22, \"condition\": \"맑음\", \"humidity\": 45}"
      }
    ],
    "max_tokens": 200
  }'
```

**최종 응답**:

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "현재 서울의 날씨는 기온 22°C, 맑음이며 습도는 45%입니다."
    },
    "finish_reason": "stop"
  }]
}
```

> Tool이 필요 없다고 모델이 판단하면 `tool_calls` 없이 `content`로 바로 답변합니다.

### 6.9 Thinking 모드 (사고 과정 분리)

모델이 답변 전에 "생각"하는 과정을 `reasoning_content` 필드로 **분리**해서 받을 수 있습니다.

**서버 기본값**: `enable_thinking: false` (챗봇 응답 지연 최소화).
**요청 단위 ON/OFF**: `chat_template_kwargs.enable_thinking` 전달.

```bash
# Thinking ON
curl http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-35B-A3B-FP8",
    "messages": [{"role": "user", "content": "15의 소인수를 구해줘"}],
    "max_tokens": 500,
    "chat_template_kwargs": {"enable_thinking": true}
  }'
```

응답 (`reasoning_parser: qwen3` 활성 덕에 자동 분리):

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "15의 소인수는 3과 5입니다.",
      "reasoning_content": "15를 소인수분해하면... 15 = 3 × 5이므로..."
    },
    "finish_reason": "stop"
  }]
}
```

**규칙 요약**:

- Thinking OFF면 `reasoning_content`는 `null`.
- 멀티턴 히스토리에 사고 과정은 **넣지 마세요** (최종 답변만 포함). Qwen3.6는 `preserve_thinking: true` 옵션으로 vLLM이 자동 유지해줍니다 ([8.2](#82-preserve_thinking-에이전트-반복-루프-최적화)).
- Qwen 3/3.5/3.6은 `<think>...</think>`가 일반 토큰이라 `skip_special_tokens: false`가 **불필요**합니다.
- Gemma 4로 교체 운영 시에는 `<|channel>...<channel|>` 경계 토큰이 스페셜 토큰이라 `"skip_special_tokens": false`를 요청에 추가해야 reasoning이 분리됩니다.

### 6.10 Qwen3.6 Thinking 복붙용 curl 예시

**Qwen3.6 공식 권장 샘플링 파라미터** (모델 카드 기준):

| 모드 | temperature | top_p | top_k | min_p | presence_penalty |
|------|:-----------:|:-----:|:-----:|:-----:|:----------------:|
| Thinking · 일반 | 1.0 | 0.95 | 20 | 0.0 | **1.5** |
| Thinking · 정밀 코딩 | 0.6 | 0.95 | 20 | 0.0 | 0.0 |
| Instruct · 일반 | 0.7 | 0.8 | 20 | 0.0 | **1.5** |
| Instruct · reasoning | 1.0 | 1.0 | 40 | 0.0 | **2.0** |

> `top_p`/`top_k`는 `generation_config.json`이 자동 적용되므로 명시 생략 가능. `presence_penalty`는 vLLM 기본 0이라 장문 Thinking에서 반복 붕괴 방지용으로 명시 권장. 한국어 응답에서 언어 혼합이 보이면 1.0~1.2로 낮추세요.

**권장 방식 — JSON 파일 + `-d @`**:

```bash
cat > /tmp/qwen_req.json <<'EOF'
{
  "model": "Qwen3.6-35B-A3B-FP8",
  "messages": [
    {"role": "system", "content": "자세하게 답변해줘."},
    {"role": "user", "content": "미국인과 한국인의 차이점 비교 설명해줘"}
  ],
  "max_tokens": 10000,
  "temperature": 1.0,
  "presence_penalty": 1.0,
  "chat_template_kwargs": {"enable_thinking": true}
}
EOF

curl http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d @/tmp/qwen_req.json
```

**한 줄 명령**:

```bash
curl -sS http://3.38.195.121:5015/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"Qwen3.6-35B-A3B-FP8","messages":[{"role":"system","content":"자세하게 답변해줘."},{"role":"user","content":"미국인과 한국인의 차이점 비교 설명해줘"}],"max_tokens":10000,"temperature":1.0,"presence_penalty":1.0,"chat_template_kwargs":{"enable_thinking":true}}'
```

**Thinking OFF — 빠른 응답**:

```bash
cat > /tmp/qwen_req_nothink.json <<'EOF'
{
  "model": "Qwen3.6-35B-A3B-FP8",
  "messages": [
    {"role": "user", "content": "미국인과 한국인의 차이점 간단히 설명"}
  ],
  "max_tokens": 2000,
  "temperature": 0.7,
  "top_p": 0.8,
  "presence_penalty": 1.5,
  "chat_template_kwargs": {"enable_thinking": false}
}
EOF

curl http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d @/tmp/qwen_req_nothink.json
```

**응답 파싱 (jq)**:

```bash
# 사고 과정 + 최종 답변 모두
curl -sS http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d @/tmp/qwen_req.json | jq '.choices[0].message | {reasoning_content, content}'

# 최종 답변만
curl -sS http://3.38.195.121:5015/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d @/tmp/qwen_req.json | jq -r '.choices[0].message.content'
```

**자주 하는 실수**:

- `\` 줄바꿈 뒤에 **공백이 붙으면** 이어쓰기가 깨져서 첫 줄만 GET으로 가 `{"detail":"Method Not Allowed"}`가 돌아옵니다. **파일 방식(`-d @`)을 권장**합니다.
- `chat_template_kwargs`는 **top-level 필드**입니다. `extra_body` 래핑 불필요.
- Thinking 토큰이 쉽게 2~4K를 먹으므로 복잡한 질의엔 `max_tokens`를 10,000 이상 잡으세요.

### 6.11 Python (OpenAI SDK) 예시

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://3.38.195.121:5015/v1",
    api_key="not-needed",   # vLLM 기본 인증 없음
)

# 기본 요청
response = client.chat.completions.create(
    model="Qwen3.6-35B-A3B-FP8",
    messages=[
        {"role": "system", "content": "간결하게 답변해."},
        {"role": "user", "content": "파이썬이란?"},
    ],
    max_tokens=200,
    temperature=0,
)
print(response.choices[0].message.content)

# 스트리밍
stream = client.chat.completions.create(
    model="Qwen3.6-35B-A3B-FP8",
    messages=[{"role": "user", "content": "안녕"}],
    max_tokens=100,
    stream=True,
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### 6.12 LangChain 예시

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://3.38.195.121:5015/v1",
    model="Qwen3.6-35B-A3B-FP8",
    api_key="not-needed",
    temperature=0,
    max_tokens=200,
)

# 기본 호출
response = llm.invoke("대한민국의 수도는?")
print(response.content)

# Tool Calling
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """지정한 도시의 현재 날씨를 조회합니다."""
    return f"{city}: 22°C, 맑음"

llm_with_tools = llm.bind_tools([get_weather])
response = llm_with_tools.invoke("서울 날씨 알려줘")
print(response.tool_calls)
```

### 6.13 에러 응답

| HTTP 코드 | 의미 |
|-----------|------|
| **400** | 잘못된 요청 (필수 필드 누락, 유효하지 않은 파라미터) |
| **404** | 존재하지 않는 모델명 또는 엔드포인트 |
| **422** | 요청 바디 파싱 실패 |
| **500** | 서버 내부 오류 |

응답 형식:

```json
{
  "object": "error",
  "message": "temperature must be non-negative, got -1.0.",
  "type": "BadRequestError",
  "code": 400
}
```

### 6.14 게이트웨이 전용 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/server-status` | 백엔드 서버 상태 대시보드 |

```bash
curl http://3.38.195.121:5015/server-status
```

```json
{
  "gateway": {"uptime_seconds": 3600.0},
  "backends": [{
    "url": "http://127.0.0.1:7070",
    "is_healthy": true,
    "is_ready": true,
    "active_connections": 2,
    "consecutive_failures": 0
  }],
  "ready_count": 1,
  "total_count": 1
}
```

---

## 7. 모델 관리

### 7.1 지원 모델 비교

| | **Qwen3.6-35B-A3B-FP8 (현재)** | Qwen3.5-27B-FP8 | Gemma 4 26B-A4B-it | Gemma 4 31B-it |
|---|---|---|---|---|
| **HF 모델 ID** | `Qwen/Qwen3.6-35B-A3B-FP8` | `Qwen/Qwen3.5-27B-FP8` | `google/gemma-4-26B-A4B-it` | `google/gemma-4-31B-it` |
| **파라미터** | 3B active / 35B total (Hybrid MoE) | 27B (Dense) | 26B active / ~45B total (MoE) | 30.7B (Dense) |
| **아키텍처** | Gated DeltaNet 75% + Gated Attention 25% (Mamba-hybrid) | Transformer | MoE | Transformer |
| **기본 dtype** | FP8 (사전 양자화) | FP8 (사전 양자화) | BF16 | BF16 |
| **양자화 필요?** | 불필요 | 불필요 | `quantization: fp8` (온라인) | `quantization: fp8` (온라인) |
| **가중치 크기** | ~35 GB | ~27 GB | ~25 GB | ~29 GB |
| **라이선스** | Apache 2.0 | Apache 2.0 | Gemma | Apache 2.0 |
| **HF 토큰** | 불필요 | 불필요 | 불필요 | 불필요 |
| **컨텍스트 (네이티브/YaRN)** | 262K / 1.01M | 131K | 128K | 256K |
| **멀티모달** | 텍스트 + 이미지 + 비디오 | 텍스트 전용 | 텍스트 + 이미지 | 텍스트 + 이미지 |
| **Thinking 기본값** | ON (서버 OFF 덮음) | ON | OFF | OFF |
| **Thinking 형식** | `<think>...</think>` | `<think>...</think>` | `<\|channel>thought...<channel\|>` | `<\|channel>thought...<channel\|>` |
| **tool_call_parser** | `qwen3_xml` (카드 권장: `qwen3_coder`) | `qwen3_xml` | `gemma4` | `gemma4` |
| **reasoning_parser** | `qwen3` | `qwen3` | `gemma4` | `gemma4` |
| **샘플링 (Thinking)** | temp=1.0, top_k=20, top_p=0.95, presence_penalty=1.5 | temp=0.6, top_k=20, top_p=0.95 | temp=1.0, top_k=64, top_p=0.95 | temp=1.0, top_k=64, top_p=0.95 |
| **MTP Speculative Decoding** | ✅ | ✅ | ❌ | ❌ |
| **vLLM 최소 버전** | 0.19.0 | 0.18.0 | 0.19.0 | 0.19.0 |
| **transformers 최소 버전** | ≥4.56.0 | ≥4.56.0 | ≥5.5.0 | ≥5.5.0 |

> 두 Qwen3.5 vs 3.6, Gemma 4 vs Qwen 3.6 상세 비교는 [`slm_research/comparison.md`](slm_research/comparison.md) 참고.

### 7.2 모델 교체 퀵 가이드

`vllm_config.yaml`에서 아래 부분만 바꾸면 됩니다. 포트·GPU 설정은 모델 무관.

```yaml
# ── Qwen3.6-35B-A3B-FP8 (현재, Hybrid MoE) ──
model: Qwen/Qwen3.6-35B-A3B-FP8
# quantization 생략 (사전 양자화 체크포인트, 자동 감지)
served_model_name: [Qwen3.6-35B-A3B-FP8]
tool_call_parser: qwen3_xml              # 모델 카드 권장은 qwen3_coder
reasoning_parser: qwen3
# Mamba-hybrid 운영 필수
async_scheduling: false
mm_encoder_tp_mode: data
mm_processor_cache_type: shm

# ── Qwen3.5 27B FP8로 교체 시 ──
# model: Qwen/Qwen3.5-27B-FP8
# served_model_name: [Qwen3.5-27B-FP8]
# tool_call_parser: qwen3_xml
# reasoning_parser: qwen3

# ── Gemma 4 26B-A4B (MoE, BF16→FP8 온라인 양자화) ──
# model: google/gemma-4-26B-A4B-it
# quantization: fp8
# served_model_name: [gemma-4-26B-A4B-it]
# tool_call_parser: gemma4
# reasoning_parser: gemma4
# # 비전 토큰 예산(기본 280 → 560 권장, 문서/차트 QA 최소선). 상세는 §7.4 참고.
# mm_processor_kwargs:
#   max_soft_tokens: 560
# limit_mm_per_prompt:
#   image: 4
#   audio: 0
#   video: 0

# ── Gemma 4 31B (Dense) ──
# model: google/gemma-4-31B-it
# quantization: fp8
# served_model_name: [gemma-4-31B-it]
# tool_call_parser: gemma4
# reasoning_parser: gemma4
# # 비전 토큰 예산(기본 280 → 560 권장, 문서/차트 QA 최소선). 상세는 §7.4 참고.
# mm_processor_kwargs:
#   max_soft_tokens: 560
# limit_mm_per_prompt:
#   image: 4
#   audio: 0
#   video: 0
```

> 교체 후 `.env`의 `CHAT_MODEL`도 `served_model_name`과 일치시키세요.
>
> **Qwen3.6 → Gemma 4 교체 시 멀티모달 플래그 정정** (vLLM 0.19.0 소스 검증):
> - `mm_encoder_tp_mode: data` — Gemma 4는 `supports_encoder_tp_data=False`(`gemma4_mm.py`에 플래그 없음)라 `vllm/config/model.py:617-625`에서 **"weights"로 자동 폴백 + 경고**. 설정 자체는 무해하나 효과 없음 → Gemma 4에서는 제거 권장.
> - `mm_processor_cache_type: shm` — **모델 독립 글로벌 파라미터**(`vllm/multimodal/registry.py:276-328`). Gemma 4에서도 동일하게 IPC 중복 제거 효과 → 그대로 **유지 권장**.
> - `async_scheduling: false` — Qwen3.6 Mamba-hybrid + `mamba_cache_mode=align` 조합의 encoder cache race 방어선이 구체적 사유. Gemma 4에서는 `align` 모드가 없으므로 기본값(`true`)으로 되돌려 TPS 5~15% 회수 가능. 단, 다중 이미지 동시성이 높으면 안전하게 `false` 유지도 가능.

### 7.3 GPU 메모리 참고

`gpu_memory_utilization` 기준. 현재 0.9 (Mamba-hybrid KV 추정 오차 대비 보수).

**L40S 46GB × 1장**:

| 모델 | 가중치 | KV Cache 가용 | 권장 `max_model_len` |
|------|--------|--------------|---------------------|
| Qwen3.6-35B-A3B-FP8 | ~35 GB | 부족 | **비권장** (TP=2 사용) |
| Gemma 4 31B FP8 (온라인) | ~29 GB | ~14.7 GB | 12288 |
| Qwen3.5-27B FP8 | ~27 GB | ~16.7 GB | 12288~16384 |
| 14B BF16 | ~28 GB | ~15.7 GB | 12288~32768 |
| 8B BF16 | ~16 GB | ~27.7 GB | 32768~65536 |

**L40S 46GB × 2장 (`tensor_parallel_size: 2`) — 현재 운영**:

| 모델 | 가중치 (rank당) | KV Cache 가용 | 권장 `max_model_len` |
|------|-----------------|---------------|----------------------|
| **Qwen3.6-35B-A3B-FP8 (현재)** | ~17.5 GB | 넉넉 (Mamba-hybrid로 Full-Attn 대비 절감) | 262144 (실기동 확인) |
| Qwen3.5-27B FP8 | ~13.5 GB | ~60.4 GB | 65536~131072 |
| Gemma 4 31B FP8 (온라인) | ~14.5 GB | ~58.4 GB | 65536~131072 |

> ⚠️ vLLM KV cache profiler가 Mamba-hybrid 구조에서 ~7배 과잉추정(vllm-project/vllm [#37121](https://github.com/vllm-project/vllm/issues/37121))되는 이슈가 있습니다. 기동 로그의 실제 `num_gpu_blocks`를 확인해 튜닝하세요.

### 7.4 Gemma 4 비전 토큰 예산 튜닝

Gemma 4 비전 인코더는 이미지당 **soft token 고정 예산** 방식으로 가변 해상도를 처리합니다. 기본값 280은 챗봇 썸네일급(≈ 645K 픽셀)에 맞춰져 있어 문서·차트·스크린샷 QA 같은 고해상도 이미지에서는 디테일 손실이 발생합니다. 공식 허용값과 대응 픽셀 면적은 다음과 같습니다 (transformers `Gemma4ImageProcessor` 공식).

| `max_soft_tokens` | Patches (pooling 전) | 대응 픽셀 면적 | 대응 해상도 | 용도 |
|:-:|:-:|:-:|:-:|:--|
| 70 | 630 | ~161K | ~400×400 | 썸네일, 아이콘 |
| 140 | 1,260 | ~323K | ~570×570 | 중저해상도 |
| **280 (기본)** | **2,520** | **~645K** | **~800×800** | 일반 사진 |
| 560 | 5,040 | ~1.3M | ~1152×1152 | **문서·차트 QA 최소 권장** |
| 1120 | 10,080 | ~2.6M | ~1620×1620 | OCR·세밀 디테일 |

> ⚠️ `gemma4_mm.py:474-485`에 validator가 하드코딩되어 있어 **이 5개 값을 벗어난 정수는 `sys.exit(1)`로 기동 자체가 실패**합니다. 중간값(예: 400)은 불가.

#### 설정 방법 3가지

**(a) YAML — `vllm_config.yaml` (서버 전체 기본값)**
```yaml
mm_processor_kwargs:
  max_soft_tokens: 560
limit_mm_per_prompt:
  image: 4
  audio: 0
  video: 0
```

**(b) CLI — `vllm serve` 네이티브**
```bash
vllm serve google/gemma-4-31B-it \
  --mm-processor-kwargs '{"max_soft_tokens": 560}' \
  --limit-mm-per-prompt image=4
```

**(c) 요청 단위 override — 특정 요청만 예산 변경**
```json
{
  "model": "gemma-4-31B-it",
  "messages": [
    {"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
      {"type": "text", "text": "Extract all text in this document."}
    ]}
  ],
  "mm_processor_kwargs": {"max_soft_tokens": 1120}
}
```
→ 썸네일 요청은 140, 문서 이미지는 1120처럼 워크로드별 동적 조절.

#### 워크로드별 권장값

| 워크로드 | `max_soft_tokens` | 근거 |
|----------|:-:|------|
| 챗봇 일반 사진(프로필, 로고 등) | 280 (기본) | ~800×800까지 정보 보존 충분 |
| 문서·차트·슬라이드 QA | **560** (운영 시작점) | A4 상단 1/3 ~ 전체 읽기 가능. 프로젝트 권장 디폴트 |
| OCR·세밀 표·소형 글씨 | 1120 | 이미지당 비용 4× 감수 |
| 썸네일 전용 | 70~140 | KV/prefill 비용 절약 |

#### 트레이드오프 (운영 영향)

| 항목 | 280 (기본) | 560 | 1120 |
|------|:-:|:-:|:-:|
| 이미지당 KV 캐시 점유 | 1× | 2× | 4× |
| vision prefill TTFT | 기준 | ~1.7–2× | ~3–4× |
| 문서·차트 정확도 | 표준 | +α | 최대 |
| `max_num_batched_tokens` 여유 | 넉넉 | 고려 필요 | **재튜닝 필수** |

560으로 올릴 때 `max_num_batched_tokens`는 `이미지당 토큰 × 동시 이미지 수 + 텍스트 여유` 기준으로 재산정하세요. 현재 98304 설정에서 `max_num_seqs: 5` × 이미지 1장이면 여유롭지만, 560 × 4장 요청이 들어오면 encoder cache 압박.

#### 다른 모델과의 키 충돌 — vLLM 0.19.0 실측

- `mm_processor_kwargs.max_soft_tokens`는 **Gemma 4 `Gemma4MultiModalProcessor`에서만 해석**됩니다. Qwen3-VL/InternVL 등 다른 VL 모델에서는 `vllm/multimodal/processing/context.py:260`의 `get_allowed_kwarg_only_overrides`가 HF processor signature를 inspect하여 **WARNING 로그 후 자동 드롭**합니다. 기동 실패나 런타임 에러는 나지 않습니다.
- Qwen3-VL 계열(Qwen3.5/3.6 MoE 포함)의 비전 파라미터는 `min_pixels` / `max_pixels` / `fps` / `num_frames` 체계(`qwen3_vl.py:733-740`)이며 Gemma 4와 호환되지 않습니다.

> 프로젝트에서는 혼란 방지를 위해 `vllm_config.yaml`의 해당 블록을 **Qwen 운영 중에는 주석 상태로** 두고, Gemma 4 교체 시 한 덩어리를 해제하는 방식으로 관리합니다.

---

## 8. Qwen3.6 고급 기능

### 8.1 MTP Speculative Decoding

Qwen3.6-35B-A3B는 Multi-Token Prediction으로 사전·사후 학습됐습니다. vLLM Speculative Decoding으로 **2토큰 예측**을 활성화하면 처리량이 향상됩니다 (B200 기준 실측 ~96K tokens/s, 수락률 90%).

```bash
vllm serve Qwen/Qwen3.6-35B-A3B-FP8 \
  --tensor-parallel-size 2 \
  --max-model-len 262144 \
  --reasoning-parser qwen3 \
  --speculative-config '{"method": "mtp", "num_speculative_tokens": 2}'
```

> ⚠️ **MTP method 표기 차이**: vLLM recipes는 `"method": "mtp"`, HF 모델 카드는 `"method": "qwen3_next_mtp"`. 두 문자열 모두 동일 MTP 경로지만 vLLM 버전마다 허용 값이 다를 수 있습니다. **운영 투입 전 실제 vLLM 0.19.0에서 시도 후 채택**하세요.

### 8.2 preserve_thinking (에이전트 반복 루프 최적화)

Qwen3.6 고유 신규 옵션. 멀티턴 대화에서 **이전 턴 reasoning**을 자동으로 히스토리에 유지해, 복잡한 에이전트 루프의 토큰 재사용 효율을 높입니다.

```json
{
  "model": "Qwen3.6-35B-A3B-FP8",
  "messages": [ ... ],
  "chat_template_kwargs": {
    "enable_thinking": true,
    "preserve_thinking": true
  }
}
```

> - Qwen3.5, Gemma 4는 이 옵션 **미지원**. 교체 시 필드를 제거해야 합니다.
> - `/think`·`/nothink` 소프트 스위치는 **공식 미지원** (Qwen3 계열과의 분기점).

### 8.3 컨텍스트 1M 확장 (YaRN)

Qwen3.6는 YaRN으로 `max_model_len`을 1,010,000까지 확장할 수 있습니다. 다만 KV cache 부담이 폭증해 **현재 L40S×2 프로필에서는 비권장**합니다.

```bash
# 참고용 — 실제 운영에서는 262144 유지
VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 vllm serve Qwen/Qwen3.6-35B-A3B-FP8 \
  --hf-overrides '{"text_config": {"rope_parameters": {"mrope_interleaved": true, "mrope_section": [11, 11, 10], "rope_type": "yarn", "rope_theta": 10000000, "partial_rotary_factor": 0.25, "factor": 4.0, "original_max_position_embeddings": 262144}}}' \
  --max-model-len 1010000
```

### 8.4 Thinking 모드 제어 (서버 vs 요청)

| 레벨 | 설정 방법 | 용도 |
|------|----------|------|
| **서버 기본값** | `vllm_config.yaml`의 `default_chat_template_kwargs.enable_thinking` | 모든 요청의 기본 동작 |
| **요청 단위** | request body의 `chat_template_kwargs.enable_thinking` | 해당 요청만 온·오프 |

```yaml
# 서버 기본: 비활성화 (챗봇용)
default_chat_template_kwargs:
  enable_thinking: false

# 서버 기본: 활성화 (reasoning 분리가 항상 필요할 때)
default_chat_template_kwargs:
  enable_thinking: true
```

모델별 thinking 토큰 형식 차이:

| 모델 | reasoning_parser | 토큰 형식 |
|------|-----------------|-----------|
| Qwen 3 / 3.5 / 3.6 | `qwen3` | `<think>사고과정</think>최종답변` |
| Gemma 4 | `gemma4` | `<\|channel>thought...<channel\|>최종답변` |
| DeepSeek R1 | `deepseek_r1` | `<think>사고과정</think>최종답변` |

---

## 9. 트러블슈팅 & 운영 주의

### 9.1 멀티모달 Encoder Cache — 가장 주의할 포인트

vLLM V1의 **encoder cache**는 멀티모달 모델의 비전 인코더 출력(embedding)을 보관합니다. 이미지 1장이 패치 분할 후 만드는 encoder output 토큰 수를 단위로 동작합니다.

**설계 제약** (vLLM 0.19.0 기준):

- `encoder_cache_size`는 **사용자가 직접 설정할 수 없음** (`config/scheduler.py:94-106`).
- 내부적으로 `max_num_batched_tokens` 값이 그대로 복사됨 (`scheduler.py:235`).
- `max_num_seqs`와는 **연동되지 않음** — 동시 요청 상한을 올려도 encoder cache는 안 커짐.

**용량 산정식**:

```
encoder cache 수용 이미지 수 ≈ max_num_batched_tokens ÷ (이미지 1장 encoder 토큰)
```

Qwen3.6-VL 계열 기준 이미지 1장 ≈ 16,384 encoder 토큰. 현재 `max_num_batched_tokens: 163840` → 약 10장 수용.

### 9.2 `Encoder cache miss for <hash>` 크래시 대응

**증상**: 정상 동작하다가 worker가 assertion으로 죽고 APIServer도 shutdown.

```
AssertionError: Encoder cache miss for <hash>.
  at gpu_model_runner.py:2961 _gather_mm_embeddings
```

**근본 원인**: encoder cache 용량 < 동시 멀티모달 요청 수 + `async_scheduling` pipeline race. scheduler 장부와 worker 실제 cache 상태가 1-step 어긋날 때 발생.

**과거 사례 (2026-04-18 07:17:51 GPU0_1)**:
- 동시 5개 이미지 요청 + encoder cache budget 2장 분량 + async scheduling 활성.
- 14시간 가동 후 경합 타이밍이 맞아 assertion. 평소 Running 1~2개일 땐 표면화되지 않음.

**해결 설정 (동시 N장 기준)**:

| 설정 | 값 | 이유 |
|------|----|------|
| `async_scheduling` | **false** | scheduler-worker pipeline race 제거 (TPS 5~15% 감소 감수) |
| `max_num_batched_tokens` | **N × 16384 × 여유 20%** | encoder cache 동반 확장. N=5 → 98304. 현재 163840으로 10장 여유 |
| `max_num_seqs` | **N** | 동시 요청 상한을 encoder cache 수용량과 매칭 |

현재 `vllm_config.yaml`에 3가지가 모두 반영돼 있습니다. 트래픽이 더 늘면 `max_num_batched_tokens`와 `max_num_seqs`를 비례 증가하세요.

### 9.3 `disable_chunked_mm_input`이 Qwen3.6에 금지인 이유

> ⚠️ **`disable_chunked_mm_input: true`를 Qwen3.6에서 절대 설정하지 마세요.**

Qwen3.6-35B-A3B는 Mamba-hybrid 구조라서, `enable_prefix_caching: true`가 켜지면 vLLM이 `mamba_cache_mode='align'`을 자동 적용합니다.

- align 모드는 attention block_size를 1056 같은 큰 값으로 확장합니다.
- 따라서 MM 입력을 block_size 배수로 쪼갤 유연성이 반드시 필요합니다.
- `vllm/config/vllm.py:1730`의 `validate_block_size()`가 `disable_chunked_mm_input=True`를 **AssertionError로 거부**합니다.

일반 VL 모델 가이드에서 이 옵션을 권장하는 글이 많지만, Mamba-hybrid에서는 반대로 동작합니다. 이 모델의 encoder cache 방어선은 `async_scheduling: false` + `max_num_seqs` 상한 + `max_num_batched_tokens` 조합으로 충분하도록 설계되어 있습니다.

### 9.4 동시 요청이 `max_num_seqs`를 초과하면?

vLLM scheduler가 FCFS(First-Come-First-Served)로 자동 큐잉합니다:

- 앞 N개: 즉시 Running.
- 나머지: Waiting 큐 (KV/encoder cache 할당 없음, 메모리 거의 안 먹음).
- Running 완료 시마다 Waiting에서 1개씩 promote.
- Gateway HTTP 타임아웃(300s)이 실질 대기 상한.

로그 확인:

```
Engine 000: ... Running: 5 reqs, Waiting: 3 reqs, ...
```

### 9.5 좀비 Worker 프로세스 주의

engine crash 시 APIServer는 shutdown되지만 **Worker 프로세스가 좀비로 남아 GPU 메모리를 계속 점유**하는 경우가 있습니다. `start.sh`의 `/health` 폴링은 이걸 감지 못해 `[SKIP] 실행 중 아님`으로 오판합니다.

재기동 시 OOM(`CUDA error: out of memory`)이 나면:

```bash
# GPU 점유 프로세스 확인
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader

# vllm 관련 프로세스 확인
ps aux | grep -E "vllm|Worker_TP" | grep -v grep

# 정상 종료 시도
kill <좀비 PID들>

# 안 죽으면 강제 종료
kill -9 <좀비 PID들>
```

### 9.6 알려진 vLLM 이슈 (운영 영향도)

| 이슈 | 요약 | 현재 방어선 |
|------|------|------------|
| [vllm #37121](https://github.com/vllm-project/vllm/issues/37121) | Hybrid Mamba/Attention KV cache ~7배 과잉추정 | 기동 로그의 `num_gpu_blocks` 재확인 후 튜닝 |
| [vllm #37602](https://github.com/vllm-project/vllm/issues/37602) | Qwen3.5 계열 동시 이미지 10+에서 EngineCore 크래시 | `max_num_seqs: 5` 상한 |
| [vllm #38643](https://github.com/vllm-project/vllm/issues/38643) | Qwen3.5 FLA linear attention 포맷 불일치 gibberish | vLLM 0.19.0 수정 여부 확인 필요 |
| [vllm #40124](https://github.com/vllm-project/vllm/issues/40124) | TurboQuant KV + Hybrid MoE가 Ampere(SM 80-86)에서 실패 | **L40S(Ada Lovelace, SM 89) 무영향** — GPU 교체 시에만 주의 |
| 자체 Bug 2026-04-18 | `Encoder cache miss` assertion | `async_scheduling: false` + `max_num_seqs` 상한. 상세는 [bugfix/2026-04-18_vllm_multimodal_encoder_cache.md](bugfix/2026-04-18_vllm_multimodal_encoder_cache.md) |

### 9.7 운영 환경 튜닝 백로그

#### Gemma 4 26B-A4B (E=128, N=352, fp8_w8a8) fused MoE config 부재

기동 로그에 다음 WARNING이 출력된다.

```
WARNING fused_moe.py:1090
Using default MoE config. Performance might be sub-optimal!
Config file not found at .../configs/E=128,N=352,device_name=NVIDIA_<GPU>,dtype=fp8_w8a8.json
```

**상태**: vLLM 0.19.1 동봉 311개 사전 튜닝 JSON 중 26B-A4B + fp8_w8a8 매칭은 H100_80GB_HBM3 한 종 뿐. L40S(개발)와 RTX PRO 6000 Blackwell(운영 예정) 모두 매칭 JSON 없음 → default fallback 동작.

**영향**: 정확도/안정성에는 영향 없음. MoE GEMM throughput 잠재 손실 (조합에 따라 10~30%).

**대응 (운영 이전 후)**:

```bash
# 1) vLLM 소스 클론 (튜닝 스크립트는 pip 패키지에 미포함)
git clone https://github.com/vllm-project/vllm.git /tmp/vllm
cd /tmp/vllm

# 2) 운영 GPU(RTX PRO 6000)에서 튜닝 실행 (vLLM 잠시 내려야 함)
python benchmarks/kernels/benchmark_moe.py \
  --model google/gemma-4-26B-A4B-it \
  --tp-size <운영 TP 크기> \
  --dtype fp8_w8a8 \
  --tune

# 3) 산출물을 vLLM이 읽는 경로에 배치
cp E=128,N=352,device_name=NVIDIA_RTX_PRO_6000_Blackwell_Workstation_Edition,dtype=fp8_w8a8.json \
   ~/.local/lib/python3.12/site-packages/vllm/model_executor/layers/fused_moe/configs/

# 4) vLLM 재기동 → WARNING 사라지고 튜닝 config 적용
```

**선택 사항**: 산출 JSON을 vLLM 본가 `vllm/model_executor/layers/fused_moe/configs/`에 PR. RTX PRO 6000 Blackwell 변종은 이미 코어팀이 다른 모델용으로 동봉 시작한 GPU라 머지 가능성 높음.

**트리거**: 운영 환경 셋업 완료 후. 개발 환경 L40S에서는 의미 없음 (운영 환경 아님).

---

## 10. QA 테스트

서버 배포 후 기능 검증을 자동화하는 스크립트. Python 표준 라이브러리만 사용합니다.

### 10.1 기본 사용법

```bash
cd /workspace/chatbot-poc/scripts/vllm

# 전체 테스트 (모델명은 vllm_config.yaml에서 자동 추출)
python test_vllm_server.py

# 커스텀 서버·모델
python test_vllm_server.py --base-url http://gpu-server:5015 --model MyModel

# 특정 카테고리만
python test_vllm_server.py --category infra inference tool

# 카테고리 목록
python test_vllm_server.py --list

# 상세 출력
python test_vllm_server.py -v
```

### 10.2 테스트 카테고리

| 카테고리 | 키 | 테스트 수 | 검증 내용 |
|---------|-----|----------|----------|
| 서버 기동 | `infra` | 3 | 헬스체크, 모델 목록, 잘못된 엔드포인트 |
| 기본 추론 | `inference` | 4 | 단일턴, 시스템 프롬프트, 멀티턴, 잘못된 모델명 |
| 스트리밍 | `streaming` | 2 | SSE 청크, usage 반환 |
| 샘플링 | `sampling` | 4 | temperature 범위, max_tokens 경계, 잘못된 값 |
| Thinking | `thinking` | 3 | 기본 OFF, 요청 단위 ON/OFF |
| Tool Calling | `tool` | 4 | 단일/복수 호출, 불필요 시 스킵, 결과 반영 |
| 경계값 | `edge` | 6 | 빈 메시지, 긴 입력, 동시 5/10개, 잘못된 JSON |
| 캐싱 | `caching` | 1 | 프리픽스 캐싱 TTFT 비교 |

### 10.3 테스트 항목 상세

#### 서버 기동 / 인프라 (`infra`)

| ID | 테스트 | 판정 기준 |
|----|-------|----------|
| 1.1 | 헬스체크 | HTTP 200 |
| 1.2 | 모델 목록 조회 | `served_model_name` 포함 |
| 1.3 | 잘못된 엔드포인트 | HTTP 404/405 |

#### 기본 추론 (`inference`)

| ID | 테스트 | 판정 기준 |
|----|-------|----------|
| 2.1 | 단일 턴 짧은 응답 | HTTP 200 + content 비어있지 않음 |
| 2.2 | 시스템 프롬프트 반영 | 영어 응답 지시 준수 확인 |
| 2.3 | 멀티턴 맥락 유지 | 응답에 언급한 이름 포함 |
| 2.4 | 존재하지 않는 모델명 | HTTP 4xx |

#### 스트리밍 (`streaming`)

| ID | 테스트 | 판정 기준 |
|----|-------|----------|
| 3.1 | 기본 SSE 청크 | 청크 2개 이상 + `data: [DONE]` |
| 3.2 | 스트리밍 usage | 마지막 청크에 usage 포함 |

#### 샘플링 (`sampling`)

| ID | 테스트 | 판정 기준 |
|----|-------|----------|
| 4.1 | temperature=0 결정적 출력 | 동일 프롬프트 2회가 동일 응답 |
| 4.2 | temperature=1.5 크래시 없음 | HTTP 200 |
| 4.3 | max_tokens=1 | `finish_reason: length` + ≤2 토큰 |
| 4.4 | 잘못된 temperature | HTTP 400 |

#### Thinking (`thinking`)

| ID | 테스트 | 판정 기준 |
|----|-------|----------|
| 5.1 | OFF 기본 | content에 `<think>` 미포함 |
| 5.2 | 요청 단위 ON | `reasoning_content` 필드 존재 |
| 5.3 | 요청 단위 OFF 명시적 전달 | content에 `<think>` 미포함 |

> Qwen3.6 `<think>...</think>`는 일반 토큰이라 `skip_special_tokens: false` 불필요.
> Gemma 4로 교체 시에만 `<|channel>...<channel|>` 경계 토큰이 스페셜 토큰이므로 요청에 `skip_special_tokens: false` 추가 필요.

#### Tool Calling (`tool`)

| ID | 테스트 | 판정 기준 |
|----|-------|----------|
| 6.1 | 단일 Tool Call | `tool_calls[0].function.name == "lookup_coverage"` |
| 6.2 | 복수 Tool 선택 | 2개 Tool 모두 호출 |
| 6.3 | Tool 불필요 시 직접 응답 | `tool_calls` 없이 content |
| 6.4 | Tool 결과 반영 | 최종 응답에 Tool 결과 핵심 정보 포함 |

#### 경계값 (`edge`)

| ID | 테스트 | 판정 기준 |
|----|-------|----------|
| 7.1 | 빈 메시지 | HTTP 200 (크래시 없음) |
| 7.2 | 긴 입력 (~6000 토큰) | 정상 처리 + usage |
| 7.3 | 동시 5개 (max_num_seqs 이내) | 5개 모두 HTTP 200 |
| 7.4 | 동시 10개 (큐잉) | 10개 모두 HTTP 200, 크래시 없음 |
| 7.5 | 잘못된 JSON | HTTP 400/422 |
| 7.6 | 필수 필드 누락 | HTTP 400/422 |

#### 캐싱 (`caching`)

| ID | 테스트 | 판정 기준 |
|----|-------|----------|
| 8.1 | 프리픽스 캐싱 TTFT | 2차 요청이 1차보다 빠름 |

---

## 11. 참고 자료

### 11.1 프로젝트 파일 구성

```
scripts/vllm/
├── VLLM_OPS_GUIDE.md            ← 이 문서
├── vllm_config.yaml             ← vLLM 서버 설정 (모델, 포트, GPU 등)
├── vllm_gateway_config.yaml     ← 게이트웨이 설정 (헬스체크, 웜업)
├── vllm_server_launcher.py      ← vLLM 런처 (환경변수 자동 + vllm serve 호출)
├── vllm_gateway.py              ← 게이트웨이 (로드밸런싱 + 헬스체크 + 웜업)
├── start.sh                     ← 클러스터 시작/중지/상태 오케스트레이터
├── test_vllm_server.py          ← QA 테스트 스크립트
├── bugfix/                      ← 운영 중 발견한 버그 기록
│   └── 2026-04-18_vllm_multimodal_encoder_cache.md
├── slm_research/                ← 모델 조사 문서
│   ├── gemma4.md
│   ├── qwen35.md
│   ├── qwen36.md
│   └── comparison.md
└── logs/                        ← 서버/게이트웨이 로그 (gitignore 대상)
```

### 11.2 관련 문서

- [slm_research/qwen36.md](slm_research/qwen36.md) — Qwen3.6 모델 상세 스펙·벤치마크·운영 메모
- [slm_research/qwen35.md](slm_research/qwen35.md) — Qwen3.5 조사
- [slm_research/gemma4.md](slm_research/gemma4.md) — Gemma 4 조사
- [slm_research/comparison.md](slm_research/comparison.md) — Gemma 4 vs Qwen 3.6 비교
- [bugfix/2026-04-18_vllm_multimodal_encoder_cache.md](bugfix/2026-04-18_vllm_multimodal_encoder_cache.md) — encoder cache race 해결 기록

### 11.3 외부 링크

- [vLLM 공식 문서](https://docs.vllm.ai/)
- [Qwen3.6 HuggingFace 모델 카드](https://huggingface.co/Qwen/Qwen3.6-35B-A3B-FP8)
- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat) (vLLM이 호환하는 API 명세)
