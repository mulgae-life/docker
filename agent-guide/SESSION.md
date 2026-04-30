---
name: session
description: docker 레포 현재 상태. 세션 시작 시 다음 작업과 최근 변경 파악용.
last-updated: 2026-04-30 (5차 세션 — start.sh 운영 견고성 + launcher fcntl/atomic)
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

- `llm-serving/DEPLOY_GUIDE.md` 1·3절 미커밋 변경: `cd /workspace/...` 후 상대경로 → 절대경로 단일화 (외부 동기화 산출물로 추정, 이번 세션에서 의도된 변경 아님). commit/유지 여부 대표님 판단

---

## 최근 세션

### 2026-04-30 (5차 세션 — start.sh 운영 견고성 + launcher fcntl/atomic)

#### 세션 목표
- start.sh + launcher의 운영 race(동시 기동 / partial-read) 및 정체성 검증 보강

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `llm-serving/vllm/start.sh` | +127/-13 | cmdline 기반 launcher 정체성 검증, `cmd_up all` runtime 폴링, `stop_gateway` 종료 폴링/SIGKILL fallback, `cmd_status` ready/total 분리, `cmd_restart` sleep 2 제거 |
| `llm-serving/vllm/vllm_server_launcher.py` | +118/-22 | fcntl 기반 port-alloc 직렬화(`_allocate_port_and_register` + active runtime 점유 회피), `_write_runtime_file` atomic write(`tempfile.mkstemp` + `os.replace`) + `.json.tmp` 잔재 정리 |

#### 결정 사항
- launcher↔launcher race는 `fcntl.flock`으로, reader↔writer race(start.sh / 게이트웨이)는 `os.replace` atomic rename으로 분리 차단
- runtime 미등록 timeout 후에도 게이트웨이 진행 + `[WARN]` (부분 가용성 우선, fail-fast 거절)
- PID 재사용 방어는 `/proc/<pid>/cmdline` 매칭으로 launcher 정체성 보강 — start_instance / stop_instance / cmd_status 모두 적용

#### 현재 상태
- 미커밋: `start.sh`, `vllm_server_launcher.py`, `DEPLOY_GUIDE.md`(외부 변경)
- 다음: 운영계 배포 검증 → Qwen :7080 이전(이전 세션 P1)

---

### 2026-04-30 (4차 세션 — code-server 제거 + vLLM 운영 가이드/테스트 보강)

#### 세션 목표
- 폐쇄망 운영 환경 대응: `code-server` / `vsix` 인프라 전면 제거 (정보보호팀 방화벽 미허용)
- Phase 2 디스커버리 구조에 따른 운영 가이드/스크립트 정합화 (VLLM_OPS_GUIDE / start.sh / test_vllm_server)
- `aws/` 디렉토리 진입점 분리 — 안내용 README ↔ 셋업 가이드(SETUP_GUIDE.md)
- `user.sh` GPU 미할당 옵션(`--gpus none`) 운영 시나리오 명시

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `aws/{README.md → SETUP_GUIDE.md}` | rename + 보강 | 안내 README와 셋업 가이드 분리. 모드/사용자/포트/볼륨/배포까지 단일 가이드로 정리. 루트 `README.md`/`PROJECT.md` 진입점 갱신 |
| `aws/Dockerfile.llm` | -21줄 | `CODE_SERVER_VERSION` 설치 블록 + `COPY vsix/` + `find vsix install` 제거 |
| `aws/entrypoint-llm.sh` | -35줄 | `CODE_SERVER_PORT` env, code-server config.yaml 생성, `nohup`/`pgrep` 백그라운드 실행 제거. `/etc/bash.bashrc` SSH/`docker exec` 셸 환경 주석 정리 |
| `aws/docker-compose.yml` | -10줄 | `5500` 포트 매핑, `CODE_SERVER_PORT` env, `healthcheck`(URL 의존) 제거 |
| `aws/.env.{dev,prd}.example` | -2줄 | `LLM_CODE_SERVER_PORT=5500` + 주석 제거 |
| `aws/user.sh` | -41줄 | `--code-port` 인자 / `forced_code_port` / port_opts 매핑 / `--label code-port` / `-e CODE_SERVER_PORT` / `cmd_rebuild`의 `old_code_port` 추출 모두 제거. `--root` 분기에 `--gpus none` (`--runtime=runc`) 운영 동선 명시 |
| `aws/vsix/.gitkeep` | 삭제 | `git rm aws/vsix/.gitkeep && rmdir aws/vsix` (디렉토리 자체 제거) |
| `aws/SETUP_GUIDE.md` | 재작성 | §1 개요, §4 .env 표, §6 사용 예시(`job/gemma/mail` 다중 root + `--gpus none`), §7-2 운영 root 컨테이너(SSH 불가, `docker exec` 안내), §8 prd 모드, §11 트러블슈팅에서 code-server 흔적 일괄 제거 |
| `README.md` (루트) | 1줄 | `aws/` 안내 문구에서 "code-server" 표기 제거 + `SETUP_GUIDE.md` 진입점 링크 |
| `agent-guide/{PROJECT,GUIDE}.md` | 미세 정리 | `Dockerfile.llm` 설명 "vLLM 베이스 + SSH (dev/prd)"로 갱신, vsix/ 행 제거, `SSM Session Manager` 용어 통합, code-server 행 제거 |
| `llm-serving/vllm/VLLM_OPS_GUIDE.md` | 대규모 갱신 (+232 net) | 운영 모델 표기를 단일 → **격리 페어**(Gemma `:5015↔:7070`, Qwen `:5016↔:7080`)로, 새 디렉토리 구조(`instances/`, `gateways/`, `discover_from`, `gateway_port`) 반영. 포트 자동 회피 설명 추가. `start.sh` 인터페이스(`up`/`down`/`status` + `[name]` 자동 라우팅) 반영 |
| `llm-serving/vllm/start.sh` | 라우팅 보강 (+178/-92) | `[name]` 인자가 `instances/<name>.yaml`이면 인스턴스, `gateways/<name>.yaml`이면 게이트웨이로 자동 감지. 양쪽 충돌 시 즉시 에러. 매칭 실패 시 가용 후보 목록 출력 |
| `llm-serving/vllm/vllm_gateway.py` | 정리 (-43줄) | 잔재 1세대 fallback(`vllm_config + backend_count`)을 dead code로 확정 후 제거. `discover_from` 미설정도 즉시 ValueError(fail-fast) |
| `llm-serving/vllm/vllm_server_launcher.py` | 추가 보강 | docstring/CLI 메시지를 `instances/<name>.yaml` 기준으로 정합 |
| `llm-serving/vllm/test_vllm_server.py` | +273 net | (1) `_Tee` 로거: 콘솔에는 ANSI 색 유지, 파일에는 `\x1b\[...m` 제거하여 사후 가독성 확보. (2) `_record_request/_record_response/_reset_request_log` 도입 — `_run_test`가 fail 시 마지막 요청/응답 메타를 detail에 자동 첨부. (3) traceback 자동 첨부. (4) 보조 검증 강화 |
| `llm-serving/{DEPLOY_GUIDE.md, README.md}` | 보강 | 새 디렉토리 구조(`instances/`, `gateways/`) 반영 + 컨테이너 내 배포 흐름 정리 |
| `llm-serving/vllm/instances/{gemma,qwen}.yaml` | 미세 수정 (각 10줄) | 디스커버리 메타/주석 정합 |
| `.gitignore` | 보강 | `__pycache__` 추적 끊기, `.runtime/` 등 잔여 룰 정리 |
| `agent-guide/SESSION.md` | 갱신 | 본 4차 세션 entry 추가 + 다음 작업 / 기타 이슈 정정 |

#### 결정 사항
- **code-server / vsix 전면 제거**: 폐쇄망 운영서버에서 정보보호팀이 5500 포트 방화벽을 허용하지 않을 가능성이 높음 → 브라우저 IDE 대신 `docker exec`(컨테이너 내부 셸) + SSM Session Manager(호스트 셸) 조합으로 운영. `vsix/` 디렉토리도 사이드로드 미사용으로 함께 제거. 진입점 README/PROJECT.md/GUIDE.md/`SETUP_GUIDE.md`/`docker-compose.yml`/`Dockerfile.llm`/`entrypoint-llm.sh`/`.env.example`/`user.sh`까지 일괄 정합 (잔존 키워드 0건, bash/yaml syntax PASS).
- **`aws/README.md` → `SETUP_GUIDE.md`**: 디렉토리 진입점(README는 짧은 안내) ↔ 셋업 가이드(SETUP_GUIDE는 절차 중심)를 분리. 루트 README/PROJECT.md에서 `SETUP_GUIDE.md`로 직접 진입.
- **`user.sh --gpus none`**: GPU 미할당 컨테이너 기동을 운영 동선으로 명시(예: 메일/관제 등 비-GPU 서비스). 내부적으로 `--runtime=runc`로 nvidia 런타임 자체를 우회. 다중 root 시나리오 예시(`job/gemma/mail` 동시 운영)도 SETUP_GUIDE §6에 추가.
- **`user.sh` 단독 실행 가능**: `docker compose up -d` 없이도 이미지만 빌드돼 있으면 `user.sh up <name> --root --service-port ... --gpus ...`만으로 컨테이너 기동 가능. `cmd_up`의 의존은 이미지 존재 검사뿐(외부 네트워크/볼륨 없음).
- **`start.sh [name]` 라우팅 통합**: `instances/<name>.yaml`과 `gateways/<port>.yaml`을 같은 `[name]` 인자로 처리. 단일 게이트웨이 재기동도 인스턴스 미터치로 가능 → 무중단 LB 운영 패턴 단단해짐.
- **`test_vllm_server.py` 디버그 가독성**: 콘솔 컬러는 유지하되 파일 로그는 ANSI escape 제거 + 마지막 request/response 자동 첨부. fail 케이스에서 "어떤 요청에 어떤 응답이었는지"를 traceback과 함께 한 detail에 모음.
- **운영 정합성 (변경 없음)**: 이전 세션의 P1 "vLLM Qwen 본체 :7080 이전"은 본 세션에서 진행하지 않음. 다음 :5016 게이트웨이 재기동 전에 vLLM 본체를 :7080으로 옮겨야 정합.

#### 검증
- `bash -n` PASS: `aws/user.sh`, `aws/entrypoint-llm.sh`, `aws/setup-ec2.sh`, `llm-serving/vllm/start.sh`
- yaml `safe_load` PASS: `aws/docker-compose.yml`
- `Dockerfile.llm` `EXPOSE`: 5555(SSH) 단독 (5500 잔재 0건)
- code-server 키워드 grep: 코드/문서/스크립트 0건
- `user.sh --root` 분기 트레이싱: `extra_start`/`extra_end`/`ssh_port` 잔재 변수 → 사용 경로 없음(참고 등급)
- work-verify (스킬 + 부에이전트) 2회: PASS, 심각/주의 등급 0건, 참고 3건 (SETUP_GUIDE §3-1 trailing space, user.sh dead variable, password 표기 일관성)

#### 교훈 (영구 기록)
- **`shell source` 시 main 가드 확인** (`lessons_shell_source_main_guard.md`): 가드 없는 스크립트를 source하면 main까지 실행됨. 운영 영향 가능 명령(서비스 기동/재기동) 시뮬레이션 시 사전에 가드 유무 확인 필수.

#### 커밋
| 해시 | 메시지 |
|------|--------|
| `ea17ea6` | update (aws README→SETUP_GUIDE 분리 + VLLM_OPS_GUIDE 운영 모델 표기 + 디스커버리 fail-fast 정합) |
| `3c8ac32` | update (code-server / vsix 인프라 전면 제거 — 폐쇄망 정책, +`user.sh --gpus none` 동선) |
| `8df843a` | update (start.sh `[name]` instances/↔gateways/ 자동 라우팅 + test 디버그 정밀화 1차) |
| `7cc29b0` | update (test_vllm_server Tee 로거 + 마지막 request/response 자동 첨부 + DEPLOY/OPS 가이드 보강) |

#### 현재 상태
- code-server / vsix 제거 + work-verify PASS (3건 참고 등급은 운영 영향 없음)
- aws 진입점 README/SETUP_GUIDE 분리 + Phase 2 디스커버리 구조 운영 문서 정합 완료
- vLLM 테스트 디버깅 가독성 개선 완료 (Tee + request/response 자동 첨부)
- 다음: Qwen 본체 `:7080` 이전(이전 세션 P1 미해결 그대로) → SGLang 골격 / STT 첫 기동

---

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
| `llm-serving/vllm/vllm_gateway.py` | 수정 | `_discover_backends()` 추가, `load_config` 우선순위 재정의: backends → discover_from (둘 다 미설정 시 fail-fast). vLLM port 중복 검증, `gateway.port` 누락 ValueError. 잔재 1세대 fallback(vllm_config + backend_count)은 dead code로 확정되어 동일 세션에서 제거 |
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
- **escape hatch 유지**: 게이트웨이 yaml에 `backends:` 명시 시 그쪽이 우선 (이질 라우팅 / 디버깅). `discover_from`도 미설정이면 `load_config`가 즉시 ValueError. 옛 1세대 fallback(`vllm_config + backend_count`)은 archive 후 dead code로 확정되어 제거 — 새 yaml 어디에도 해당 키가 없어 호환 의미가 없었음.
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
