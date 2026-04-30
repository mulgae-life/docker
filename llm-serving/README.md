# 🚀 LLM Serving

LLM 서빙 프레임워크 운영 구성 모음. 서버 인프라(EC2/Docker)와 분리된, **서빙 레이어 전용** 디렉토리.

> 인프라(EC2 + Docker)는 [`../aws/`](../aws/), 컨테이너 환경은 [`../my-docker-server/`](../my-docker-server/) 참조.
> 코드/모델 이관(로컬 → S3 → EC2 → 컨테이너) 절차는 [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md) 참조.

## 📦 구성

| 프레임워크 | 상태 | 용도 |
|-----------|:----:|------|
| [`vllm/`](vllm/) | ✅ 운영 | vLLM 서버 + 멀티 GPU 게이트웨이 (한 인스턴스에서 다중 모델 라우팅, OpenAI 호환) |
| `sglang/` | 🔜 예정 | SGLang 기반 서빙 |
| [`stt/`](stt/) | 🧪 PoC | Qwen3-ASR-1.7B + Whisper-large-v3 동시 서빙 (transcription endpoint) — 한국어 비교용 |

## 🎯 vLLM (현재 운영 중)

설정은 **인스턴스 단위 yaml**(`instances/`)과 **게이트웨이 단위 yaml**(`gateways/`)로 분리. 게이트웨이는 `discover_from` + 인스턴스 yaml의 `gateway_port` 메타 키로 backends를 자동 매칭한다 (수동 명시 불필요).

| 파일 / 디렉토리 | 역할 |
|------|------|
| [`vllm/VLLM_OPS_GUIDE.md`](vllm/VLLM_OPS_GUIDE.md) | 운영 가이드 (모델 추가, 트러블슈팅, 멀티 GPU 분할) |
| [`vllm/start.sh`](vllm/start.sh) | 빠른 기동 스크립트 (`up [name]` / `down [name]` / `status`) |
| [`vllm/vllm_server_launcher.py`](vllm/vllm_server_launcher.py) | vLLM 서버 런처 (인스턴스 yaml `-c` 인자 수신) |
| [`vllm/vllm_gateway.py`](vllm/vllm_gateway.py) | OpenAI 호환 게이트웨이 (자동 디스커버리 LB) |
| [`vllm/instances/`](vllm/instances/) | **인스턴스 단위 yaml** (`<name>.yaml` 1개 = vLLM 프로세스 1대). `gateway_port` 메타 + 모델/포트/GPU |
| [`vllm/gateways/`](vllm/gateways/) | **게이트웨이 단위 yaml** (`<port>.yaml` 1개 = 게이트웨이 1대). `discover_from`으로 인스턴스 자동 매칭 |
| [`vllm/test_vllm_server.py`](vllm/test_vllm_server.py) | 서버 헬스/추론 테스트 |
| [`vllm/slm_research/`](vllm/slm_research/) | SLM 비교 리서치 (Gemma, Qwen) |
| [`vllm/bugfix/`](vllm/bugfix/) | 운영 중 발견된 이슈 기록 |

운영 인스턴스/게이트웨이 추가는 yaml 한 파일 복사 → 값만 수정 → `./start.sh up <name>` (인스턴스) 또는 게이트웨이 재기동(자동 디스커버리). 자세한 사용법은 [`vllm/VLLM_OPS_GUIDE.md`](vllm/VLLM_OPS_GUIDE.md) 참조.

## ➕ 새 프레임워크 추가 시

`llm-serving/<framework>/` 디렉토리를 만들고:
- 운영 가이드(`<FRAMEWORK>_OPS_GUIDE.md`)
- 기동 스크립트
- 설정 파일
- 테스트 스크립트

위 vLLM 구조를 참고해 일관된 형태로 추가합니다.
