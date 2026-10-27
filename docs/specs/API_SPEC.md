# RoadPush API 명세서 (v1)

| 항목 | 내용 |
|---|---|
| 기본 주소 · 형식 | `https://<백엔드 서버>/api/v1` · Swagger UI `/docs` · JSON, 사진 등록·검사만 `multipart/form-data` |
| 시각 · ID | ISO 8601 + 시간대 (응답 `+09:00`, 시간대 없는 요청은 422) · `site_` `obs_` `ana_` `rev_` `act_` + 12자리 |
| 인증 · CORS | 인증 없음 (시연용) · 프론트 주소만 허용 (`road-push-fe.vercel.app`과 Vercel 미리보기) |
| 오류 | `{"detail": "한국어 메시지"}` · 404 대상 없음 · 409 지금 상태에서 불가 · 415 파일 형식 · 422 입력 오류 |

## 엔드포인트

| ID | 메서드 · 경로 | 요청 핵심 | 응답 · 주요 오류 |
|---|---|---|---|
| F1 | `POST /observations/inspect` | `file` | 200 촬영 시각 · GPS · 구간 후보 · 415 |
| F1 | `GET /sites/candidates` | `lat` `lng` `radius_m`(기본 50) | 200 반경 안 구간 · 링크 · 보도 폭 |
| F1 | `POST /observations` | `file` `captured_at` `lat` `lng` + `site_id` 또는 `new_site_name` | **202** `pending` · 404 · 415 · 422 |
| F7 | `GET /observations` | `site_id` `processing_status` `passability` `review_status` `since` `until` | 200 목록 (최신순) |
| F4 | `GET /observations/{id}` · `/analyses` | — | 200 Observation · 분석 이력 · 404 |
| F7 | `POST /observations/{id}/reanalyze` · `/cancel` | `note` | 202 · 200 · 409 대기 중·시작됨 |
| F5 | `POST /observations/{id}/reviews` · `/cancel` | `status` `human_passability` `comment` | 200 · 409 검수할 분석 없음 |
| F5 | `POST /observations/{id}/notes` | `content` `author` | 201 Observation |
| F5 | `POST /observations/{id}/action` · `/bulk-action` | `state` `dept` `content` (`observation_ids`) | 200 |
| F5 | `POST /actions` · `PATCH /actions/{id}` | `before_observation_id` · `after_observation_id` `state` | 201 · 200 · 422 |
| F3 | `GET /map` | `south` `west` `north` `east` (선택) | 200 구간별 최신 · 지도 색 |
| F6 | `GET /sites/{id}/compare` | `before` `after` | 200 전후 · `delta`(같은 버전만) |
| 관리 | `/sites` CRUD · `DELETE /observations/{id}` | `name` `lat` `lng` `sidewalk_width_m` | 201 · 204 · 409 |
| 운영 | `GET /health` · `GET /files/{key}` | — | 상태 · 원본·근거 이미지 |

## 공통 응답 `Observation`

| 구분 | 필드 |
|---|---|
| 마스터 문서 4.4 | `observation_id` `analysis_id` `site_id` `location`(구간 대표 위치) `captured_at` `processing_status` `passability` `risk_score`(0~100) `uncertainty_reason` `images{original, surface_overlay, obstacle_overlay}` `review_status` `human_passability` |
| 화면용 | `site_name` `area` `addr` `occupancy` `bottleneck_ratio` `causes[]` `uncertainty_code` `action{state, label, dept}` `history[]` `model_version` |
| 원칙 | 처리 상태 · AI 판정 · 사람 검수는 따로 · 완료 전·판단 유보면 수치는 `null` |

## 코드값

| 필드 | 값 |
|---|---|
| `processing_status` | `pending` 대기 · `processing` 분석 중 · `completed` 완료 · `failed` 실패 |
| `passability` | `passable` 통과 가능 · `narrowed` 좁아짐 · `blocked` 단절/우회 필요 · `undetermined` 판단 유보 |
| `review_status` | `pending` 미검수 · `confirmed` 확인 · `corrected` 수정 |
| `action.state` | `none` 미처리 · `inspection_requested` 현장점검 요청 · `in_progress` 조치 중 · `done` 조치 완료 |
| `uncertainty_code` | `surface_uncertain` · `boundary_review` · `boundary_failed` · `occupancy_unverified` · `low_coverage` |
| `causes` | `차량` · `공사시설` · `적치물` · `PM` · `장애물`(클래스 없음) |
