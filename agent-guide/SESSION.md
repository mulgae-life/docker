---
name: session
description: docker 레포 현재 상태. 세션 시작 시 다음 작업과 최근 변경 파악용.
last-updated: 2026-04-29
---

# 세션 상태

> 세션 시작 시 현재 상태를 빠르게 파악하기 위한 문서. 갱신은 세션 종료 시.

---

## 작업 관리

| 항목 | 내용 |
|------|------|
| **이슈 트래커** | 별도 도구 없음 (git history + 본 SESSION.md "다음 작업" 표) |
| **원격 레포** | `git@github.com:mulgae-life/docker.git` (`origin/main`) |
| **배포 채널** | `aws s3 sync . s3://hgi-ai-res/hjjo/aws/` (코드) → EC2 동기화 |

---

## 다음 작업

| 우선순위 | 작업 | 상태 |
|---------|------|------|
| P1 | `llm-serving/sglang/` 디렉토리 골격 (운영 가이드 + 런처 + 설정 + 테스트) | Todo |
| P2 | `llm-serving/stt/` 디렉토리 골격 (Whisper 등 STT 전용 서빙) | Todo |
| P2 | `aws/` 추가 안전성·정합성 보강 (최근 흐름 연속) | In progress |
| P3 | `agent-guide/` MCP 도구 섹션 채우기 (필요 시) | Todo |

---

## 기타 이슈

- `llm-serving/vllm/vllm_config.yaml`에 unstaged 로컬 변경이 있음 (작업 외 변경, 사용자 직접 처리 예정)
- `agent-guide/` 자체는 untracked 상태 — 첫 커밋 필요

---

## 최근 세션

### 2026-04-29

#### 세션 목표
- 레포 디렉토리 재편 정리 및 문서 정합성 확보
- agent-guide 3종 파일 초기화

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `README.md` (루트) | 재작성 | "서버 세팅·운영 구성 모음" 메타 안내로 99% rewrite |
| `my-docker-server/{Dockerfile.dev,Dockerfile.gpu,docker-compose.yml,entrypoint.sh,.env.example}` | 이동(rename) | 루트 → `my-docker-server/`, UID/GID 기본값 2000으로 통일 |
| `my-docker-server/README.md` | 추가 | 기존 루트 README 기반 + UID 2000 + `<서비스>` 표기 명확화 |
| `llm-serving/vllm/*` | 추가 | `vllm/` 자산을 `llm-serving/vllm/`으로 추가 (코드/설정/가이드/리서치) |
| `llm-serving/README.md` | 추가 | 프레임워크 인덱스 (vLLM 운영 + SGLang/STT 예정) |
| `.gitignore` | 보강 | `llm-serving/vllm/{logs/, image.png}` ignore + EOF newline |
| `agent-guide/{GUIDE,PROJECT,SESSION}.md` | 추가 | AI 에이전트 가이드 3종 초기화 |

#### 결정 사항
- 레포를 **3-디렉토리 분리** 구조로 확정: `my-docker-server` (로컬 dev/GPU) ↔ `aws` (EC2 인프라) ↔ `llm-serving` (서빙 프레임워크)
- 디렉토리 분리 원칙을 `PROJECT.md`에 명문화 (신규 파일 위치 결정 기준)
- `my-docker-server/`의 UID/GID 기본값을 `2000`으로 통일 (`.env.example` 기준)
- `llm-serving/`은 인덱스 README + 프레임워크별 서브디렉토리(vllm/sglang/stt) 형태로 확장

#### 커밋
| 해시 | 메시지 |
|------|--------|
| `b3159e9` | refactor: 레포 구조 재편 (my-docker-server / aws / llm-serving) |
| `60d148a` | docs: README 호스트 경로 표기 명확화 + .gitignore EOF newline |

> `60d148a`는 `origin/main`에 push 대기 중 (사용자 직접 push 예정).

#### 현재 상태
- 레포 구조 재편 + 문서 정합성 확보 완료
- 다음: SGLang/STT 서빙 디렉토리 골격, aws 후속 보강
