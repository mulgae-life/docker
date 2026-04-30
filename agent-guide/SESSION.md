---
name: session
description: docker 레포 현재 상태. 세션 시작 시 다음 작업과 최근 변경 파악용.
last-updated: 2026-04-30 (vLLM 게이트웨이 자동 디스커버리 + yaml 통일)
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
| P1 | **vLLM Qwen 본체 :7080 이전**: `instances/qwen.yaml`의 `port: 7080`이 의도된 다음 운영 포트. 게이트웨이 :5016은 메모리상 :7071 보유 중이라 즉시 영향 없으나, 다음 게이트웨이 재기동 시 yaml 기준(:7080)으로 디스커버리하므로 그 시점 전에 vLLM 본체를 :7080으로 옮겨야 정합. | Todo |
| P1 | `llm-serving/sglang/` 디렉토리 골격 (운영 가이드 + 런처 + 설정 + 테스트) | Todo |
| P1 | **`llm-serving/stt/` PoC**: 시나리오 D 확정 — **Qwen3-ASR-1.7B + Whisper-large-v3** 동시 서빙(GPU 0/1 분리, port 7170/7171, transcription endpoint). 인프라(start.sh + 모델별 config 2종 + README) 구현 완료. 다음 단계: 실제 기동(LLM 인스턴스 stop 필요) → 한국어 벤치(`test_stt.py`, WER/RTF/latency) → 게이트웨이 통합 | In progress (인프라 구축 완료, 기동/벤치 대기) |
| P2 | **RTX PRO 6000 Blackwell 운영 이전 후 fused MoE 튜닝**: `benchmark_moe.py`로 `E=128,N=352,device_name=NVIDIA_RTX_PRO_6000_Blackwell_Workstation_Edition,dtype=fp8_w8a8.json` 생성 → site-packages `vllm/model_executor/layers/fused_moe/configs/`에 배치 → 가능하면 vLLM 본가 PR. 트리거: 운영 환경 셋업 완료 시점 | Todo |
| P3 | `agent-guide/` MCP 도구 섹션 채우기 (필요 시) | Todo |

---

## 기타 이슈

- 없음 (이전 세션의 unstaged 변경은 commit `2905914`로 정리됨)

---

## 최근 세션

### 2026-04-30 (vLLM 게이트웨이 자동 디스커버리 + yaml 통일)

#### 세션 목표
- vLLM 게이트웨이 ↔ 인스턴스 페어 격리 + 자동 디스커버리 구조(Phase 2) 도입
- 다중 모델/LB 시나리오에서 게이트웨이 yaml 백엔드를 수동 명시 없이 자동 매칭
- 신규 인스턴스 yaml의 복붙 확장성 확보 — 주석/구조 통일 + 운영 노하우 보존

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `llm-serving/vllm/instances/{gemma,qwen}.yaml` | 신규 (각 390줄) | 인스턴스 단위 yaml. `gateway_port` 메타 키로 소속 게이트웨이 선언. archive 원본의 모든 운영 노하우 주석을 두 파일에 동일하게 보존 |
| `llm-serving/vllm/gateways/{5015,5016}.yaml` | 신규 (각 70줄) | 게이트웨이 단위 yaml. `discover_from: ../instances`로 자동 매칭. backends 수동 명시는 escape hatch로 유지 |
| `llm-serving/vllm/vllm_gateway.py` | 수정 (+107줄) | `_discover_backends()` 추가, `load_config` 우선순위 재정의: backends → discover_from → deprecated fallback. vLLM port 중복 검증, `gateway.port` 누락 ValueError |
| `llm-serving/vllm/vllm_server_launcher.py` | 수정 (+22줄) | `_LAUNCHER_KEYS`에 `gateway_port` 추가 (vllm serve 인자 누수 방지). docstring을 `instances/<name>.yaml` 형태로 갱신 |
| `llm-serving/vllm/start.sh` | 재작성 (+327/-220) | `instances/*.yaml` + `gateways/*.yaml` 자동 순회. 인터페이스 `up [name]` / `down [name]` / `status` / `restart [name]`. 단일 인스턴스 모드는 게이트웨이 미터치 |
| `llm-serving/vllm/{vllm_config,vllm_gateway_config}.yaml` | 이동(아카이브) | `agent-guide/.archive/2026-04-30_vllm-config-migration/`로 mv (rm 금지) |
| `agent-guide/SESSION.md` | 수정 | 본 세션 + 다음 작업(P1 Qwen :7080 이전) 추가 |
| `llm-serving/README.md`, `llm-serving/DEPLOY_GUIDE.md`, `llm-serving/vllm/VLLM_OPS_GUIDE.md` | 수정 | 새 디렉토리 구조 반영 (instances/, gateways/, discover_from, gateway_port) |
| `memory/lessons_archive_via_mv.md` | 신규 | 산출물 정리 시 mv 아카이빙 원칙. "삭제할까요?" 프레이밍 금지 |
| `memory/feedback_preserve_operational_comments.md` | 신규 | 통일 = 구조/위치/주석 동일화이지 주석 단축 아님. 운영 노하우 보존 원칙 |

#### 결정 사항
- **자동 디스커버리 채택 (Phase 2)**: 게이트웨이 yaml에서 backends 수동 명시 대신 `discover_from` + 인스턴스 yaml의 `gateway_port` 메타 키로 단방향 선언. 복붙 확장 시 한 파일만 추가하면 게이트웨이 재기동 시 자동 등록.
- **격리 페어 + LB 양립**: 같은 `gateway_port`를 갖는 인스턴스가 여러 개면 자동 LB. 다른 게이트웨이 소속이면 무시. vLLM port 중복은 게이트웨이 기동 시 ValueError로 거부.
- **escape hatch 유지**: 게이트웨이 yaml에 `backends:` 명시 시 그쪽이 우선. 1세대 fallback(`vllm_config + backend_count`)도 deprecated 경고와 함께 유지 — 호환성/디버깅 목적.
- **무중단 마이그레이션**: vLLM 본체 2대(:7070, :7071) 무중단 유지. 게이트웨이만 신규 yaml로 재기동.
- **yaml 통일 = 동일 구조 + 풍부 주석 보존**: 1차 통일에서 운영 노하우 주석을 일반화 핑계로 다이어트했다가 대표님 항의로 archive 원본 베이스 풍부 복원. 두 instances yaml 라인 수 390/390, top-level 키 29/29 완전 일치, diff 13라인(모두 모델/리소스 값).
- **포트 자동 회피**: 인스턴스 yaml의 `port`는 hint. launcher가 socket binding test로 사용 중이면 `+1, +2 ...` 비어있는 첫 포트로 자동 회피. 실제 포트를 `instances/.runtime/<name>.json`에 기록하고 게이트웨이가 이 파일을 우선 참조. **복붙 LB 시나리오에서 port 깜빡 안 바꿔도 자동으로 다른 포트에 띄우고 게이트웨이가 자동 LB**. 검증: 같은 yaml port 7000 두 인스턴스 → 자동 회피 7000+7001 → 게이트웨이가 둘 다 backends 등록 (시뮬레이션 테스트 6/6 통과).
- **__pycache__ 추적 끊기**: `git rm --cached llm-serving/vllm/__pycache__/*.pyc` (working tree 보존). `.gitignore`의 `__pycache__/` 룰이 이미 있어 향후 추가 추적 안 됨. 추가로 `.runtime/` 룰 등록.

#### 운영 정합성 메모
- 현재 :5016 게이트웨이는 메모리상 backend `:7071` 유지(재기동 안 됨). vLLM 본체도 :7071 살아있음 → 클라이언트 호출 즉시 영향 없음.
- `instances/qwen.yaml`의 `port: 7080`은 다음 운영 단계에서 의도된 포트 (대표님 직접 변경). 다음 :5016 게이트웨이 재기동 시 yaml 기준(:7080)으로 디스커버리 → vLLM 본체를 :7080으로 옮긴 후 게이트웨이 재기동하는 흐름이 정상.

#### 교훈 (영구 기록)
- **rm 금지, mv 아카이빙 일변도** (`lessons_archive_via_mv.md`): 자율 작업 마지막 정리 시 "삭제할까요?" 프레이밍하지 말고 처음부터 `.archive/<YYYY-MM-DD>_<태그>/`로 mv. work-principles에 이미 명문화된 룰을 위반.
- **통일 ≠ 주석 다이어트** (`feedback_preserve_operational_comments.md`): "두 파일 동일하게"는 구조/위치/주석 텍스트 동일화이지 노하우 일반화가 아님. 라인 참조, 크래시 사후 분석 메모, allowed values 표 등은 한두 줄로 복원 불가능한 깊이라 두 파일 모두에 동일하게 유지.

#### 커밋
| 해시 | 메시지 |
|------|--------|
| `2905914` | update (Phase 2 자동 디스커버리 + yaml 통일/복원 + 아카이빙 일괄) |

#### 현재 상태
- 디스커버리/통일/노하우 보존 모두 완료, work-verify 통과
- 다음: vLLM Qwen 본체 :7080 이전 (yaml과 본체 정합 회복) → SGLang 골격 / STT 첫 기동

---

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

### 2026-04-29 (3차 세션 — STT 인프라 + 배포 가이드)

#### 세션 목표
- STT PoC 시나리오 확정 + vLLM 통합형 인프라 구현 (Qwen3-ASR-1.7B + Whisper-large-v3 동시 서빙)
- llm-serving 전체 배포 가이드 작성 (로컬 → S3 → 운영계 컨테이너)

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `llm-serving/stt/configs/qwen3_asr.yaml` | 추가 | Qwen3-ASR-1.7B (GPU 0, :7170, transcription) |
| `llm-serving/stt/configs/whisper_v3.yaml` | 추가 | Whisper-large-v3 (GPU 1, :7171, baseline) |
| `llm-serving/stt/start.sh` | 추가 | configs/*.yaml 순회 인스턴스 기동/중지/상태 (vllm 런처 재사용) |
| `llm-serving/stt/README.md` | 추가 | 사용법 + 트러블슈팅 + GPU 점유 운영 주의 |
| `llm-serving/stt/MODEL_STUDY.md` | 수정 | §6 시나리오 D 확정 + actual 디렉토리 반영 + 변경 이력 entry 추가 |
| `llm-serving/DEPLOY_GUIDE.md` | 추가 | 로컬→S3→컨테이너 배포 가이드 (106줄, 슬림화 완료) |
| `llm-serving/README.md` | 수정 | stt 항목 갱신 + DEPLOY_GUIDE 링크 |
| `agent-guide/SESSION.md` | 수정 | P1 진행 상태 + 본 세션 추가 |
| `.gitignore` | 수정 | `llm-serving/stt/{logs,samples}/` 추가 |

#### 결정 사항
- **STT 시나리오 D 확정**: Qwen3-ASR-1.7B + Whisper-large-v3 (1.55B) 동시 서빙으로 한국어 비교. baseline은 turbo가 아닌 large-v3 (무게 매칭, 1.7B vs 1.55B)
- **vLLM 통합형 채택**: STT 전용 런처 작성 안 함 — `vllm/vllm_server_launcher.py` 그대로 재사용 (HF 다운로드/오프라인 모드/임시 config 처리 자산 활용)
- **모델별 config 분리**: 기존 vLLM은 "단일 모델 + DP 인스턴스"지만 STT는 이질 모델 2종 → `configs/{qwen3_asr,whisper_v3}.yaml` 분리 + start.sh가 자동 순회
- **배포 가이드 위치**: `vllm/VLLM_OPS_GUIDE.md` 가 아닌 `llm-serving/DEPLOY_GUIDE.md` 신규 (vllm + stt + 향후 sglang 통합 + aws/README와 1:1 짝)
- **배포 흐름 단순화**: `docker exec -it <컨테이너> bash` → `cd /workspace/` → `sudo aws s3 sync …` (컨테이너에 awscli/sudo 설치되어 있음 — `requirements.txt:awscli>=1.35.0`, `entrypoint-llm.sh:60` 의 sudo 그룹 추가 사실 확인)

#### 교훈
- 컨테이너/환경 사실(특정 도구 설치 여부 등)은 **Dockerfile 한 곳만 grep해서 단정 금지** — `requirements.txt`, `entrypoint`까지 모두 확인. 1차 점검에서 "컨테이너에 aws CLI 미설치" 잘못 단정 → 정정. memory `lessons_container_env_fact_check.md`에 영구 기록.
- 가이드/문서는 처음부터 슬림하게. 첫 작성에서 케이스 분리(메인 compose vs user.sh)와 변수 처리로 300줄 비대 → 대표님 지적 후 106줄로 재작성. 운영자가 한 명령으로 따라 칠 수 있는 형태가 핵심.

#### 현재 상태
- STT 인프라 구축 완료 (구문/파싱/start.sh status 모두 검증) — 실제 기동은 LLM stop 후 가능
- DEPLOY_GUIDE PoC 단계 적합 형태로 확정 + memory lesson 영구화
- 다음: STT 첫 기동 + 한국어 벤치 (`test_stt.py`)

---

### 2026-04-29 (2차 세션 — aws P2 보강)

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
