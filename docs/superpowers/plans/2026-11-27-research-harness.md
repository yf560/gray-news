# GRAY Research Harness Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. No agent delegation is required.

**Goal:** 로컬에서 기사를 맡기고, 단계별 조사·근거 검토를 거친 결과를 나중에 내 기록에서 확인한다.
**Architecture:** Python HTTP 서버와 워커 스레드 하나를 사용한다. SQLite에 작업·체크포인트·문서·근거·결과를 저장하고 기존 UI 렌더러에 version:1 결과를 전달한다.
**Tech Stack:** Python 표준 라이브러리, sqlite3, EXAONE API, Tavily API, 기존 HTML/CSS/JavaScript.
**Spec:** `docs/superpowers/specs/2026-11-27-research-harness.md` — 구현 전 반드시 읽는다.

## Global Constraints

- 단일 사용자·단일 프로세스·동시 작업 1개·대기 최대 10개.
- 원문 최대 50,000자, 쟁점 2개, 쟁점당 관점 최대 3개.
- 기본 LLM 3회 + 조건부 보완 1회. 재시도 포함 LLM HTTP 시도 최대 6회.
- 검색 기본 4회 + 보완 2회, 문서 최대 8개, 외부 본문 추출 최대 6개, 입력 URL 추출 별도 1개.
- 작업 경과 상한 15분, 단일 LLM 요청 180초 이하.
- 키·원문·외부 API 응답은 로그에 출력하지 않는다. 자료 부족을 관점 생성으로 메우지 않는다.

## Review Focus

- DB 저장 직전 서버가 종료되면 마지막 외부 호출이 중복될 수 있음: 재개 경계를 테스트한다.
- 인용 구절이 있지만 주장과 무관한 자료: 의미 검토 실패 결과를 표시하지 않는다.
- 자료가 한쪽만 확보됨: 자료 한계와 한 관점을 허용하며 강제 찬반을 생성하지 않는다.
- 취소 직후 API 응답 도착: 추가 요청과 결과 반영을 막는다.
- 기존 localStorage 기록: 새 서버 기록과 함께 재열람 가능해야 한다.

### Task 1. 작업 저장과 상태 전이

Files: create `job_store.py`, `test_job_store.py`; modify `.gitignore`.
Interfaces: `create_job(input_data)->dict`, `claim_next()->dict|None`, `save_stage(job_id, stage, input_hash, output, validation)->None`, `get_job(id)->dict|None`, `list_jobs(limit,before)->dict`, `recover_interrupted()->None`.

- [ ] 임시 SQLite로 선점 1회·완료 체크포인트 원자성·재시작 running→interrupted·중복 입력 재사용 테스트 작성.
- [ ] 스키마와 SQL 트랜잭션, 스레드별 연결 구현. 저장소의 기존 DB ignore 확인.
- [ ] `py -m unittest test_job_store.py` 실행 후 검토·커밋.

### Task 2. 비동기 접수 API

Files: modify `server.py`, `test_local_http.py`.
Interfaces: 명세의 `/api/jobs` 6개 경로. `POST`는 실행을 기다리지 않고 202 반환. 응답 샘플을 B에게 문서로 제공.

- [ ] 잘못된 입력·큐 상한·상태 충돌·Host/Origin·DB 파일 접근 거부 테스트 작성.
- [ ] job_store로 요청을 연결하고 원문을 목록에서 제외. 동기 API는 전환 기간 유지.
- [ ] 로컬 HTTP 테스트로 접수와 조회 확인 후 커밋.

### Task 3. 자료 공급과 근거 검증

Files: create `research.py`, `prompts.py`, `test_research.py`; modify `server.py`의 기존 함수 참조.
Interfaces: `plan(article)->dict`, `collect(plan,budget)->list[dict]`, `draft(article,plan,documents)->dict`, `review(article,draft,documents)->dict`, `validate_evidence(evidence,documents)->dict`.

- [ ] 실제 키 없이 fixture로 잘못된 ID·없는 quote·공백 차이·snippet_only·무관한 주장의 검토 실패 테스트 작성.
- [ ] 기존 외부 호출·키 로더·429 처리 로직을 보존해 이동. 예산은 호출 전에 하네스에서 예약하고 공급자는 숨은 재시도를 하지 않도록 계약 변경.
- [ ] 계획·초안·검토의 JSON 타입과 길이 상한 검증 구현. 의미 검토 실패 fixture가 결과 관점에서 제외되는지 확인.
- [ ] 단위 테스트 후 커밋. 현재 경량 분석의 기존 테스트도 이동에 맞게 유지.

### Task 4. 워커와 체크포인트 실행

Files: create `harness.py`, `test_harness.py`; modify `server.py` 시작/종료.
Interfaces: `run_job(job_id)->None`, `worker_loop(stop_event)->None`, `assemble_result(article, approved_draft, documents)->dict`.

- [ ] 단계 중 실패 후 재개·전체 시도 예산·429 대기·서버 재시작·취소 후 추가 호출 0건·결과 미반영 테스트 작성.
- [ ] 단계 성공마다 저장. 실패 단계만 재실행하고 모델/프롬프트/입력 버전이 다르면 재개 거부.
- [ ] 기본 계획→수집→초안→검토→조립만 먼저 연결. 보완 조사 없이도 부족 사유를 명시해 동작하도록 구현.
- [ ] 단위 및 HTTP 통합 테스트 후 커밋.

### Task 5. 맡기기와 내 기록 UI

Files: modify `index.html`, `assets/app.css`; API 샘플을 `docs/job-api-examples.json`에 추가.
Interfaces: job 목록/상세는 명세 응답을 사용. 완료 result만 기존 `normalizeAnalysis`/`renderAnalysis`에 전달.

- [ ] 샘플 queued/running/completed/failed/interrupted 상태로 접수→내 기록→결과 이동 구현.
- [ ] 5초 polling, 숨겨진 탭에서 30초 또는 중지, 종료 상태에서 polling 중지 구현.
- [ ] 취소·재개·삭제 충돌·서버 미실행·기존 브라우저 기록을 처리. 퍼센트나 완료 예정 시각을 지어내지 않기.
- [ ] 360px와 PC에서 직접 확인. 브라우저를 닫고 다시 열어 작업이 유지되는지 확인 후 커밋.

### Task 6. 제한된 보완과 실제 기사 검증

Files: modify `harness.py`, `prompts.py`, `test_harness.py`, `LOCAL_REVIEW.md`, `README.md`.
Interfaces: `repair(article,plan,draft,review,documents,budget)->dict`, 실행은 전체 작업에서 최대 한 번.

- [ ] 보완 조건 충족 시만 실행·문서 교체 상한·두 번째 보완 금지·모든 관점 제거 시 실패 테스트 작성.
- [ ] 보완 단계에서 추가 자료 수집·추출·최종 LLM 요청 후 하드 검증. 별도 의미 검토를 추가 통과했다고 표시하지 않기.
- [ ] 자동 테스트 통과 후 서로 다른 기사 3건으로 원문/근거/입장 연결을 사람이 확인. 키와 원문은 로그에서 제외.
- [ ] 기본/보완 소요시간과 한계를 보고서에 기록. 현재 계획은 구현 지침이며 완료된 기능으로 문서화하지 않는다.

## 순서와 역할

A: Task 1→2→3→4→6. B: Task 2의 응답 샘플 확정 후 Task 5. Task 3은 Task 1/2의 기본 API 구현과 논리적으로 분리 가능하지만 A의 실제 시간에 맞춰 순서대로 진행한다.
첫 증분은 접수·저장·기본 조사·검토·재열람. 두 번째 증분은 보완 조사. 모든 기능을 한 번에 완성하려다 작업 저장과 재개가 빠지지 않게 한다.
