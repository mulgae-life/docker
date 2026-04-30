---
name: project
description: docker 레포 핵심 요약. 서버·운영 구성 자산 모음으로 디렉토리 분리 원칙과 기술 스택 파악용.
last-updated: 2026-04-30
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
└── llm-serving/                      # LLM 서빙 프레임워크 모음
    ├── README.md                     # 프레임워크 인덱스
    ├── DEPLOY_GUIDE.md               # 서빙 인프라 배포 가이드
    └── vllm/                         # 운영 중 (격리 페어 + 자동 디스커버리)
        ├── VLLM_OPS_GUIDE.md         # 운영 가이드 (핵심)
        ├── start.sh                  # 빠른 기동 (instances/+gateways/ 자동 순회)
        ├── vllm_server_launcher.py   # 단일 vLLM 기동 + 포트 자동 회피
        ├── vllm_gateway.py           # OpenAI 호환 게이트웨이 + 자동 디스커버리
        ├── instances/                # 인스턴스 yaml (모델/포트/GPU)
        │   ├── gemma.yaml            #   ├ gateway_port: 5015 → :5015 페어
        │   └── qwen.yaml             #   └ gateway_port: 5016 → :5016 페어
        ├── gateways/                 # 게이트웨이 yaml (포트/디스커버리)
        │   ├── 5015.yaml
        │   └── 5016.yaml
        ├── test_vllm_server.py       # 서버 헬스/추론 테스트
        ├── slm_research/             # SLM 비교 (Gemma, Qwen)
        └── bugfix/                   # 운영 중 발견 이슈 기록
```

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| **컨테이너** | Docker, Docker Compose v2, Buildx |
| **베이스 OS** | Ubuntu 24.04, NVIDIA CUDA 12.6.3-devel-ubuntu24.04 |
| **GPU 호스트** | NVIDIA Open Driver, NVIDIA Container Toolkit, Fabric Manager (H100/H200/A100/B100/B200) |
| **클라우드** | AWS EC2 (g6e/p4/p5), EBS, IAM/S3, SSM Session Manager |
| **서빙** | vLLM, FastAPI 게이트웨이 (OpenAI 호환) — 향후 SGLang, STT(Whisper) 추가 예정 |
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
| `llm-serving/vllm/vllm_server_launcher.py` | 다중 vLLM 서버 기동 (GPU 분할) |
| `llm-serving/vllm/vllm_gateway.py` | OpenAI 호환 + 모델 라우팅 게이트웨이 |
| `llm-serving/vllm/VLLM_OPS_GUIDE.md` | vLLM 운영 가이드 (핵심 참조 문서) |

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

# 3) vLLM 서빙
cd llm-serving/vllm
./start.sh up              # instances/+gateways/ 자동 순회 (포트 충돌 시 자동 회피)
./start.sh status          # 인스턴스/게이트웨이 상태 확인
python test_vllm_server.py # 추론/스트리밍/툴콜 QA
```

> 자세한 절차/트러블슈팅은 각 디렉토리의 README 또는 `llm-serving/vllm/VLLM_OPS_GUIDE.md` 참조.

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
| [../llm-serving/vllm/VLLM_OPS_GUIDE.md](../llm-serving/vllm/VLLM_OPS_GUIDE.md) | vLLM 운영 가이드 |
