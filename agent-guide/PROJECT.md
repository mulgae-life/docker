---
name: project
description: docker 레포 핵심 요약. 서버·운영 구성 자산 모음으로 디렉토리 분리 원칙과 기술 스택 파악용.
last-updated: 2026-05-04
---

# 프로젝트 개요

> 서버 설치·운영·배포·서빙과 관련된 모든 인프라 구성을 한 레포에서 관리합니다. 로컬 dev / AWS 인프라 / LLM 서빙을 디렉토리로 분리해 각 영역을 독립적으로 갱신할 수 있게 구성한 **운영 자산 모음**입니다.

---

## TL;DR

| 항목 | 내용 |
|------|------|
| **프로젝트** | docker (서버 세팅 & 운영 구성 모음) |
| **목적** | 인프라(어디에 띄우는가) ↔ 서빙(무엇을 어떻게 띄우는가)을 한 레포에서 관리하면서 디렉토리로 책임 분리 |
| **기술 스택** | Docker, Ubuntu 24.04, NVIDIA CUDA 12.6, AWS EC2 GPU, vLLM, Python 3.12 / Node.js LTS |
| **운영 자산** | `my-docker-server/` (로컬 dev/GPU) + `aws/` (EC2 GPU 인프라) + `llm-serving/vllm/` (서빙) |
| **작업 관리** | 별도 도구 없음 → `git log` + `SESSION.md` "다음 작업" |

---

## 디렉토리 분리 원칙

| 레이어 | 책임 | 위치 | 안 다루는 것 |
|--------|------|------|------------|
| **개발 환경** | 로컬 PC·사내 서버에 띄우는 컨테이너 (개발자 PC) | `my-docker-server/` | EC2 호스트 셋업, 모델 서빙 |
| **인프라** | EC2 호스트 셋업, 드라이버, 다중 사용자, 포트/볼륨 정책 | `aws/` | 모델 추론 로직, 게이트웨이 라우팅 |
| **서빙** | LLM 모델 서빙 프레임워크 설정·게이트웨이·운영 가이드 | `llm-serving/` | 어디에 띄울지 (인프라), 컨테이너 OS 설정 |

> 신규 파일을 만들 때는 위 책임 표를 보고 디렉토리를 결정하세요. 예: vLLM의 새로운 멀티모달 설정은 `llm-serving/vllm/`, EC2 자동 스케일 정책은 `aws/`, 새로운 로컬 GPU 워크플로우는 `my-docker-server/`.

---

## 프로젝트 구조

```
docker/
├── README.md                         # 메타 진입점 (디렉토리 안내)
├── agent-guide/                      # AI 에이전트 가이드 (GUIDE/PROJECT/SESSION)
│
├── my-docker-server/                 # 로컬 dev/GPU Docker 환경
│   ├── Dockerfile.dev                # Ubuntu 24.04 + Node/Python/Playwright/CC
│   ├── Dockerfile.gpu                # CUDA 12.6 + CuPy/Numba
│   ├── docker-compose.yml            # cfd + dev-fullstack 서비스
│   ├── entrypoint.sh                 # 홈 디렉토리 초기화 + chown
│   ├── .env.example                  # USERNAME/PASSWORD/UID=2000/GID=2000
│   └── README.md
│
├── aws/                              # AWS EC2 GPU 인프라
│   ├── setup-ec2.sh                  # Phase 1 → reboot → Phase 2 자동
│   ├── user.sh                       # 다중 사용자 컨테이너 (포트 자동 할당)
│   ├── docker-compose.yml            # vLLM 베이스 컨테이너
│   ├── Dockerfile.llm                # vLLM 베이스 + SSH (dev/prd)
│   ├── entrypoint-llm.sh
│   ├── requirements.txt              # 컨테이너 내 pip (transformers는 --no-deps)
│   ├── .env.dev.example / .env.prd.example
│   ├── ssh-config-sample
│   └── README.md
│
└── llm-serving/                      # LLM/STT 서빙 프레임워크 모음
    ├── README.md                     # 프레임워크 인덱스
    ├── DEPLOY_GUIDE.md               # 서빙 인프라 배포 가이드 (LLM+STT 공용)
    ├── VLLM_API_GUIDE.md             # vLLM 사용자용 API 가이드 (호출 예시·파라미터)
    ├── VLLM_OPS_GUIDE.md             # vLLM 운영자용 가이드 (기동·튜닝·트러블슈팅)
    ├── STT_API_GUIDE.md              # STT 사용자용 API 가이드 (transcription·realtime·통합)
    ├── STT_OPS_GUIDE.md              # STT 운영자용 가이드 (시스템 구조·메모리 핏·의존성)
    ├── vllm/                         # LLM 운영 중 (격리 페어 + 자동 디스커버리)
    │   ├── start.sh                  # 빠른 기동 (instances/+gateways/ 자동 순회)
    │   ├── vllm_server_launcher.py   # 단일 vLLM 기동 + 포트 자동 회피 + yaml-relative runtime
    │   ├── vllm_gateway.py           # OpenAI 호환 게이트웨이 (chat + audio + realtime)
    │   ├── instances/                # 인스턴스 yaml (키당 제목 한줄 슬림, 상세는 _SCHEMA.txt)
    │   │   ├── _SCHEMA.txt            #   인스턴스 키 레퍼런스 + 모델별 노하우(MTP·soft_tokens·GPU표)
    │   │   ├── gemma.yaml            #   ├ 연구 gemma  gw 6015 → 외부 :5015 (PII 경유)
    │   │   ├── prd-gemma.yaml        #   ├ 운영 gemma  gw 5501 → :5501 직접 (PII 미경유)
    │   │   ├── prd-pii-gemma.yaml    #   ├ 운영 gemma  gw 6501 → :5501 (PII 경유)
    │   │   ├── qwen.yaml             #   ├ 연구 qwen   gw 6016 → :5016 (PII 경유)
    │   │   └── prd-pii-qwen.yaml     #   └ 운영 qwen   gw 6502 → :5502 (PII 경유)
    │   ├── gateways/                 # 게이트웨이 yaml (host/port만 차이, 상세는 _SCHEMA.txt)
    │   │   ├── _SCHEMA.txt            #   게이트웨이 키 레퍼런스
    │   │   ├── 5501.yaml             #   외부 직접 (0.0.0.0, 비PII 운영 gemma)
    │   │   └── 6015/6016/6501/6502.yaml  # 내부 전용 (외부 입구는 pii/ 프록시)
    │   ├── tests/                   # 테스트 코드/픽스처/결과 디렉토리
    │   │   ├── test_vllm_server.py  # 서버 헬스/추론 9 카테고리 QA
    │   │   ├── traffic_test_vllm.py # 보수적 트래픽/과부하 테스트
    │   │   ├── speed_test.py        # 모델 간 속도 매트릭스 누적 (--base-url 단독)
    │   │   ├── image.png            # 멀티모달 fixture
    │   │   └── results/             # speed_results.md 등 누적 리포트
    │   ├── slm_research/             # SLM 비교 (Gemma, Qwen)
    │   └── bugfix/                   # 운영 중 발견 이슈 기록
    ├── pii/                          # PII/DLP 가드 운영 중 (외부 포트 인수 → 게이트웨이 포워딩, enforcement)
    │   ├── start.sh                  # NER(GPU3)+프록시 기동 (up/down/status [5015|5016|5501|5502|all])
    │   ├── proxy.py                  # in/out PII 검사 프록시 (외부 5015/5016/5501/5502 인수)
    │   ├── ner_server.py             # 한국어 NER 서버 (transformers, GPU)
    │   ├── hooks.py/config.py/audit.py  # 통합 검사·설정·감사로그(평문 미저장 HMAC)
    │   ├── detectors/                # structured(regex+체크섬) + ner_client(LB) + normalize(NFKC)
    │   ├── configs/                  # proxy.{yaml(5015)/5501/5016/5502/e2e}.yaml + _SCHEMA.txt + audit.salt(시크릿, git·S3 제외)
    │   └── tests/                    # 단위 + e2e + eval_pii(합성 정확성) + recall_gate(실데이터 recall 게이트, data/ 라벨 JSONL)
    └── stt/                          # STT 운영 중 (vllm 페어 패턴 동일, launcher/gateway 본체 재사용)
        ├── README.md
        ├── MODEL_STUDY.md            # 후보 모델 비교 / 시나리오
        ├── start.sh                  # vllm/start.sh 패턴 풀 도입 (../vllm 코드 재사용)
        ├── instances/                # + _SCHEMA.txt (키 레퍼런스 + 모델별 표)
        │   ├── voxtral.yaml          # gw 5018 (realtime 분리, GPU 2, 내부 :7172)
        │   ├── qwen3_asr.yaml        # gw 5017 (GPU 0, 내부 :7170)
        │   └── whisper_v3.yaml       # gw 5017 (GPU 2, 내부 :7171)
        └── gateways/                 # 5017.yaml + 5018.yaml + _SCHEMA.txt (warmup off, audio timeout 600s)
```

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| **컨테이너** | Docker, Docker Compose v2, Buildx |
| **베이스 OS** | Ubuntu 24.04, NVIDIA CUDA 12.6.3-devel-ubuntu24.04 |
| **GPU 호스트** | NVIDIA Open Driver, NVIDIA Container Toolkit, Fabric Manager (H100/H200/A100/B100/B200) |
| **클라우드** | AWS EC2 (g6e/p4/p5), EBS, IAM/S3, SSM Session Manager |
| **서빙** | vLLM (chat + audio + realtime), FastAPI 게이트웨이 (OpenAI 호환 + 대기열 기반 과부하 차단). LLM(Gemma/Qwen :5015/:5016) + STT(Voxtral :5017). 향후 SGLang 추가 예정 |
| **런타임** | Python 3.12, Node.js LTS (nvm) |
| **개발 도구** | Claude Code, OpenAI Codex, GitHub CLI, tmux, fzf, ripgrep |
| **풀스택 SDK** | Next.js, FastAPI, LangChain, ChromaDB, Supabase CLI, Playwright |
| **GPU Python** | NumPy, Numba, CuPy, Matplotlib, nvitop |
| **보안 / 접근** | OpenSSH, fail2ban |
| **로케일** | ko_KR.UTF-8, Asia/Seoul |

---

## 핵심 파일

| 파일 | 역할 |
|------|------|
| `my-docker-server/docker-compose.yml` | `cfd`(GPU) + `dev`(풀스택) 서비스 정의, 호스트 홈 영속화 |
| `aws/setup-ec2.sh` | Amazon Linux 2023 호스트 1회 셋업 (사용자/SSH/EBS/Docker/NVIDIA, Phase 1↔2 자동 전환) |
| `aws/user.sh` | 사용자별 독립 컨테이너 + 포트 자동 할당(`up`/`down`/`list`/`rebuild`) |
| `aws/Dockerfile.llm` | vLLM 베이스 + SSH. dev/prd 모드 분기 |
| `aws/docker-compose.yml` | 메인 컨테이너 정의 (`.env`로 GPU/메모리/포트 제어) |
| `llm-serving/vllm/vllm_server_launcher.py` | 다중 vLLM 서버 기동 (GPU 분할, yaml-relative runtime json) — LLM/STT 공용 |
| `llm-serving/vllm/vllm_gateway.py` | OpenAI 호환 게이트웨이 (chat/completions + audio/transcriptions + realtime WebSocket) — LLM/STT 공용 |
| `llm-serving/vllm/tests/test_vllm_server.py` | 서버 헬스/추론 9 카테고리 QA |
| `llm-serving/vllm/tests/traffic_test_vllm.py` | 운영 서버 보호를 우선한 smoke/overload 트래픽 테스트 |
| `llm-serving/vllm/tests/speed_test.py` | 게이트웨이 단위 속도 매트릭스 측정 (`--base-url` 단독, 모델명 자동 추출, results/speed_results.md 누적 append) |
| `llm-serving/pii/proxy.py` | PII 가드 프록시 — 외부 5015/5501 인수, in(주민·카드 차단/이름·주소·전화 마스킹)·out 검사 후 게이트웨이 포워딩 |
| `llm-serving/pii/start.sh` | NER(GPU3 공유)+프록시 기동, 다중 포트 (`up/down/status [5015\|5501\|all]`), salt 자동주입 |
| `llm-serving/pii/tests/eval_pii.py` | PII 정확성 평가 (한국어 합성 케이스셋, 타입별 precision/recall + 과탐) |
| `llm-serving/pii/tests/recall_gate.py` | 실데이터 recall 게이트 하버스 (라벨 JSONL span-겹침 매칭, person/address/org ≥0.95 미달 시 exit 1, 데이터 없으면 스킵) |
| `llm-serving/VLLM_API_GUIDE.md` | vLLM 사용자용 API 가이드 (§1~§5: 호출·파라미터·`.env`) |
| `llm-serving/VLLM_OPS_GUIDE.md` | vLLM 운영자용 가이드 (§6~§15: 기동·튜닝·트러블슈팅·QA) |
| `llm-serving/STT_API_GUIDE.md` | STT 사용자용 API 가이드 (§1~§5: transcription·realtime·통합) |
| `llm-serving/STT_OPS_GUIDE.md` | STT 운영자용 가이드 (§6~§12: 시스템 구조·메모리 핏·의존성·트러블슈팅·QA) |
| `llm-serving/stt/start.sh` | STT 클러스터 기동 (vllm/start.sh 패턴 풀 도입, ../vllm 코드 재사용) |
| `llm-serving/stt/instances/voxtral.yaml` | Voxtral 인스턴스 (gateway_port 5017, GPU 2, 내부 :7172) |
| `llm-serving/stt/gateways/5017.yaml` | STT 게이트웨이 (warmup 비활성화, audio timeout 600s) |

---

## 빠른 시작

```bash
# 1) 로컬 dev/GPU 환경
cd my-docker-server
cp .env.example .env       # USERNAME/PASSWORD 수정 (UID/GID 기본 2000)
docker compose up -d --build       # GPU 미보유 시: docker compose up -d --build dev
ssh <USERNAME>@localhost -p 5010   # dev
ssh <USERNAME>@localhost -p 5000   # cfd

# 2) AWS EC2 GPU 인프라 (Amazon Linux 2023)
cd aws
cp .env.dev.example .env   # 또는 .env.prd.example
vim .env                   # USERNAME/PASSWORD/VOLUME_DEVICE/HF_TOKEN
sudo ./setup-ec2.sh        # Phase 1 → 자동 reboot → Phase 2
docker compose up -d --build
sudo ./user.sh up jin --password 1234 --gpus 0,1   # 추가 사용자

# 3) vLLM 서빙 (LLM)
cd llm-serving/vllm
./start.sh up              # instances/+gateways/ 자동 순회 (포트 충돌 시 자동 회피)
./start.sh status          # 인스턴스/게이트웨이 상태 확인
python tests/test_vllm_server.py # 추론/스트리밍/툴콜 QA
python tests/speed_test.py --base-url http://localhost:5015  # 모델 간 속도 매트릭스 누적

# 4) STT 서빙 (Voxtral, vllm 페어 패턴 동일)
cd ../stt
./start.sh up              # instances/voxtral.yaml ↔ gateways/5017.yaml 페어 자동
./start.sh status          # voxtral(:7172) + Gateway 5017 (ready 1/1)
curl http://localhost:5017/health   # 게이트웨이 health
```

> 자세한 절차/트러블슈팅은 각 디렉토리의 README 또는 `llm-serving/{VLLM,STT}_{OPS,API}_GUIDE.md` 참조.

---

## 상세 참조

| 문서 | 내용 |
|------|------|
| [SESSION.md](SESSION.md) | 현재 상태, 다음 작업, 최근 세션 로그 |
| [GUIDE.md](GUIDE.md) | 작업 원칙, 용어, 체크리스트 |
| [../README.md](../README.md) | 레포 메타 안내 (디렉토리 진입점) |
| [../my-docker-server/README.md](../my-docker-server/README.md) | 로컬 dev/GPU 환경 사용법 |
| [../aws/SETUP_GUIDE.md](../aws/SETUP_GUIDE.md) | EC2 셋업·다중 사용자·dev/prd 모드 |
| [../llm-serving/README.md](../llm-serving/README.md) | 서빙 프레임워크 인덱스 |
| [../llm-serving/VLLM_API_GUIDE.md](../llm-serving/VLLM_API_GUIDE.md) | vLLM 사용자용 API 가이드 |
| [../llm-serving/VLLM_OPS_GUIDE.md](../llm-serving/VLLM_OPS_GUIDE.md) | vLLM 운영자용 가이드 |
| [../llm-serving/STT_API_GUIDE.md](../llm-serving/STT_API_GUIDE.md) | STT 사용자용 API 가이드 (transcription·realtime) |
| [../llm-serving/STT_OPS_GUIDE.md](../llm-serving/STT_OPS_GUIDE.md) | STT 운영자용 가이드 |
| [../llm-serving/stt/README.md](../llm-serving/stt/README.md) | STT 디렉토리 안내 |
