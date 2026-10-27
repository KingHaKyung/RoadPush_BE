# API 명세서 (v1)

> 기능 ID는 [FEATURE_SPEC.md](FEATURE_SPEC.md). 서버를 띄우면 `/docs`(Swagger UI)에서 같은 API를 바로 호출할 수 있습니다.

## 0. 공통

| 항목 | 값 |
|---|---|
| 기본 주소 | 로컬 `http://localhost:8000/api/v1` · 배포 `https://<공인 IP>.sslip.io/api/v1` |
| 형식 | 요청·응답 JSON (UTF-8). 사진 등록·검사만 `multipart/form-data` |
| 인증 | 없음 (시연용). CORS 허용: `http://localhost:5173`, `https://road-push-fe.vercel.app`, Vercel 미리보기 주소 |
| 시각 | ISO 8601 + 시간대. 응답은 `+09:00`, 요청의 시간대 없는 시각은 422 |
| ID | 접두사 + 12자리: `site_…` `obs_…` `ana_…` `rev_…` `act_…` `note_…` |
| 파일 주소 | 응답의 `images.*`는 바로 열 수 있는 전체 URL (`/files/{key}`) |
| 오류 형식 | `{"detail": "사람이 읽는 한국어 메시지"}` (입력 검증 오류는 FastAPI 기본 `detail` 배열) |

| 상태 코드 | 뜻 | 화면 처리 |
|---|---|---|
| 200 · 201 · 204 | 성공 · 생성 · 삭제(본문 없음) | |
| 202 | 접수됨, 분석은 대기열에서 진행 | "AI 분석 대기 중" 표시 후 다시 조회 |
| 404 | 대상 없음 | |
| 409 | 지금 상태에서는 할 수 없음 (이미 분석 중, 취소할 검수 없음 등) | `detail`을 그대로 안내 |
| 415 | 지원하지 않는 파일 형식 | |
| 422 | 입력 값 오류 (시간대 없음, 구간 미확인, 손상 이미지 등) | `detail`을 그대로 안내 |

## 1. 관측 응답 `Observation` (마스터 문서 4.4 + 화면용 필드)

목록·상세·등록·검수·처리·메모 응답이 모두 이 형식입니다.

| 필드 | 형식 | 설명 |
|---|---|---|
| **observation_id** | string | 관측 ID |
| **analysis_id** | string \| null | 응답에 쓴 분석 (취소한 재분석은 건너뜀) |
| **site_id** | string | 확정 구간 |
| **location** | `{latitude, longitude}` | **구간 대표 위치** (같은 구간 관측은 같은 좌표) |
| **captured_at** | datetime | 촬영 시각 (+09:00) |
| **processing_status** | enum | `pending` · `processing` · `completed` · `failed` |
| **passability** | enum \| null | `passable` · `narrowed` · `blocked` · `undetermined`. 완료 전 null |
| **risk_score** | int \| null | 상대 위험도 0~100. 유보·완료 전 null |
| **uncertainty_reason** | string \| null | 판단 유보 사유 문장 |
| **images** | `{original, surface_overlay, obstacle_overlay}` | 원본·근거 이미지 URL (overlay는 완료 후) |
| **review_status** | enum | `pending` · `confirmed` · `corrected` |
| **human_passability** | enum \| null | 사람이 수정한 판정 |
| site_name · link_id | string | 구간 이름 · 표준노드링크 LINK_ID |
| source · device | string | `upload` · `cctv` · `car` · `drone` / 기기 이름 |
| capture_location · location_source | object · string | 사진 실제 촬영 좌표 · 출처 `exif`·`manual`·`device` |
| area · addr | string \| null | 시군구 + 법정동 · 지번 주소 (비면 자동 채움) |
| occupancy | int \| null | 병목 단면 점유율(%) |
| bottleneck_ratio · bottleneck_m_est | float \| null | 남은 통로 비율 · 구간 보도 폭이 있을 때 추정 m |
| risk_complete | bool | false면 길이 항 없이 폭 항만 반영한 부분 지표 |
| causes | string[] | `차량` · `공사시설` · `적치물` · `PM` · `장애물` |
| uncertainty_code | string \| null | `surface_uncertain` · `boundary_review` · `boundary_failed` · `occupancy_unverified` · `low_coverage` |
| provisional | object \| null | `boundary_review`일 때만 검토용 추정 `{passability, bottleneck_ratio, occupancy}` (확정 아님) |
| reasons | string[] | 판정 근거 문장 |
| analysis_trigger · attempts · failure_reason | | `initial`·`reanalysis` · 시도 횟수 · 실패 사유 |
| review | object \| null | 최신 검수 `{id, status, human_passability, comment, reviewer, created_at}` |
| action | `{action_id, state, label, dept}` | 처리 상태 (`none`이면 `미처리`) |
| history | `[{t, txt, ai, by}]` | 분석·검수·메모·처리 이력 (시간순) |
| model_version · rule_version | string \| null | 판정을 만든 모델·규칙 버전 |

```json
{
  "observation_id": "obs_3f9a1c2b7d4e",
  "analysis_id": "ana_8b2c4d6e1f3a",
  "site_id": "site_1a2b3c4d5e6f",
  "location": { "latitude": 37.3868, "longitude": 127.1273 },
  "captured_at": "2026-10-27T14:32:00+09:00",
  "processing_status": "completed",
  "passability": "narrowed",
  "risk_score": 46,
  "uncertainty_reason": null,
  "images": { "original": "https://…/files/observations/obs_…/original.jpg", "surface_overlay": "https://…", "obstacle_overlay": "https://…" },
  "review_status": "pending",
  "human_passability": null,
  "causes": ["차량"],
  "action": { "action_id": null, "state": "none", "label": "미처리", "dept": null }
}
```
(값은 형식 예시)

## 2. 관측 (`/observations`)

### POST `/observations/inspect` — 등록 전 사진 검사 (F1-1)
`multipart/form-data`

| 필드 | 필수 | 설명 |
|---|---|---|
| file | ✓ | 사진 (`.jpg` `.jpeg` `.png` `.webp`) 또는 영상 (`.mp4` `.mov` `.avi` `.mkv`) |

응답 200: `{captured_at, lat, lng, candidates}` — `candidates`는 GPS가 있을 때만 `GET /sites/candidates`와 같은 형식. 오류 415.

### POST `/observations` — 관측 등록 (F1-3)
`multipart/form-data` · 응답 **202** `Observation` (`processing_status=pending`)

| 필드 | 필수 | 설명 |
|---|---|---|
| file | ✓ | 사진(영상은 선택 기능) |
| captured_at | ✓ | 촬영 시각, 시간대 포함 (예: `2026-10-27T14:32:00+09:00`) |
| captured_at_source | | `exif` · `manual`(기본) · `device` |
| lat · lng | | 함께 보내야 함. 신규 구간이면 필수 |
| location_source | | `exif` · `manual` · `device` |
| site_id | △ | 후보에서 고른 기존 구간 |
| new_site_name · new_site_description | △ | 일치하는 구간이 없을 때 신규 구간 |
| source · device | | 기본 `upload` |
| area · addr | | 비우면 분석 때 자동 채움 |

△ `site_id`와 `new_site_name` 중 하나는 필수.
오류: 404 구간 없음 · 415 형식 · 422 시간대 없음 / lat·lng 한쪽만 / 구간 미확인 / 신규 구간인데 위치 없음 / 이미지 손상.

### GET `/observations` — 목록·검수 표 (F7-1)
응답 200 `Observation[]` (촬영 시각 최신순)

| 쿼리 | 설명 |
|---|---|
| site_id · source | 구간 · 출처 |
| processing_status · passability · review_status | 상태 필터 (passability는 AI 판정) |
| since · until | 촬영 시각 범위 (시간대 포함) |
| limit | 기본 500, 최대 2,000 |

### GET `/observations/{id}` — 상세 (F4-1)
응답 200 `Observation` · 404

### GET `/observations/{id}/analyses` — 분석 이력 (F2-7)
응답 200 `[{id, trigger, reanalysis_note, status, attempts, error, error_kind, model_version, rule_version, passability, risk_score, occupancy, bottleneck_ratio, bottleneck_m_est, confidence, uncertainty_code, uncertainty_reason, causes, evidence, files, created_at, finished_at}]`

### POST `/observations/{id}/reanalyze` — 재분석 요청 (F7-2)
본문(선택) `{"note": "무엇을 보완했는지"}` · 응답 **202** `Observation` · 409 이미 분석 대기 중

### POST `/observations/{id}/reanalyze/cancel` — 재분석 취소 (F7-3)
응답 200 `Observation` (이전 분석 결과·검수로 돌아감) · 409 취소할 재분석 없음 / 이미 시작됨

### POST `/observations/{id}/reviews` — 확인·수정 (F5-1)

| 필드 | 필수 | 설명 |
|---|---|---|
| status | ✓ | `confirmed`(AI 판정 확인) · `corrected`(사람 판정으로 수정) |
| human_passability | corrected일 때 ✓ | `passable` · `narrowed` · `blocked` · `undetermined` |
| comment | | 확인 의견 또는 수정 이유(오탐 사유) |
| reviewer | | 검수자 (예: `교통행정과`) |
| analysis_id | | 비우면 최신 완료 분석 |

응답 200 `Observation` · 404 이 관측의 분석이 아님 · 409 완료된 분석 없음 · 422 수정인데 판정 없음

### POST `/observations/{id}/reviews/cancel` — 판정 취소 (F5-2)
본문(선택) `{comment, reviewer, analysis_id}` · 응답 200 `Observation` (`review_status=pending`) · 409 취소할 검수 없음

### POST `/observations/{id}/notes` — 메모 (F5-5)
본문 `{"content": "운영사 수거 요청 접수 #1234", "author": "교통행정과"}` · 응답 **201** `Observation` · 422 빈 메모

### POST `/observations/{id}/action` — 처리 상태 변경 (F5-3)

| 필드 | 필수 | 설명 |
|---|---|---|
| state | ✓ | `none` · `inspection_requested` · `in_progress` · `done` |
| dept | | 담당 부서. `null`을 명시하면 배정 해제 |
| content · actor | | 처리 내용 · 처리자 |

이 관측을 '조치 전'으로 하는 조치가 없으면 만들고, 있으면 갱신 (`action_logs`에 이력). 응답 200 `Observation`.

### POST `/observations/bulk-action` — 일괄 처리 (F5-3)
본문 `{"observation_ids": ["obs_…"], "state": "inspection_requested", "dept": "주차관리과", "content": null, "actor": "교통행정과"}` · 응답 200 `Observation[]` · 404 없는 관측

### DELETE `/observations/{id}` — 관측 삭제 (F7-4)
분석·검수·메모·조치(조치 전으로 연결된 것)·사진 파일을 함께 삭제. '조치 후'로 연결된 조치는 연결만 끊음. 응답 **204** · 409 분석 중

## 3. 지도 (`/map`)

### GET `/map` — 구간별 최신 판정 (F3-1)

| 쿼리 | 설명 |
|---|---|
| south · west · north · east | 넷 다 주면 그 범위의 구간만 |

응답 200 `[{site_id, site_name, location, latest: Observation, reference: Observation|null, effective_passability, effective_source}]`
- `reference`: 최신이 대기·분석 중·실패일 때 이전 완료 관측
- `effective_passability`: 지도 색 = 관리자 판정 > AI 판정, 출처 `human` · `ai`

## 4. 구간 (`/sites`)

| 메서드 · 경로 | 기능 | 요청 | 응답 · 오류 |
|---|---|---|---|
| GET `/sites/candidates?lat=&lng=&radius_m=` | 구간 후보 (F1-2) | 반경 기본 50 m | `{radius_m, sites:[{site, distance_m, latest_captured_at, latest_thumbnail}], nearby_links:[{link_id, road, distance_m}], reference_link, reference_walk_link}` |
| GET `/sites` | 목록 (F1-4) | | `Site[]` |
| POST `/sites` | 생성 | `{name, lat, lng, description?, link_id?, sidewalk_width_m?}` | 201 `Site` (link_id 비우면 가까운 링크, 보도 폭은 참조 레이어로 채움) |
| GET `/sites/{id}` | 조회 | | `Site` · 404 |
| PATCH `/sites/{id}` | 수정 | 바꿀 필드만 | `Site` (보도 폭을 고치면 출처 `field`) |
| DELETE `/sites/{id}` | 삭제 | | 204 · 409 관측이 있음 |
| GET `/sites/{id}/observations` | 같은 구간 관측 시간순 (F6-1) | | `Observation[]` (촬영 시각 오름차순) |
| GET `/sites/{id}/actions` | 구간 조치 이력 (F5-4) | | `Action[]` |
| GET `/sites/{id}/compare?before=&after=` | 전후 비교 (F6-2) | 조치 전·후 관측 ID | 아래 · 404 · 422 다른 구간 / 전후 시각 역전 |

`Site` = `{id, name, description, lat, lng, link_id, sidewalk_width_m, sidewalk_width_source, ref_walk_link_id, created_at}`
`sidewalk_width_source` = `field` · `ref_ngii` · `ref_building_no` · `ref_route_approx`

전후 비교 응답:
```json
{
  "site_id": "site_…",
  "before": { "…": "Observation" },
  "after": { "…": "Observation" },
  "actions": [ { "…": "Action" } ],
  "comparable": true,
  "note": "같은 모델·규칙 기준. 촬영 위치·방향이 같은지는 사진으로 확인하세요",
  "delta": { "passability": ["blocked", "passable"], "risk_score": -38, "bottleneck_ratio": 0.42 }
}
```
모델·규칙 버전이 다르거나 완료된 분석이 없으면 `comparable=false`, `delta=null` (사진·상태만 비교).

## 5. 현장 조치 (`/actions`)

| 메서드 · 경로 | 기능 | 요청 | 응답 · 오류 |
|---|---|---|---|
| POST `/actions` | 조치 생성 (F5-4) | `{before_observation_id, state?='inspection_requested', dept?, content?, actor?}` | 201 `Action` · 404 |
| PATCH `/actions/{id}` | 조치 갱신 · 조치 후 관측 연결 | `{state?, dept?, content?, after_observation_id?, actor?, note?}` | 200 `Action` · 404 · 422 다른 구간 / 조치 전보다 먼저 촬영 |

`Action` = `{id, site_id, before_observation_id, after_observation_id, state, label, dept, content, created_at, updated_at, logs:[{state, dept, note, actor, created_at}]}`

## 6. 기타

| 메서드 · 경로 | 설명 |
|---|---|
| GET `/api/v1/health` | `{"status": "ok", "analysis_engine": "roadpush_ai", "obstacle_causes": true}` |
| GET `/files/{key}` | 원본·overlay 파일 (응답 `images`에 전체 URL로 들어감) |
| GET `/docs` · `/openapi.json` | Swagger UI · OpenAPI 명세 |
| GET `/vworld/wmts/{레이어/z/y/x.png}` | 브이월드 타일 프록시 (`VWORLD_KEY`) |
| GET `/api/utic/player?cctvid=` | UTIC 재생 페이지 302 (`UTIC_API_KEY`, 등록 IP 서버만) |
| GET `/api/its/cctvs` · `/api/its/stream` | ITS CCTV 위치 · HLS 주소 (`ITS_API_KEY`) |

## 7. 프론트 연결 현황

| 프론트 | 호출하는 API |
|---|---|
| `/dashboard` (`src/api/client.ts`) | `GET /observations` (5초마다) · `POST /observations/bulk-action` · `reviews` · `reviews/cancel` · `reanalyze` · `reanalyze/cancel` · `notes` |
| `/report` (`src/report/api.ts`) | `POST /observations/inspect` · `GET /sites/candidates` · `POST /observations` |
| 미연결 | `GET /map` · `GET /observations/{id}` · `analyses` · `/actions` · `/sites/{id}/compare` · `/sites/{id}/observations` · `DELETE` |
