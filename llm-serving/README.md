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

| 파일 | 역할 |
|------|------|
| [`vllm/VLLM_OPS_GUIDE.md`](vllm/VLLM_OPS_GUIDE.md) | 운영 가이드 (모델 추가, 트러블슈팅, 멀티 GPU 분할) |
| [`vllm/start.sh`](vllm/start.sh) | 빠른 기동 스크립트 |
| [`vllm/vllm_server_launcher.py`](vllm/vllm_server_launcher.py) | vLLM 서버 런처 |
| [`vllm/vllm_gateway.py`](vllm/vllm_gateway.py) | OpenAI 호환 게이트웨이 (모델 라우팅) |
| [`vllm/vllm_config.yaml`](vllm/vllm_config.yaml) | 서버 설정 (모델/포트/GPU 분할) |
| [`vllm/vllm_gateway_config.yaml`](vllm/vllm_gateway_config.yaml) | 게이트웨이 라우팅 설정 |
| [`vllm/test_vllm_server.py`](vllm/test_vllm_server.py) | 서버 헬스/추론 테스트 |
| [`vllm/slm_research/`](vllm/slm_research/) | SLM 비교 리서치 (Gemma, Qwen) |
| [`vllm/bugfix/`](vllm/bugfix/) | 운영 중 발견된 이슈 기록 |

자세한 사용법은 [`vllm/VLLM_OPS_GUIDE.md`](vllm/VLLM_OPS_GUIDE.md) 참조.

## ➕ 새 프레임워크 추가 시

`llm-serving/<framework>/` 디렉토리를 만들고:
- 운영 가이드(`<FRAMEWORK>_OPS_GUIDE.md`)
- 기동 스크립트
- 설정 파일
- 테스트 스크립트

위 vLLM 구조를 참고해 일관된 형태로 추가합니다.
