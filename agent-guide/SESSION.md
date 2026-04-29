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
| P2 | **RTX PRO 6000 Blackwell 운영 이전 후 fused MoE 튜닝**: `benchmark_moe.py`로 `E=128,N=352,device_name=NVIDIA_RTX_PRO_6000_Blackwell_Workstation_Edition,dtype=fp8_w8a8.json` 생성 → site-packages `vllm/model_executor/layers/fused_moe/configs/`에 배치 → 가능하면 vLLM 본가 PR. 트리거: 운영 환경 셋업 완료 시점 | Todo |
| P3 | `agent-guide/` MCP 도구 섹션 채우기 (필요 시) | Todo |

---

## 기타 이슈

- `llm-serving/vllm/{VLLM_OPS_GUIDE.md, start.sh, vllm_config.yaml}`에 unstaged 로컬 변경 (작업 외 변경, 사용자 직접 처리 예정)

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
| `d80e3bb` | docs: agent-guide 3종 초기화 (GUIDE / PROJECT / SESSION) |

#### 현재 상태
- 레포 구조 재편 + 문서 정합성 확보 완료
- 다음: SGLang/STT 서빙 디렉토리 골격, aws 후속 보강

---

### 2026-04-29 (추가 세션)

#### 세션 목표
- `aws/` P2 안전성·정합성 보강 4건 일괄 적용
- 내부망 운영 정책 문서화

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `aws/entrypoint-llm.sh` | 수정 | 빈 홈/UID 불일치 분기 통합 → 두 케이스 모두 `setup_user_home` 호출 (P2.5) |
| `aws/setup-ec2.sh` | 수정 | `/volume`을 `root:root` + `0775`로 통일, `root-homes` 사전 생성, USERNAME 분기 3종 명시 (P2.7) |
| `aws/requirements.txt` | 수정 | `pytest>=9.0.0` → `pytest>=8.0` 보수화 (P2.8) |
| `aws/README.md` | 수정 | §1 내부망 표기 박스 + §9-2 rebuild 한계(`down→up` 사이클) 안내 (P2.6) |
| `aws/ssh-config-sample` | 수정 | 호스트 IP 갱신 (`3.35.12.44` → `3.38.195.121`) |

#### 결정 사항
- **내부망 운영 정책**: HF_TOKEN/IP/PASSWORD 등 시크릿 노출 검토 적용 안 함. 정합성·안정성·운영 편의성에 집중. memory `project_internal_network.md`에 영구 저장
- `/volume` 자체 소유권은 컨테이너 동작에 무관(직접 마운트 없음) → 정합성 차원에서 `root:root` 통일
- `user.sh rebuild`는 `PASSWORD/GPU/MODE` 보존이 의도된 설계 → 코드 변경 대신 README 명시로 해결

#### 현재 상태
- aws P2 4건 + 부가 1건 모두 적용 + 셸 문법 검증 + 정합성 cross-check 통과
- 추가 검증 필요(EC2 환경): `docker compose build` 의존성 / `setup-ec2.sh` 재실행 멱등성
