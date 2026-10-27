# 기능 명세서 (백엔드)

> 기준: 마스터 설계·근거 문서 V4 4.6(F1~F7)·4.4 공통 응답, 사전질의 답변서 1~3.
> API 상세 [API_SPEC.md](API_SPEC.md) · 테이블 [DB.md 4장](../DB.md#4-테이블-정의) · 판정 규칙 [PASSABILITY.md](../PASSABILITY.md) · 구성도 [architecture/](../architecture/)

## 1. 사용자와 범위

| 사용자 | 하는 일 | 화면 |
|---|---|---|
| 현장 촬영자 | 보도 사진을 찍어 위치·구간을 확인하고 등록 | 프론트 `/report` |
| 관리자 (지자체·도로관리기관) | 지도·목록으로 판정 확인, 검수, 처리 상태·담당 부서 지정, 메모, 재분석 | 프론트 `/dashboard` |
| 운영자 (백엔드 담당) | 구간 관리, 잘못 올린 관측 삭제, 모델·배포 점검 | `/docs` (Swagger UI), 스크립트 |

범위 밖 (답변서 기준): 개인 길찾기, 주민 민원 접수 창구, 휠체어·유모차 기준 판정(확장 과제), 실제 사고 감소 주장.

## 2. 기능 목록

`프론트 연결`: ✅ 화면에서 호출함 · ➖ 백엔드 내부 자동 처리 · ⬜ API만 있음 (화면 미연결)

### F1. 관측 등록·구간 확인

| ID | 기능 | 설명 | API | 처리 규칙 | 프론트 연결 |
|---|---|---|---|---|---|
| F1-1 | 사진 메타데이터 확인 | 등록 전 사진의 촬영 시각·GPS를 읽고, 위치가 있으면 주변 구간 후보를 함께 준다 | `POST /observations/inspect` | EXIF가 없으면 null → 사용자가 보완. 원본은 저장하지 않음 | ✅ `/report` |
| F1-2 | 구간 후보 조회 | 반경 안 기존 구간(최근 사진 썸네일 포함), 가까운 표준노드링크, 참조 레이어 정보 | `GET /sites/candidates` | 반경 기본 50 m. 가깝다는 이유만으로 자동 확정하지 않음 | ✅ `/report` |
| F1-3 | 관측 등록 | 사진 + 촬영 시각 + 위치 + 확정 구간을 저장하고 분석을 대기열에 넣는다 | `POST /observations` | `site_id`(기존) 또는 `new_site_name`(신규) 필수. 시간대 없는 시각 거부. 202 즉시 응답 | ✅ `/report` |
| F1-4 | 구간 관리 | 구간 목록·생성·수정·삭제 | `GET·POST /sites`, `GET·PATCH·DELETE /sites/{id}` | 관측이 있는 구간은 삭제 불가(409). 실측 폭을 고치면 출처 `field` | ⬜ (`/docs`로 관리) |
| F1-5 | 행정구역·주소 자동 채우기 | 비어 있는 `area`·`addr`을 브이월드 역지오코딩으로 채움 | (분석 워커) | 키가 없거나 실패해도 분석은 진행. 등록 때 넣은 값은 덮어쓰지 않음 | ➖ |
| F1-6 | 참조 보도 폭 연결 | 신규 구간 위치에서 가까운 참조 링크를 붙이고, 실측 폭이 없으면 보도 폭을 채움 | (구간 등록 때) | 성남 표준노드링크 우선, 서울 보행 링크 대체. 근사값(`route_approx`)은 기본 제외 | ➖ |

### F2. AI 분석

| ID | 기능 | 설명 | API | 처리 규칙 | 프론트 연결 |
|---|---|---|---|---|---|
| F2-1 | 분석 대기열 | `analyses` 행이 곧 작업 큐. 워커 1개가 순서대로 처리 | (내부) | pending → processing → completed / failed. 서버 재시작 시 processing을 pending으로 복구 | ➖ |
| F2-2 | 자동 재시도 | 실행 오류는 같은 작업을 다시 시도 | (내부) | 첫 시도 포함 최대 3회. 손상·미지원 입력은 재시도 없이 실패 | ➖ |
| F2-3 | 보행면·통로 분석 | RoadPush_AI `RoadPushAnalyzer.analyze_photo()` 1회 호출 (Mask2Former → Phase 6 → 7-A → 7-B, YOLO11s) | (내부) | 모델 객체는 워커 수명 동안 재사용. AI 원본 응답 전체를 `evidence`에 저장 | ➖ |
| F2-4 | 판정 | 7-B 결과를 통과·좁아짐·단절·판단 유보로 바꾼다 | (내부) | 백엔드는 값을 다시 계산하지 않음. 측정 불가는 0%가 아니라 유보 + 사유 ([PASSABILITY.md 0장](../PASSABILITY.md)) | ➖ |
| F2-5 | 원인 배지·처리 유형 | 통로를 막은 덩어리와 겹친 탐지만 원인으로 | (내부) | 차량(C) · 공사시설(D) · 적치물(B) · PM(A). 상태를 '불법'으로 단정하지 않음 | ➖ |
| F2-6 | 근거 이미지 | 보행면·장애물 overlay 생성 | `GET /files/{key}` | 응답 `images`에 전체 URL | ✅ |
| F2-7 | 분석 이력 | 재시도 횟수, 재분석 구분, 근거 JSON | `GET /observations/{id}/analyses` | 재분석해도 이전 결과를 지우지 않음 | ⬜ |

### F3. 위험 지도 · F4. 상세

| ID | 기능 | 설명 | API | 처리 규칙 | 프론트 연결 |
|---|---|---|---|---|---|
| F3-1 | 구간별 최신 판정 | 구간마다 최신 촬영 관측, 최신이 대기·실패면 이전 완료 결과를 `reference`로 | `GET /map` | 지도 색 = 관리자 판정 > AI 판정 (`effective_source`). 최신이 유보면 이전 정상 판정으로 대체하지 않음 | ⬜ (프론트는 `GET /observations`로 직접 계산) |
| F4-1 | 관측 상세 | 4.4 공통 응답 + 화면용 필드 + 이력 | `GET /observations/{id}` | 처리 상태·AI 판정·검수 상태를 한 필드로 합치지 않음 | ⬜ (목록 응답으로 대신) |

### F5. 검수·처리

| ID | 기능 | 설명 | API | 처리 규칙 | 프론트 연결 |
|---|---|---|---|---|---|
| F5-1 | AI 판정 확인·수정 | 확인(의견) 또는 사람 판정 + 오탐 사유 | `POST /observations/{id}/reviews` | 완료된 분석에만. AI 결과는 그대로 두고 분석 단위로 저장 | ✅ |
| F5-2 | 판정 취소 | 확인·수정을 취소해 미검수로 | `POST /observations/{id}/reviews/cancel` | 이력은 지우지 않고 취소 행을 추가. 검수가 없으면 409 | ✅ |
| F5-3 | 처리 상태·담당 부서 | 미처리 → 현장점검 요청 → 조치 중 → 조치 완료, 부서 배정·해제 | `POST /observations/{id}/action`, `POST /observations/bulk-action` | 조치가 없으면 만들고 있으면 갱신. 변경마다 `action_logs`에 기록 | ✅ (일괄) |
| F5-4 | 현장 조치 · 조치 후 관측 연결 | 조치 생성·갱신, 재촬영 관측을 '조치 후'로 연결 | `POST /actions`, `PATCH /actions/{id}`, `GET /sites/{id}/actions` | 조치 후 관측은 같은 구간 + 조치 전보다 나중 촬영 | ⬜ |
| F5-5 | 메모 | 접수번호 등 자유 메모 | `POST /observations/{id}/notes` | 처리 상태·검수는 바꾸지 않고 `history`에 남음 | ✅ |

### F6. 전후 비교

| ID | 기능 | 설명 | API | 처리 규칙 | 프론트 연결 |
|---|---|---|---|---|---|
| F6-1 | 같은 구간 관측 시간순 | 전후로 고를 관측 목록 | `GET /sites/{id}/observations` | 촬영 시각 오름차순 | ⬜ |
| F6-2 | 조치 전후 비교 | 두 관측 + 관련 조치 + 지표 변화 | `GET /sites/{id}/compare?before=&after=` | 모델·규칙 버전이 같을 때만 지표 변화(`delta`). 위험도 변화 ≠ 사고 감소 | ⬜ |

### F7. 검수 표·재분석

| ID | 기능 | 설명 | API | 처리 규칙 | 프론트 연결 |
|---|---|---|---|---|---|
| F7-1 | 관측 목록·필터 | 처리 상태·판정·검수 상태·구간·출처·기간 | `GET /observations` | 촬영 시각 최신순, 최대 2,000건 | ✅ (필터는 화면에서) |
| F7-2 | 재분석 요청 | 새 분석 행을 만들어 다시 분석 | `POST /observations/{id}/reanalyze` | 이미 대기 중이면 409. 새 분석은 미검수부터 | ✅ |
| F7-3 | 재분석 취소 | 시작 전 재분석 요청 취소 → 이전 결과로 | `POST /observations/{id}/reanalyze/cancel` | pending일 때만. 이미 시작했으면 409 | ✅ |
| F7-4 | 관측 삭제 | 잘못 올린 관측과 분석·검수·메모·조치·파일 삭제 | `DELETE /observations/{id}` | 되돌릴 수 없음. 분석 중이면 409. 구간은 남김 | ⬜ (관리용) |

### 운영

| ID | 기능 | API | 설명 |
|---|---|---|---|
| S-1 | 상태 확인 | `GET /health` | 서버 상태, 분석 엔진(`roadpush_ai`·`dummy`) |
| S-2 | API 문서 | `GET /docs` | Swagger UI에서 바로 호출 |
| S-3 | 지도·CCTV 프록시 | `/vworld/wmts/*`, `/api/utic/player`, `/api/its/*` | 배포에서는 선택 (지도는 프론트 Vercel 함수가 처리) |
| S-4 | 모델 점검 | `python scripts/check_model.py <사진>` | 모델 파일·SHA-256·클래스 매핑·사진 1장 분석 |
| S-5 | 시연 데이터 | `python scripts/seed_demo.py --base <API 주소>` | 분석이 끝난 예시 관측 등록 |

## 3. 상태와 코드

| 항목 | 값 | 뜻 |
|---|---|---|
| 처리 상태 `processing_status` | `pending` · `processing` · `completed` · `failed` | 분석 대기 · 분석 중 · 완료(판정 또는 유보) · 실패 |
| AI 판정 `passability` | `passable` · `narrowed` · `blocked` · `undetermined` | 통과 가능 · 좁아짐 · 단절/우회 필요 · 판단 유보 |
| 검수 상태 `review_status` | `pending` · `confirmed` · `corrected` | 미검수 · 확인 · 수정 |
| 처리 상태 `action.state` | `none` · `inspection_requested` · `in_progress` · `done` | 미처리 · 현장점검 요청 · 조치 중 · 조치 완료 |
| 유보 사유 `uncertainty_code` | `surface_uncertain` · `boundary_review` · `boundary_failed` · `occupancy_unverified` · `low_coverage` | 걸을 면 미정 · 경계 사람 검토 · 경계 실패 · 점유 확인 불가 · 측정 범위 부족 |
| 원인 `causes` | `차량` · `공사시설` · `적치물` · `PM` (+ 클래스 없는 덩어리 `장애물`) | 프론트 처리 경로 C · D · B · A |
| 출처 `source` | `upload` · `cctv` · `car` · `drone` | MVP는 `upload` |

## 4. 비기능 요구사항

| 항목 | 기준 | 구현 |
|---|---|---|
| 응답 형식 | 마스터 문서 4.4 공통 분석 응답 | `app/api/schemas.py` `ObservationOut` |
| 시간 | 응답은 한국 시간(+09:00), DB는 UTC | 등록 시 시간대 없는 시각 거부 |
| 응답성 | 등록은 분석을 기다리지 않음 | 202 + 대기열, 화면은 5초마다 다시 조회 |
| 신뢰성 | 실패와 재시도 구분, 재시작 복구 | 재시도 3회, `recover_stuck()` |
| 추적성 | 어떤 모델·규칙으로 나온 판정인지 | `model_version`·`rule_version`·`evidence` 저장 |
| 사람 판단 보존 | AI 결과와 사람 판정 분리 | `reviews` 별도 테이블, 재분석 시 승계 안 함 |
| 보안 | DB 외부 노출 차단, 키 비노출 | Supabase 모든 테이블 RLS, 키는 서버 환경 변수 |
| 접근 | 프론트 주소만 허용 | CORS: `localhost:5173`, `road-push-fe.vercel.app`, Vercel 미리보기 |
| 이식성 | 로컬 SQLite ↔ 배포 PostgreSQL 같은 코드 | SQLAlchemy 2 + Alembic |
| 품질 | 변경마다 자동 확인 | pytest 59개 (기본 SQLite, `TEST_DATABASE_URL`로 PostgreSQL) |
| 개인정보 | 얼굴·번호판 비식별 (답변서2) | **미정** — 방법 확정 필요 |
| 인증 | 담당자 계정·권한 | **없음** (시연용) — 확장 과제 |
