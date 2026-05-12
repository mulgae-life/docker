---
name: session
description: docker 레포 현재 상태. 세션 시작 시 다음 작업과 최근 변경 파악용.
last-updated: 2026-05-12 (STT 한국어 정확도 의사결정 + yaml 통일 + 옵션 A 리팩토링)
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
| P1 | **5015 Gemma 운영 프로파일 확정**: 현재 라이브 게이트웨이는 `max_inflight_requests=20`, `max_queue_size=20`으로 동작. 로컬 `gateways/5015.yaml`은 단일 인스턴스 안전값 `2/18`에 운영 후보 `20/40` 주석이 붙어 있으므로, 재시작 전 실제 GPU/`max_num_seqs` 기준으로 값 확정 필요. | Todo |
| P1 | **5015 Gemma 장문 트래픽 검증**: 단문 smoke/동시 20/24 요청은 통과. 운영 전 `max_tokens >= 512` 또는 실제 평균 프롬프트/출력 길이로 latency, queue timeout, 429 비율 확인 필요. | Todo |
| P1 | **vLLM Qwen 본체 :7080 이전**: `instances/qwen.yaml`의 `port: 7080`이 의도된 다음 운영 포트. 게이트웨이 :5016은 메모리상 :7071 보유 중이라 즉시 영향 없으나, 다음 게이트웨이 재기동 시 yaml 기준(:7080)으로 디스커버리하므로 그 시점 전에 vLLM 본체를 :7080으로 옮겨야 정합. | Todo |
| P1 | **Gemma 4 31B / Qwen 3.6 27B MTP 실기동 검증**: yaml/런처 준비 완료(2026-05-12). 진입 전 ① GPU 0(prd-gemma 26B-A4B)·GPU 1(qwen 35B-A3B) prd 인스턴스 down ② vLLM 0.19.0+ 확인(`gemma4_assistant` model_type 인식) ③ 외부망 가능 환경에서 첫 기동(런처가 `gemma-4-31B-it-assistant` 자동 다운로드) ④ acceptance/TPOT/throughput 사내 벤치 — `slm_research/mtp.md` §5 워크로드별 권장 참고. 기존 운영(26B-A4B / 35B-A3B-FP8) 교체 여부 별도 결정. | Todo |
| P1 | `llm-serving/sglang/` 디렉토리 골격 (운영 가이드 + 런처 + 설정 + 테스트) | Todo |
| P1 | **`llm-serving/stt/` 한국어 정성 비교 (PoC 잔여)**: 2026-05-12 검증으로 후보·의사결정 명확화 — "정확도 우선 + offline" 시나리오 E(MODEL_STUDY §5.5) 채택. 1순위 Whisper-large-v3 base(rtzr 벤치 CER 11.34%) → 부족 시 한국어 fine-tune 트랙(ENERZAi 사례 CER ~6.45%). Voxtral은 실시간 시나리오 B 격리. `test_stt.py`(WER/RTF/latency/정성평가) 작성으로 실측 확정. | Todo (의사결정 ✅, 실측 대기) |
| P2 | **STT 동시 N 세션 운영 전환 (현재 단일 PoC)**: `instances/voxtral.yaml`의 `gpu_memory_utilization 0.35 → 0.40~0.50`, `max_num_seqs 1 → 2~4`, `gateways/5017.yaml`의 `max_inflight_requests/max_queue_size` 동기 상향. 동시 stream 목표 결정 후 진행. | Todo |
| P2 | **운영계 컨테이너에 STT 의존성 반영 + 모델 동기화**: `aws/requirements.txt`에 추가된 `soundfile/soxr/librosa` 가 운영계 컨테이너 빌드에 반영되도록 재배포. Voxtral 17GB는 외부망 PC → S3 → `/models/STT/` 사전 동기화 (폐쇄망 대비). | Todo |
| P2 | **RTX PRO 6000 Blackwell 운영 이전 후 fused MoE 튜닝**: `benchmark_moe.py`로 `E=128,N=352,device_name=NVIDIA_RTX_PRO_6000_Blackwell_Workstation_Edition,dtype=fp8_w8a8.json` 생성 → site-packages `vllm/model_executor/layers/fused_moe/configs/`에 배치 → 가능하면 vLLM 본가 PR. 트리거: 운영 환경 셋업 완료 시점 | Todo |
| P2 | **운영 가이드 wrapper / logging.sh 보강 (옵션 A 후속)**: 2026-05-12 옵션 A 리팩토링으로 `stt/start.sh`·`stt/logging.sh`가 `../vllm/` 본체를 호출하는 thin wrapper가 됨. `STT_OPS_GUIDE.md` / `VLLM_OPS_GUIDE.md` 양쪽에 (1) 본체/wrapper 구조 한 줄 (2) `logging.sh` 운영 명령(S3 호출 카운트 sync) 섹션 추가. | Todo |
| P3 | `agent-guide/` MCP 도구 섹션 채우기 (필요 시) | Todo |

---

## 기타 이슈

- 5015 라이브 런타임 overload 값(`20/20`)과 로컬 `gateways/5015.yaml` 값(`2/18`, 운영 후보 주석 `20/40`)이 다름. 게이트웨이 재시작 전 운영 목표값 확정 필요.

---

## 최근 세션

### 2026-05-12 (STT 한국어 정확도 의사결정 + STT yaml 통일 + 옵션 A 리팩토링)

#### 세션 목표
- 한국어 STT 의사결정용 본문 검증: Voxtral-Mini-4B-Realtime vs Whisper-large-v3, "정확도 우선 + offline" 조건에서 어느 게 우수한지 + Whisper의 산업 표준 baseline 위상 + 한국어에서 Whisper 능가를 공개한 모델 정리.
- 운영 결과를 `stt/MODEL_STUDY.md` 신규 §4.5 / §5.5 / §7 / §8로 보존.
- STT instances/ yaml 3종 구조·주석 통일 (운영 노하우 동기화, 모델별 값만 차이).
- `vllm/` ↔ `stt/` 의 `start.sh` / `logging.sh` 약 90% 중복 제거 — 옵션 A(env-driven 본체 + thin wrapper) 채택.

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `llm-serving/stt/MODEL_STUDY.md` | 대규모 추가 (+122줄) | §4.5 신규: WER vs CER 정의, rtzr 한국어 STT 벤치(Whisper 11.34% vs VITO 6.77% vs ClovaSpeech 7.96%), 한국어 fine-tune 사례(ENERZAi KsponSpeech 6.45%), Qwen3-ASR-1.7B(FLEURS Korean CER 2.57) / Voxtral(FLEURS Korean WER 12.29 offline / 14.30 동률·2400ms) 한국어 수치, Voxtral Transcribe V2 공개 가중치 부재 확인, Whisper-large-v3 산업 표준 baseline 위치(MLPerf v5.1). §5.5 신규 시나리오 E "정확도 우선 + offline" — 1순위 Whisper + 한국어 fine-tune, 2순위 Whisper base, 3순위 Qwen3-ASR-1.7B. §7 Sources 본문 검증 자료 8건 추가. §8 변경 이력 entry. (커밋: `831a9a7 study`) |
| `llm-serving/stt/instances/voxtral.yaml` | 마스터로 재작성 (117→143줄) | 헤더/메타/모델/env/task/server/GPU·메모리/추론/컴파일/로깅 9개 섹션 구조 확립. 모든 운영 노하우 주석에 `voxtral / qwen3_asr / whisper_v3` 3행 비교표 동일하게 명시. |
| `llm-serving/stt/instances/qwen3_asr.yaml` | 구조 통일 (66→139줄) | voxtral과 동일 키 순서·동일 주석. 모델별 값만 차이 (model/task/gpus/port/gpu_memory_utilization=0.50/max_num_seqs=8/max_model_len=8192). 비교 PoC라 `gateway_port` / `env` / `compilation_config` 주석 처리 (구조는 유지). |
| `llm-serving/stt/instances/whisper_v3.yaml` | 구조 통일 (54→140줄) | qwen3_asr과 동일 접근. `gpu_memory_utilization=0.20`, `max_num_seqs=5`, `max_model_len` 주석처리(모델 config 자동 감지). |
| `llm-serving/vllm/start.sh` | env-driven 일반화 (+4줄) | `INSTANCES_DIR / GATEWAYS_DIR / LOG_DIR / CLUSTER_LABEL` 을 env로 받음 (`${VAR:-default}` 패턴). 미설정 시 자기 디렉토리/`vLLM` 라벨로 동작 — 기존 동작 100% 동일. 헤더 echo 3곳 `"$CLUSTER_LABEL 클러스터"`로 변경. launcher/gateway 호출은 항상 `$SCRIPT_DIR` 기준 → 본체 단일 출처. |
| `llm-serving/vllm/logging.sh` | env-driven 일반화 (+4줄) | `WORK_DIR / INST_DEFAULT / S3_PREFIX`를 env로 받음. 미설정 시 vllm/·`prd-gemma`·`logs/vllm` 기본값. |
| `llm-serving/stt/start.sh` | thin wrapper로 교체 (534→26줄) | `HERE` 산출 후 `CLUSTER_LABEL=STT`·`INSTANCES_DIR/GATEWAYS_DIR/LOG_DIR` export → `exec bash ../vllm/start.sh "$@"`. 기존 운영 명령(`./stt/start.sh up voxtral` 등) 사용법 100% 동일. |
| `llm-serving/stt/logging.sh` | thin wrapper로 교체 (147→30줄) | `WORK_DIR=$HERE`·`INST_DEFAULT=voxtral`·`S3_PREFIX=logs/stt` export → `exec bash ../vllm/logging.sh "$@"`. STT 호출 통계가 `s3://hgi-ai-res/logs/stt/<inst>/` 로 LLM(`logs/vllm/`)과 분리. |
| `llm-serving/stt/README.md` | 표현 정정 (L37) | "start.sh만 STT 변종으로 분리" → "start.sh/logging.sh/launcher/gateway 모두 ../vllm/ 본체를 재사용. stt/start.sh, stt/logging.sh는 env export 후 exec 호출하는 thin wrapper". |

#### 결정 사항
- **시나리오 E "정확도 우선 + offline" 채택**: 회의록·인터뷰 등 batch 변환용. 1순위 Whisper-large-v3 + 한국어 fine-tune, 2순위 Whisper base 즉시 도입, 3순위 Qwen3-ASR-1.7B. Voxtral은 시나리오 B(실시간) 격리 — 480ms 권장 설정에서 Whisper 대비 한국어 WER ~1.44%p 열등.
- **수치 해석 — WER ≠ CER**: 같은 FLEURS Korean 데이터셋이라도 Voxtral 논문 Whisper WER 14.30% vs Qwen3-ASR 논문 Whisper-large-v3 CER 2.07%. metric 차이로 직접 비교 불가. 한국어는 공식적으로 CER 권고(Whisper Discussion #1762). 자체 측정이 가장 신뢰 가능 — `test_stt.py` 작성으로 확정 필요.
- **옵션 A 리팩토링 (env-driven 본체 + thin wrapper)**: 옵션 B(`_lib/` 공통 디렉토리)·옵션 C(symlink)·옵션 D(현재 유지) 비교 후 채택. 근거: (1) `vllm`이 기본이라는 사용자 멘탈 모델 일치 (2) launcher/gateway가 이미 동일 패턴 (3) wrapper 26줄로 진입점 보존 (4) sglang 추가 시 동일 패턴 그대로. 결과 — 1,351줄 → 734줄 (-617, -45%).
- **yaml 통일 = 구조 + 주석 동일화** (`feedback_preserve_operational_comments` 재확인): voxtral 마스터로 통일하되 비교 PoC는 `gateway_port`/`env`/`compilation_config`를 주석 처리하여 키 구조 유지. 라인 수 143/139/140 (차이 ≤4줄, vllm gemma/qwen 56줄 차이 패턴 유사).
- **stt/logging.sh 신규 도입**: 5/8 vllm/logging.sh 추가(015e9e4) 이후 stt에 미반영 갭 발견 → 옵션 A 진행 전 단계로 먼저 신규 작성, 옵션 A로 wrapper화. `POST /v1/audio/transcriptions`도 `POST /v1/` 필터에 매칭 (실증 80건 INFO MM-DD + 6건 transcription POST).

#### 검증
| 항목 | 결과 |
|------|------|
| bash syntax 4종 (vllm/stt × start/logging) | PASS |
| YAML 문법 3종 (voxtral/qwen3_asr/whisper_v3) | `yaml.safe_load` PASS |
| 라이브 회귀: `vllm/start.sh status` | gemma(PID 876058) + gateway 5015/5016 UP 유지 |
| 라이브 회귀: `stt/start.sh status` (wrapper) | "═══ STT 클러스터 상태 ═══" 라벨 + voxtral의 gateway_port=5017 → gw :5017 매칭 정확 |
| cwd 독립성: `/tmp`에서 stt wrapper 호출 | `HERE=$(cd $(dirname $BASH_SOURCE[0]) && pwd)`로 stt/ 절대경로 정확 산출 |
| yaml 키 동등성 (Python set 비교) | 공통 14개 + voxtral 단독 3개(gateway_port·env·compilation_config) + qwen3_asr/whisper_v3 단독 1개(task) — 의도된 차이 |
| work-verify 3회 (1차 logging.sh 후 / 2차 yaml+옵션 A 후 / 3차 README 정정 후) | 모두 PASS — 참고 등급만 발견, 심각/주의 0건 |

#### 교훈 (영구 기록 후보)
- **vllm↔stt 양쪽 동시 갱신 부담은 코드 단일 출처로 해소**: d58daee(2026-05-07) 처럼 양쪽 90줄씩 동기 변경하던 패턴이 본 리팩토링으로 사라짐. 향후 sglang 추가 시 26줄 wrapper만 작성하면 진입점 완성.
- **검색 요약 ≠ 모델카드/논문 본문**: 검색 요약은 "Voxtral macro-avg 5.9% vs Whisper 7.4%"를 자주 인용하지만 이는 13개 언어 평균이지 한국어 단독이 아님. 실제 한국어 WER은 Whisper가 동률(2400ms) 또는 우위(480ms). 모델카드/논문 표(Voxtral 논문 Table 7, Qwen3-ASR 논문 Table A.2)까지 본문 fetch가 필수. (`feedback_model_card_verification` 재적용 성공 사례)

#### 현재 상태
- STT yaml 3종 통일 ✅, 옵션 A 리팩토링 ✅, MODEL_STUDY §4.5/§5.5 ✅ (커밋 완료 `831a9a7`)
- 미커밋 변경 (본 세션 후속): stt/{instances yaml 3종, start.sh, logging.sh, README.md} + vllm/{start.sh, logging.sh}
- 다음 작업 후보: (a) 운영 가이드 wrapper/logging.sh 보강 (P2 신규) (b) `test_stt.py` 작성 + 한국어 실측 (P1) (c) sglang 디렉토리 골격 (옵션 A 패턴 그대로 적용)

---

### 2026-05-12 (Gemma 4 31B / Qwen 3.6 27B MTP 도입 + 런처 drafter 자동 다운로드)

#### 세션 목표
- 2026-05-05 공개된 Gemma 4 external drafter MTP와 Qwen 3.6 native MTP 비교 정리 (`slm_research/mtp.md`, 70f7b57 커밋 분량 포함).
- 운영 yaml(`instances/gemma.yaml`, `instances/qwen.yaml`)에 MTP 설정 반영 — GPU 0/1 각 1장 L40S 단일 배치.
- 런처가 `speculative_config.model` (external drafter)도 메인 모델처럼 없으면 자동 다운로드하도록 일반화.

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `llm-serving/vllm/vllm_server_launcher.py` | 기능 추가 | `_resolve_model_path(model_id, download_dir, *, kind)` helper 추출 (download_model 다음 위치). `main()`의 모델 경로 해석 블록을 helper 호출로 교체 + `speculative_config.model` 분기 신설 (dict in-place 절대경로 치환 → `_write_vllm_config`의 yaml.dump로 자식 vLLM에 전달, OFFLINE 환경에서도 로컬 로드). 절대경로 sys.exit·메시지·로그 텍스트 동치성 보존. |
| `llm-serving/vllm/instances/gemma.yaml` | 모델 교체 + MTP | 26B-A4B-it FP8 → **31B-it FP8** (GPU 0). `max_model_len 65536 → 8192` (31B FP8 ~29GB + drafter 0.5B BF16 → KV 여유 ~12GB). 신규 섹션 `Speculative Decoding (MTP)` — external drafter `google/gemma-4-31B-it-assistant` + `num_speculative_tokens: 4`. 헤더 모델명 갱신. |
| `llm-serving/vllm/instances/qwen.yaml` | 모델 교체 + MTP | 35B-A3B-FP8 → **27B-FP8** (GPU 1, Dense). `max_model_len` 보수 조정. 신규 섹션 `Speculative Decoding (MTP)` — native MTP `method: qwen3_next_mtp` + `num_speculative_tokens: 1` (drafter 모델 없음, 메인 모델 자기 자신의 sequential MTP head). 헤더 모델명 갱신. |

#### 결정 사항
- **drafter 자동 다운로드 위치**: 런처에 `_resolve_model_path` helper로 추출 — 호출 2회(메인 model + spec_cfg.model)라 추상화 정당. 향후 EAGLE/Medusa 같은 추가 external drafter 키에도 재사용 가능. inline 반복 대비 diff 약간 증가지만 DRY가 우선.
- **OFFLINE 환경변수 의미 재정리**: `HF_HUB_OFFLINE=1`은 "오프라인용 모델 다운로드"가 아니라 "Hub 호출 금지(로컬 캐시만)". 런처는 자신이 다운로드할 때만 일시 해제(`os.environ.pop`)하고, vLLM subprocess env에는 강제 주입 — drafter도 사전에 절대경로로 받아둬야 OFFLINE 자식이 정상 로드. dict의 model 키를 in-place 절대경로 치환하는 방식 채택.
- **31B + drafter 단일 L40S 메모리**: BF16 31B 62GB는 단일 GPU 불가 → FP8 on-the-fly 양자화로 ~29GB. drafter 0.5B BF16 추가 ~1GB. `gpu_memory_utilization=0.85` 기준 KV ~12GB → `max_model_len 8192` 보수 시작값. recipes 권장 `num_speculative_tokens 4–8` 중 하단 채택.
- **31B-it-assistant 라이선스**: drafter는 Apache 2.0 (메인 Gemma 라이선스와 별개) — HF 모델 카드 4종 직접 fetch로 검증.

#### 검증
| 항목 | 결과 |
|------|------|
| `yaml.safe_load`로 gemma/qwen yaml 파싱 | 정상, `speculative_config` dict 정상 매핑 |
| `python ast.parse(launcher)` | syntax OK |
| `_resolve_model_path` dry-run 5분기 | 메인(있음) skip / drafter(없음) → `download_model` 호출 / Qwen 27B(있음) skip / 빈 값 / `download_dir` 없음 — 모두 의도대로 |
| `speculative_config.method`만 있는 qwen.yaml | `spec_cfg.get("model")` falsy → 분기 자동 스킵 (회귀 없음) |
| `instances/prd-gemma.yaml`처럼 spec_config 자체 없는 인스턴스 | `isinstance(None, dict)` False → 스킵 (회귀 없음) |

#### 현재 상태
- yaml 변경 + 런처 패치 완료. **아직 실 기동 안 함** — 사전 작업 필요: ① GPU 0/1 점유 prd 인스턴스 down ② vLLM 0.19.0+ 확인 (`gemma4_assistant` model_type 인식) ③ 외부망 접근 가능한 환경에서 첫 기동 (런처가 drafter 자동 받음, HF 캐시 후 OFFLINE에서도 정상).

---

### 2026-05-04 (STT Voxtral 페어 구조 도입 + 게이트웨이 5017 + 가이드 작성)

#### 세션 목표
- vLLM의 `instances/+gateways/` 페어 패턴을 STT에도 도입하여 운영 표면 단일화 (외부 노출 :5017 게이트웨이 + 내부 :7172 인스턴스).
- `vllm_gateway.py` 본체에 `/v1/audio/transcriptions` (POST) + `/v1/realtime` (WS) 라우트 추가 — STT 게이트웨이는 별도 코드 없이 본체 재사용.
- Voxtral-Mini-4B-Realtime-2602 메모리 핏 (단일 세션 PoC 0.35 + max_num_seqs=1).
- 사용자/운영자 분리 가이드 2종(`STT_API_GUIDE.md`, `STT_OPS_GUIDE.md`) 작성 + 외부 참조 일괄 갱신.

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `llm-serving/vllm/vllm_gateway.py` | 기능 추가 | `POST /v1/audio/transcriptions` (multipart proxy, timeout 600s, admission/LB 적용) + `WS /v1/realtime` (양방향 frame relay, close code 4429/4503/4500 매핑). websockets/fastapi WebSocket import 추가 |
| `llm-serving/vllm/vllm_server_launcher.py` | 동작 변경 | `_LAUNCHER_KEYS`에 `env` 추가 + yaml의 env dict를 subprocess 환경에 머지. `RUNTIME_DIR`을 yaml dirname 기준으로 동적 결정 (STT/LLM runtime 격리) |
| `llm-serving/stt/start.sh` | 풀 도입 | vllm/start.sh 패턴 그대로 복사 후 launcher/gateway 경로만 `../vllm/`으로 변경 + 헤더/사용법 STT 라벨링 |
| `llm-serving/stt/instances/voxtral.yaml` | 신규 | Voxtral 인스턴스 (gateway_port 5017, GPU 2, 내부 :7172, 메모리 핏 0.35 + max_num_seqs=1, env: VLLM_DISABLE_COMPILE_CACHE=1, compilation_config: PIECEWISE) |
| `llm-serving/stt/instances/{qwen3_asr,whisper_v3}.yaml` | 위치 이동 | 구 `stt/configs/` → `stt/instances/` (mv) |
| `llm-serving/stt/gateways/5017.yaml` | 신규 | STT 게이트웨이 (warmup 비활성화, audio timeout 600s, max_inflight=1) |
| `llm-serving/STT_API_GUIDE.md` | 신규 | 사용자용 §1~§5 (한눈에 보기·첫 호출·핵심 기능·파라미터·통합 예제). Voxtral verbose_json 미지원 캐비어트 4곳 |
| `llm-serving/STT_OPS_GUIDE.md` | 신규 | 운영자용 §6~§12 (시스템 구조·기동·모델 관리·설정 표·트러블슈팅·QA·참고). 메모리 표 실측 반영 (0.35 = KV 4.24 GiB / 2,160 token / max concurrency 1.05x) |
| `llm-serving/README.md` / `llm-serving/DEPLOY_GUIDE.md` | 갱신 | STT 진입점/디렉토리/기동 명령 추가 |
| `llm-serving/stt/README.md` | 재작성 | 페어 구조/단일 인스턴스 옵션/의존성 안내 갱신 |
| `llm-serving/stt/MODEL_STUDY.md` | 갱신 | §6.2 PoC 절차 — Voxtral 운영 반영. §6.3 디렉토리 — instances/+gateways/. §8 변경 이력 항목 추가 |
| `agent-guide/PROJECT.md` / `README.md` | 갱신 | 트리/핵심 파일/빠른 시작/상세 참조에 STT 추가 |
| `aws/requirements.txt` | 신규 라인 | `soundfile/soxr/librosa` 추가 (운영계 재배포 시 ImportError 방지) |

#### 검증 (라이브)
| 항목 | 결과 |
|------|------|
| `GET /health` (게이트웨이 5017) | 200, `{"status":"ok","ready":1,"total":1}` |
| `GET /v1/models` | `Voxtral-Mini-4B-Realtime-2602` (max_model_len 32768) |
| `POST /v1/audio/transcriptions` (1초 사인파) | 200, RTT 평균 372ms, `{"text":"","usage":{"type":"duration","seconds":1}}` |
| `WS /v1/realtime` | session.created 이벤트 수신, 게이트웨이 logs 정상 (`realtime 프록시 시작 → 종료`) |
| Runtime 격리 | `stt/instances/.runtime/voxtral.json` ↔ `vllm/instances/.runtime/gemma.json` 분리 유지 |
| `server-status` overload | accepted=7, rejected=0, queue_timeout=0 |

#### work-verify 발견 사항 (1차) → 모두 즉시 수정
1. `stt/start.sh` 출력 헤더 "vLLM 클러스터" 잔존 → "STT 클러스터" 일괄 교체 (3곳).
2. `STT_API_GUIDE.md`에서 Voxtral의 verbose_json 지원으로 잘못 안내 → 4곳 캐비어트 추가 (실제 400 BadRequestError 확인).
3. `STT_API_GUIDE.md` LangChain `langchain_community.tools` (alias) → `langchain_core.tools` (정식) 교체.

#### work-verify 2차 (회귀)
모든 1차 수정 적용 후 라이브 RTT 372ms (370~374ms 변동), backend healthy 1/1, overload 정상. 추가 발견 없음. **이상 없음 — 운영 가능.**

#### 다음 작업 후보
- 한국어 정성 비교 (Voxtral vs Qwen3-ASR vs Whisper-large-v3, `test_stt.py` 작성).
- 운영계 컨테이너 재배포 (requirements.txt 반영) + 폐쇄망 모델 사전 동기화.
- 동시 N 세션 운영 전환 (메모리 핏 + max_num_seqs 동기 상향).

---

### 2026-05-04 (vLLM 5015 안정성/트래픽 테스트)

#### 세션 목표
- 5015 Gemma 게이트웨이를 실제 운영 트래픽 기준으로 보호할 수 있는지 점검
- 과부하 시 서버가 내려가지 않도록 동시 처리량 제한, 대기열, 429 방어 응답을 구성
- 운영 전 사용할 보수적 트래픽 테스트 스크립트와 라이브 검증 결과를 확보

#### 변경 파일
| 파일 | 변경 유형 | 요약 |
|------|----------|------|
| `llm-serving/vllm/vllm_gateway.py` | 기능 보강 | `AdmissionController` 추가. `/v1/chat/completions` 앞단에서 동시 처리 슬롯과 대기열을 제한하고, 대기열 포화/시간 초과 시 429 + `Retry-After` 반환. `/server-status`에 overload 스냅샷 포함 |
| `llm-serving/vllm/gateways/5015.yaml` | 설정 보강 | `overload` 설정과 변수 설명 정리. 현재 로컬 값은 단일 인스턴스 안전값 `max_inflight_requests=2`, `max_queue_size=18`; 운영 `max_num_seqs=20` 전환 시 `20/40` 후보 주석 보존 |
| `llm-serving/vllm/gateways/5016.yaml` | 설정 보강 | 5016 게이트웨이에도 동일한 `overload` 설정 구조와 설명 추가 |
| `llm-serving/vllm/traffic_test_vllm.py` | 테스트 보강 | `smoke`/`overload` 모드, 429 허용 판정, 사후 `/health`·`/server-status` 생존 확인, 통과 기준, 리포트 저장 추가 |
| `llm-serving/vllm/test_vllm_server.py` | 문서 보강 | 테스트 단계 설명 docstring 추가 |
| `.archive/2026-05-04_vllm-5015-traffic-test/logs/` | 산출물 보존 | 라이브 트래픽 테스트 리포트 3건 보존 |
| `README.md`, `llm-serving/README.md`, `agent-guide/PROJECT.md`, `llm-serving/VLLM_OPS_GUIDE.md`, `agent-guide/SESSION.md` | 문서 갱신 | 운영 가이드 경로 정정, 과부하 차단/트래픽 테스트 설명, 본 세션 로그 반영 |

#### 결정 사항
- 게이트웨이의 `max_inflight_requests`는 vLLM의 `max_num_seqs` 이하로 맞춘다. 초과 요청은 `max_queue_size`만큼 게이트웨이에서 대기하고, 대기열 포화 또는 `queue_timeout_seconds` 초과 시에만 429로 차단한다.
- 단일 5015 인스턴스(`gemma.yaml` 현재 `max_num_seqs=2`)는 `2 inflight + 18 queued = 총 20명 수용`이 안전 기준이다.
- 실제 운영에서 GPU를 늘리고 `gemma.yaml max_num_seqs=20`을 검증한 뒤에는 `5015.yaml`을 `max_inflight_requests=20`, `max_queue_size=40`, `queue_timeout_seconds=180`, `retry_after_seconds=10` 기준으로 올리는 구성이 적합하다.
- `traffic_test_vllm.py --mode overload`에서는 HTTP 429를 실패가 아니라 방어 응답으로 집계한다. 단, `smoke` 모드에서는 429를 실패로 본다.
- 작업 중 `work-verify`로 "보강 완료" 선언과 실제 코드 반영 불일치를 발견했고, 이후 `traffic_test_vllm.py`에 실제 overload 판정/사후 점검/통과 기준을 반영했다.

#### 라이브 테스트 결과
| 대상 | 조건 | 결과 | 주요 지표 |
|------|------|------|----------|
| `http://3.38.195.121:5015` | 10 요청 / 동시 2 / `max_tokens=64` | 10/10 성공 | p95 889ms, 에러 0% |
| `http://3.38.195.121:5015` | 20 요청 / 동시 20 / `max_tokens=32` | 20/20 성공 | p95 4.36s, 에러 0% |
| `http://3.38.195.121:5015` | 24 요청 / 동시 24 / `max_tokens=16` | 24/24 성공 | p95 3.09s, 에러 0% |

#### 현재 상태
- 라이브 5015 최종 상태: `/health=200`, ready `1/1`, `active_connections=0`, `queued_requests=0`, `rejected_total=0`, `queue_timeout_total=0`.
- 현재 라이브 게이트웨이 overload 값은 `max_inflight_requests=20`, `max_queue_size=20`, `queue_timeout_seconds=60`, `retry_after_seconds=5`.
- 로컬 `gateways/5015.yaml` 값과 라이브 런타임 값이 다르므로, 다음 게이트웨이 재시작 전에 운영 목표값을 확정해야 한다.
- 잔존 검증: 장문 출력(`max_tokens >= 512`)과 실제 서비스 프롬프트 길이 기준 트래픽 테스트.

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
