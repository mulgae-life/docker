# 🐳 my-docker-server (로컬 dev / GPU 환경)

로컬 PC·사내 서버에 띄우는 풀스택 개발 + GPU 연산용 Docker 환경.

> AWS EC2 운영 인프라는 [`../aws/`](../aws/), LLM 서빙은 [`../llm-serving/`](../llm-serving/) 참조.

## 📦 이미지 구성

| 파일 | 베이스 | 용도 |
|------|--------|------|
| `Dockerfile.dev` | Ubuntu 24.04 | 풀스택 개발 (Node, Python, Playwright 등) |
| `Dockerfile.gpu` | NVIDIA CUDA 12.6 + Ubuntu 24.04 | GPU 연산 (CUDA, CuPy, NumPy 등) |

## 🛠️ 포함 스택

### dev (풀스택 개발)

| 영역 | 구성 |
|------|------|
| **런타임** | Node.js LTS (nvm), Python 3.12 |
| **패키지 매니저** | pnpm, yarn, pip |
| **프레임워크** | Next.js, FastAPI, LangChain |
| **DB/검색** | PostgreSQL client, Supabase CLI, ChromaDB |
| **크롤링/테스트** | Playwright + Chromium, BeautifulSoup |
| **도구** | Git, GitHub CLI, Claude Code, Codex, tmux, fzf, ripgrep |
| **로케일** | 한국어 (ko_KR.UTF-8), 서울 타임존 |

### cfd (GPU 연산)

| 영역 | 구성 |
|------|------|
| **런타임** | Node.js LTS (nvm), Python 3.12, CUDA 12.6 |
| **라이브러리** | NumPy, Numba, CuPy, Matplotlib |
| **도구** | Git, Claude Code, Codex, tmux, cmake |
| **로케일** | 한국어 (ko_KR.UTF-8), 서울 타임존 |

## 🚀 빠른 시작

```bash
# 1. Docker 설치 (없을 경우)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER  # 재로그인 필요

# 2. 레포 클론
git clone https://github.com/mulgae-life/docker.git
cd docker/my-docker-server

# 3. 환경 변수 설정
cp .env.example .env
# .env 파일에서 USERNAME, PASSWORD를 본인 계정으로 수정
# UID/GID는 호스트 사용자와 일치시킴 (id -u / id -g 로 확인)

# 4. dev만 빌드 + 실행
docker compose up -d --build dev

# 5. GPU 포함 전체 실행 (NVIDIA GPU + nvidia-container-toolkit 필요)
docker compose up -d --build

# 6. 접속 (.env에서 설정한 USERNAME 사용)
ssh <USERNAME>@localhost -p 5010     # dev
ssh <USERNAME>@localhost -p 5000     # cfd
```

## 🔐 환경 변수 (.env)

`.env` 파일에서 사용자 정보를 설정합니다. **이 파일은 Git에 포함되지 않습니다.**

```env
USERNAME=user        # 컨테이너 내 사용자명
PASSWORD=changeme    # SSH 및 sudo 비밀번호
UID=2000             # 호스트 사용자의 UID (id -u 로 확인)
GID=2000             # 호스트 사용자의 GID (id -g 로 확인)
```

> `.env.example`을 복사한 후 본인 환경에 맞게 수정하세요.
> 기본값은 UID/GID `2000`입니다. 호스트 계정과 다르면 마운트한 `/workspace`의 권한이 어긋날 수 있으니 반드시 일치시킵니다.

## 💾 데이터 영속화

| 경로 | 타입 | 호스트 위치 | 설명 |
|------|------|-----------|------|
| `/workspace` | bind mount | `/workspace` | 코드/프로젝트 (호스트와 공유) |
| `/home/<USERNAME>` | bind mount | `/opt/docker-homes/<서비스>/<USERNAME>` | 사용자 홈 (재빌드 시에도 유지) |

> `<서비스>`는 `docker-compose.yml`의 서비스 이름(`cfd` 또는 `dev`)이며, 컨테이너 이름(`dev-fullstack` 등)과 다릅니다.

홈 디렉토리가 호스트에 영속화되므로, 재빌드해도 다음 항목이 보존됩니다:
- Claude Code / Codex 로그인 및 대화기록
- SSH 키, Git 설정
- `.bashrc`, `.tmux.conf` 등 사용자 설정
- npm/pip 캐시

첫 실행 시 홈 디렉토리가 비어있으면 Dockerfile의 초기 설정이 자동 복사됩니다.

> ⚠️ Dockerfile의 `.bashrc` 등 설정을 변경한 경우, 기존 파일이 우선합니다. 초기화하려면:
> ```bash
> sudo rm -rf /opt/docker-homes/dev/<USERNAME>
> docker compose up -d --build
> ```

## 🔌 포트 매핑

| 컨테이너 | SSH | 서비스 포트 | 메모리 제한 |
|----------|-----|------------|-----------|
| cfd | 5000 | 5001-5009 | 24g |
| dev-fullstack | 5010 | 5011-5019 | 24g |

## 📂 프로젝트 셋업

컨테이너 접속 후:

```bash
cd /workspace

# GitHub 인증
gh auth login

# 프로젝트 클론 예시
gh repo clone <org>/<repo>
cd <repo>
cp .env.example .env
pip install -r requirements.txt
```

## ⚙️ 커스터마이징 가이드

### 하드웨어 사양에 따른 설정

`docker-compose.yml`에서 수정:

```yaml
deploy:
  resources:
    limits:
      memory: 24g    # ← 호스트 RAM에 맞게 조정
```

| 호스트 RAM | 컨테이너당 권장 | 비고 |
|-----------|--------------|------|
| 16GB | 12g | 호스트 여유 4GB 확보 |
| 32GB | 24g | 현재 설정 |
| 64GB | 48g | 대규모 모델/데이터 처리 |

### GPU 설정

`docker-compose.yml` → cfd 서비스:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all          # ← 특정 GPU만: count: 1
          capabilities: [gpu]
```

GPU가 없는 환경에서는 cfd 서비스의 `deploy` 블록을 제거하고 `docker compose up -d dev`로 dev만 실행.

### CUDA 버전 변경

`Dockerfile.gpu` 1행:

```dockerfile
FROM nvidia/cuda:12.6.3-devel-ubuntu24.04  # ← 버전 변경
```

[사용 가능한 CUDA 이미지 목록](https://hub.docker.com/r/nvidia/cuda/tags)

### Node.js 버전 변경

`Dockerfile.dev`, `Dockerfile.gpu`에서 `nvm install --lts` 부분을 원하는 버전으로 교체합니다 (예: `nvm install 22 && nvm alias default 22`). `default` 심링크 로직은 그대로 유지됩니다.

### Python 패키지 추가/제거

`Dockerfile.dev` → `pip install --no-cache-dir` 블록에서 패키지 추가/제거.
`Dockerfile.gpu` → GPU 관련 Python 패키지 동일.

### 포트 범위 변경

`docker-compose.yml`의 `ports`와 Dockerfile의 `EXPOSE`를 함께 수정:

```yaml
# docker-compose.yml
ports:
  - "5010:22"              # SSH
  - "5011-5019:5011-5019"  # ← 범위 변경 시 양쪽 동일하게
```

```dockerfile
# Dockerfile
EXPOSE 22 5011-5019        # ← compose와 일치시킴
```

### 볼륨 마운트 경로 변경

작업 디렉토리를 다른 경로로 변경하려면 `docker-compose.yml`에서:

```yaml
volumes:
  - /my/path:/workspace  # ← 호스트 경로를 원하는 곳으로 변경
```

## 🏃 운영 명령

```bash
docker compose ps            # 상태 확인
docker compose logs -f       # 로그
docker compose restart       # 재시작
docker compose up -d --build # Dockerfile 수정 후 재빌드
docker compose down          # 컨테이너 삭제 (홈 데이터 유지)
```
