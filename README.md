# Docker 개발 환경

풀스택 개발 환경 Docker 이미지 모음.

## 이미지 구성

| 파일 | 베이스 | 용도 |
|------|--------|------|
| `Dockerfile.dev` | Ubuntu 24.04 | 풀스택 개발 (Node, Python, Playwright 등) |
| `Dockerfile.gpu` | NVIDIA CUDA 12.6 + Ubuntu 24.04 | GPU 연산 (CUDA, CuPy, NumPy 등) |

## 포함 스택

### dev (풀스택 개발)

| 영역 | 구성 |
|------|------|
| **런타임** | Node.js 22 LTS, Python 3.12 |
| **패키지 매니저** | pnpm, yarn, pip |
| **프레임워크** | Next.js, FastAPI, LangChain |
| **DB/검색** | PostgreSQL client, Supabase CLI, ChromaDB |
| **크롤링/테스트** | Playwright + Chromium, BeautifulSoup |
| **도구** | Git, GitHub CLI, tmux, fzf, ripgrep |
| **로케일** | 한국어 (ko_KR.UTF-8), 서울 타임존 |

### gpu (GPU 연산)

| 영역 | 구성 |
|------|------|
| **런타임** | Python 3.12, CUDA 12.6 |
| **라이브러리** | NumPy, Numba, CuPy, Matplotlib |
| **도구** | Git, tmux, cmake |

## 빠른 시작

```bash
# 1. Docker 설치 (없을 경우)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER  # 재로그인 필요

# 2. 레포 클론
git clone https://github.com/mulgae-life/docker.git
cd docker

# 3. dev만 빌드 + 실행
docker compose up -d --build dev

# 4. GPU 포함 전체 실행 (NVIDIA GPU + nvidia-container-toolkit 필요)
docker compose up -d --build

# 5. 접속
ssh hjjo@localhost -p 2222      # dev
ssh hjjo@localhost -p 2223      # gpu
```

## 사용자명/비밀번호 변경

```bash
docker compose build --build-arg USERNAME=myuser --build-arg PASSWORD=mypass dev
```

## 포트 매핑

| 호스트 | 컨테이너 | 서비스 | 용도 |
|--------|----------|--------|------|
| 2222 | 22 | dev | SSH |
| 3001 | 3001 | dev | Next.js (culture-calendar) |
| 8000 | 8000 | dev | FastAPI (chatbot-poc) |
| 2223 | 22 | gpu | SSH |

추가 포트가 필요하면 `docker-compose.yml`에서 수정.

## 프로젝트 셋업

컨테이너 접속 후:

```bash
cd /workspace

# GitHub 인증
gh auth login

# culture-calendar
gh repo clone mulgae-life/culture-calendar
cd culture-calendar
cp .env.example .env
ln -s ../../.env apps/web/.env.local
pnpm install
pnpm dev                    # http://서버IP:3001

# chatbot-poc
cd /workspace
gh repo clone mulgae-life/chatbot-poc
cd chatbot-poc
cp .env.example .env
pip install -r requirements.txt --break-system-packages
uvicorn app.main:app --host 0.0.0.0 --port 8000  # http://서버IP:8000
```

## 운영 명령

```bash
docker compose ps            # 상태 확인
docker compose logs -f       # 로그
docker compose restart       # 재시작
docker compose up -d --build # Dockerfile 수정 후 재빌드
docker compose down -v       # 완전 삭제 (주의: 데이터 삭제)
```

