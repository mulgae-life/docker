---
name: session
description: docker 레포 현재 상태. 세션 시작 시 다음 작업과 최근 변경 파악용.
last-updated: 2026-07-22 (가이드 문서 정합 일괄 갱신 — 26B 전면 표기 + 2-모드 재서술)
---

# 세션 상태

> 세션 시작 시 현재 상태를 빠르게 파악하기 위한 문서. 갱신은 세션 종료 시. 과거 세션 상세가 필요하면 `git log`와 해당 커밋 diff를 참조.

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
| P1 | **:5015 운영 프로파일 (26B 기준)**: 2026-07-21부터 :5015 = 비PII 직접 게이트웨이 + gemma-26b(fp8·TP2·gmu 0.9·max_len 32768, overload 20/40). 잔존: 장문 트래픽 기준 latency·429 비율 측정. | 갱신(모델 교체), 장문 검증 잔존 |
| P1 | **MTP 실기동 검증 (31B/26B/Qwen)**: 31B·Qwen 27B(5/13) + 26B-A4B(7/21, QA 통과) 실기동 확인. 잔존: ① Qwen 5016 재기동·가용성 ② acceptance/TPOT 사내 벤치(`slm_research/mtp.md` §5 참고). | 부분 완료, 벤치 잔존 |
| P1 | **모델 간 속도 매트릭스**: `tests/speed_test.py --base-url`. 26B-A4B 6행 확보(c=1 TPS 168.5 / c=10 TPS 81 — 31B quick 64.8 대비 단발 약 2.6배). 잔존: 31B·Qwen 풀 매트릭스로 3모델 비교 완성. | 부분 완료(26B 측정) |
| P1 | `llm-serving/sglang/` 디렉토리 골격 (운영 가이드 + 런처 + 설정 + 테스트) | Todo |
| P1 | **STT 한국어 정성 비교 (PoC 잔여)**: 시나리오 E(정확도+offline) 채택 완료 — 1순위 Whisper-large-v3(+한국어 fine-tune 트랙). `test_stt.py`(WER/RTF/정성) 작성으로 실측 확정. | 의사결정 ✅, 실측 대기 |
| P2 | **26B-A4B MTP 튜닝**: acceptance 32~47%(31B 70~85% 대비 낮음 — MoE 저동시성 특성). 동시성 4+ 실측 후 낮으면 `num_speculative_tokens 4→2` 또는 MTP off A/B. | 신규 (2026-07-21) |
| P2 | **PII NER 후속 고도화 (대표님 지시로 보류)**: 현재 연구·운영 모두 PII 미사용(비PII vllm만 운영). 재사용 전 필수 — ① **🚨 512 토큰 초과 청킹**(현재 긴 텍스트 500 에러 → fail-open 무검사 통과/fail-closed 차단. overlap 청킹 + 회귀 테스트) ② 마이크로 배칭 ③ replica 스케일아웃(`configs/ner.yaml` backends 복제, LB 기지원). 동시성 1차(스레드풀+세마포어)는 7/21 완료. | 신규 (후속 대기) |
| P2 | **PII 가드 운영 적용 후속 (보류)**: 잔존 — 운영계 :5501 실기동(설정 준비 완료) · 보험 실데이터 recall 게이트 실측(`recall_gate.py` 하버스 준비, 라벨 JSONL 대기) · 스트리밍 progressive buffer(별도 합의) · 이미지 OCR PII · 배포 시 `PII_AUDIT_SALT` 확인. 설계: `agent-guide/plans/pii-dlp-gateway.md`. | 보류(PII 미사용 중) |
| P2 | **비PII qwen 대칭 보강**: qwen은 PII 경유(6502)만 존재. 필요 시 인스턴스(gw 5502) + `gateways/5502.yaml`(0.0.0.0) 추가로 gemma와 대칭. **대표님 답변 대기.** | Todo |
| P2 | **`prd-gemma`(비PII) vLLM host 정리**: `host: 0.0.0.0`이라 vLLM :7070 외부 노출(`prd-pii-gemma`는 127.0.0.1). 방화벽 7070 차단 확인 또는 127.0.0.1 통일. | Todo |
| P2 | **STT 동시 N 세션 전환**: voxtral gmu 0.35→0.40~0.50, max_num_seqs 1→2~4 + 게이트웨이 overload 동기 상향. 동시 stream 목표 결정 후. | Todo |
| P2 | **운영계 STT 의존성 반영**: `aws/requirements.txt`의 `soundfile/soxr/librosa` 재배포 + Voxtral 17GB S3 경유 `/models/STT/` 동기화(폐쇄망 대비). | Todo |
| P2 | **RTX PRO 6000 Blackwell 이전 후 fused MoE 튜닝**: `benchmark_moe.py`로 config json 생성 → site-packages 배치. 트리거: 운영 환경 셋업 완료. | Todo |
| P2 | **wrapper/logging.sh 가이드 보강**: STT/VLLM OPS 가이드에 본체/wrapper 구조 + `logging.sh` S3 카운트 sync 섹션. | Todo |
| P3 | `agent-guide/` MCP 도구 섹션 채우기 | Todo |

---

## 기타 이슈

- ~~5015 라이브 overload 값과 로컬 yaml 불일치~~ → 7/13 전 서빙 다운 + 재기동으로 해소(현재 라이브 = yaml 기준). Qwen :7080 이전 건도 같은 이유로 자연 해소.

---

## 최근 세션

### 2026-07-22 (가이드 문서 정합 일괄 갱신 + STT 포트 정정 + 문체 점검)

- **목표**: 7/21 변경(26B 전환·비PII/PII 2-모드·download·ner.yaml)이 미반영된 가이드 문서 전수 정합. 방향은 대표님 확정 — ① API 가이드 모델 표기 26B 전면 교체 ② PII 서술은 2-모드 구조로 재서술.
- **변경**: `VLLM_API_GUIDE.md`(모델명 약 30곳 26B-A4B 교체 — generation_config 샘플링 동일 확인, PII 박스·§3.6~3.7·에러표를 "PII 모드 한정"으로 조건부화) · `VLLM_OPS_GUIDE.md`(헤더·§6.1 구성도·§7.5/7.8/7.9·§9 파일트리·§10.1·§14를 2-모드로 재서술, `prd-pii-*` 파일명 정합, §7.4 LB 예시 소속 불일치 정정) · `DEPLOY_GUIDE.md`(진입점 맵·§3 기동 모드별 분리, download 절차 반영) · `llm-serving/README.md`·`pii/README.md`(PII "선택 모드(현재 미운용)" 강등, `ner.yaml` 등재, NER 512 토큰 한계 알려진 이슈 명기) · `slm_research/mtp.md` §5.4 본 환경 실측 추가(acceptance 31B 70~85% vs 26B 32~47%, c=1 TPS 약 2.4배).
- **상태**: 완료. 검증 — API 가이드 31B 잔존 2곳(의도된 안내문), `prd-qwen` 옛 명칭 잔존 0, "모든 LLM PII 경유" 옛 전제 문구 0.
- **(후속) STT 포트 정정 — P1 "STT 문서 정정" 해소**: 실물 yaml 기준(voxtral=gw **5018** realtime 분리 / qwen3_asr·whisper_v3=gw **5017** model 라우팅, whisper GPU **2**)으로 `STT_OPS_GUIDE`(§6 구성도·§9.1 gmu 0.4/max_len 16384·§9.3 overload 20/40/180·§11 QA 포트) · `STT_API_GUIDE`(:5018 분리, 상시 기동 안내) · `stt/README`(구성 표·GPU 충돌: whisper↔voxtral) · `PROJECT`·`llm-serving/README`·`DEPLOY`·`OPS`·루트 README·`MODEL_STUDY` 현재 상태부 일괄 정정.
- **(후속) 문체 점검**: aws·llm-serving·agent-guide 전 문서 상투어/번역체/압축체 패턴 일괄 검색 — 실질 지적 3건(수동태 직역 1, 수다 사족 1, "(소중함)" 라벨 1) 수정. 조사·계획 기록물(slm_research 상세, pii_model_research, plans/)은 이력 보존 위해 문체 미수정.

### 2026-07-21 (재기동 복구 + Gemma 26B-A4B 전환 + download 명령 신설 + PII NER 개선)

> 6주 공백 후 세션. 7/13 10:57 전 서빙 동시 절단(전원 단절형, 7/20 호스트 재부팅) 확인 → 26B-A4B 전환 라이브까지.

**핵심 변경** (커밋 `c78b259`·`32c1e8a`·`1e3f40c`·`93c31a7` + 미커밋 PII 배치):
- **HF 모델 갱신 대응**: gemma-4 31B/26B 커밋 이력 확인 — 가중치 무변경, `chat_template.jinja`(툴콜·reasoning fix)와 `tokenizer_config.json`만 갱신 → 증분 동기화로 해결(108GB 재다운로드 불필요). 갱신 전 파일 `/models/LLM/google/.archive/2026-07-21_pre-update/` 보존.
- **`./start.sh download [name|all]` 신설**: launcher `--download-only`에 sync 모드 — 로컬이 있어도 HF 최신과 증분 동기화. **`up`은 네트워크 미접근 유지**(폐쇄망 절차: 개방→download→차단→up). STT wrapper 자동 상속.
- **`${model}-assistant` 치환**: drafter가 본체 model을 자동 추종(launcher 문자열 치환). gemma 4종 yaml spec 블록 완전 동일화 — cp 후 본체/drafter 짝 어긋남 구조적 차단. Qwen(native MTP)은 미영향.
- **`gemma-26b.yaml` 신설 + 라이브**: 26B-A4B(MoE 128/8) + MTP drafter 0.4B(`num_speculative_tokens 4` 공식 고정, method 키 금지). fp8·TP2·gmu 0.9·port 7071. `_SCHEMA.txt`에 MoE 함정 4건(저동시성 이득 제한·4-bit 양자화 금지·MXFP4 #39000·DP>1 #38999) 이관.
- **연구계 PII on/off = "모드=모델" 매핑(대표님 확정)**: 비PII 직접 `up gemma-26b`+`up 5015`(26B, gateways/5015.yaml 0.0.0.0) ↔ PII 경유 `up gemma`+`up 6015`+`pii up 5015`(31B). :5015와 GPU0·1 각각 택일.
- **PII NER 개선(미커밋)**: ① `configs/ner.yaml` 신설 — gpu/models_dir/max_concurrency/backends yaml 승격(env `PII_GPU` 우선) ② `ner_server.py` 추론 스레드풀 분리 + 세마포어 — 부하 중에도 `/health` 1ms 응답(기존: 추론 블로킹 → unhealthy 오판 → fail-closed 전체 차단 연쇄 가능).

**발견 이슈**: 🚨 NER 512 토큰 초과 청킹 부재(P1급, 기존 버그 — 긴 텍스트 500 에러). PII 미사용 중이라 후속 보류(위 다음 작업 표 참조).

**검증**: 26B 게이트웨이 :5015 ready 1/1 + `test_vllm_server.py` 9 카테고리 통과(새 chat template + gemma4 파서 궁합 포함) + speed 6행. download/치환/NER 동시성 각각 라이브 검증.

**운영 반영 주의**: `${model}-assistant` 표기 yaml은 신형 `vllm_server_launcher.py`와 **반드시 동반 배포**(구형 런처는 문자 그대로 해석해 실패).

**문서 정합(아침)**: 미기록 커밋 2건 반영 — `d242cc4`(GUIDE 작업 환경 토폴로지 신설), `c18bcbf`(start.sh 무인자 도움말). 6/09 "미커밋" 표기는 실제로는 커밋 완료였음(`de734ce`·`86a4839`).

**현재 상태**: 연구계 gemma-26b(GPU0·1) ← :5015 직접 라이브. PII·STT·qwen 미기동. 미커밋: PII 배치 4파일 + speed_results 5행 + agent-guide 3종.

---

## 과거 세션 (초압축 — 상세는 git log)

| 날짜 | 요약 |
|------|------|
| 2026-06-09 | 전 서빙 yaml 주석 슬림화(인스턴스 433→65줄 등) + 공용 `_SCHEMA.txt` 5종 분리(운영 노하우 이관). PII on/off 토글 구조(운영 5501 비PII/6501 PII 분리)·토폴로지 Q&A(enforcement=포트 인수 이유·LB 위치·bypass 정합). 커밋 `de734ce` |
| 2026-06-05 ③ | NER "연구/운영 공유" 주석 오류 10곳 정정(`3c23160`). 한국어 PII 모델 재조사 — SLM 전환 비권장(인코더 NER F1 96 vs 79), townboy 실측 최고(P 90.5/R 95)로 현 구성 유지, 라이선스 해소(`NOTICE.md`, KPF-BERT MIT + KDPII CC-BY). 리포트 `pii/pii_model_research.md` |
| 2026-06-05 ② | PII 후속 배치: ORG 과탐 헤더 토글·bypass 토큰·우회 차단(점 구분자·account→card Luhn 승격·function_call/input_text 검사)·스트리밍 재작성(fail-closed 503, n>1 보존)·`recall_gate.py` 하버스. 코덱스 리뷰 4회 선별 수용. 테스트 60건, 합성 eval P 100/R 96.6. 커밋 `b1e33a6`·`1270baa`·`eaeecb6` |
| 2026-06-05 ① | PII/DLP 운영 토폴로지 적용: 프록시가 외부 :5015 인수, 게이트웨이 6015 내부 이동(파일명=포트 rename). NER 풀(8911 vmaca/8901 townboy, GPU3) + salt 자동주입. 버그픽스 2건(brn 우선순위 누락·지역번호 regex). 연구계 :5015 enforcement 라이브. 커밋 `295ce8c` |
| 2026-05-27 | `vllm/start.sh logs [name]` 서브커맨드(tail -F 단일 진입점, 기본 all·-n 50). STT wrapper 자동 반영 |
| 2026-05-15 | `aws/user.sh --extra-ports` 콤마 구분 다중 range 지원(라벨 원본 보존으로 rebuild 복원) |
| 2026-05-13 ② | STT 단일 게이트웨이 전환(3 모델 `gateway_port: 5017` 활성, model 필드 라우팅) + `STT_API_GUIDE.md` 사용자 톤 재작성. OPS/README 옛 표기 정정은 P1 잔존 |
| 2026-05-13 ① | Gemma 31B 실기동 성공 — 단일카드 6회 fail 후 TP=2·max_len 32768·gmu 0.95 확정. `tests/` 디렉토리 신설 + `speed_test.py`(8컬럼 누적). 교훈: vLLM 메모리 에러는 startup free check ↔ KV 부족 단계 분리 진단(gmu/max_len 처방 다름) |
| 2026-05-12 ② | STT 한국어 의사결정 — 시나리오 E(정확도+offline): Whisper-large-v3 1순위(+fine-tune 트랙), Voxtral은 실시간 격리. 옵션 A 리팩토링: stt `start.sh`/`logging.sh`를 vllm 본체 thin wrapper화(-617줄). STT yaml 3종 통일 |
| 2026-05-12 ① | MTP 도입 — Gemma 31B external drafter(`-assistant` 0.5B) + Qwen 27B native(`method: mtp`). launcher `_resolve_model_path` drafter 자동 다운로드 |
| 2026-05-04 ② | STT Voxtral 페어 구조(instances/ + gateways/5017) — 게이트웨이 본체에 `audio/transcriptions`·`realtime`(WS) 라우트 추가, STT 가이드 2종 신설. 라이브 검증 통과 |
| 2026-05-04 ① | 게이트웨이 `AdmissionController`(inflight/queue 제한 + 429 Retry-After) + `traffic_test_vllm.py` smoke/overload. 라이브 과부하 24동시 통과 |
| 2026-04-30 ③ | start.sh 운영 견고성(cmdline 정체성 검증·종료 폴링) + launcher fcntl port-alloc 직렬화·atomic runtime write |
| 2026-04-30 ② | code-server/vsix 전면 제거(폐쇄망 정책) · `aws/README→SETUP_GUIDE` 분리 · start.sh `[name]` instances/gateways 자동 라우팅 · test 디버그 가독성(Tee+요청/응답 첨부) |
| 2026-04-30 ① | 게이트웨이 자동 디스커버리(Phase 2: `gateway_port` 매칭 + 포트 자동 회피 + runtime json, backends 수동 명시는 escape hatch) + yaml 통일. 교훈: mv 아카이빙·운영 주석 다이어트 금지 |
| 2026-04-29 ①~③ | 레포 3-디렉토리 재편(`my-docker-server`/`aws`/`llm-serving`) + agent-guide 3종 초기화 · STT 인프라 초안 + `DEPLOY_GUIDE.md` · aws P2 보강 + 내부망 운영 정책(`project_internal_network`) |
