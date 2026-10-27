# RoadPush 백엔드

**RoadPush — 보행자를 '차도로 밀어내는 길' AI 자동탐지**의 API 서버와 AI 분석 모듈입니다.
사진에서 보도·차도와 장애물을 분석해 **남은 연속 통로**를 계산하고, 통과 가능 / 좁아짐 / 단절·우회 필요 / 판단 유보와
근거를 지자체 담당자에게 제공합니다. 검수 → 현장조치 → 재촬영 전후 비교까지 기록합니다.

- 프론트엔드: [KingHaKyung/RoadPush_FE](https://github.com/KingHaKyung/RoadPush_FE) · https://road-push-fe.vercel.app
- 2026 AI 라이프 솔루션 챌린지 · 'AI 라이프 아이디어 챌린지' 후보작 RoadPush 구현

| 문서 | 내용 |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | 로컬 개발 환경 설정 (설치 → 실행 → 시연 데이터 → 확인 → 문제 해결) |
| [docs/GUIDE.md](docs/GUIDE.md) | 처리 흐름, 폴더 구조, 모델 운용, 배포, 제출 준비, 일정 |
| [docs/API.md](docs/API.md) | API (마스터 문서 4.4 응답 형식), 프론트 필드 매핑 |
| [docs/specs/API_SPEC.md](docs/specs/API_SPEC.md) | API 명세서: 엔드포인트별 요청·응답·오류 코드 |
| [docs/specs/FEATURE_SPEC.md](docs/specs/FEATURE_SPEC.md) | 기능 명세서: F1~F7 기능, 상태 코드, 비기능 요구사항, 프론트 연결 현황 |
| [docs/architecture/](docs/architecture/) | 시스템 아키텍처 · 서비스 흐름 구성도 (SVG · PNG) |
| [docs/FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md) | 프론트 연결 패치 적용 방법, Supabase → 백엔드 → 프론트 화면 전체 테스트 순서 |
| [docs/DB.md](docs/DB.md) | 테이블 정의·관계도, Supabase 연결, 스키마 변경 절차 |
| [docs/PASSABILITY.md](docs/PASSABILITY.md) | 통과 여부·상대 위험도 계산, 상대/절대 수치, 한계와 극복 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 실행·개발 중 발생한 문제의 원인·해결·재발 방지 기록 |

## 기술 스택

| 영역 | 사용 |
|---|---|
| API | Python 3.12, FastAPI, Pydantic |
| DB | SQLAlchemy 2, Alembic 마이그레이션 · 로컬 SQLite / 배포 Supabase PostgreSQL |
| AI 분석 | RoadPush_AI 통합 파이프라인 `roadpush_ai.RoadPushAnalyzer`를 그대로 사용: Surface (Mask2Former Mapillary → Phase 6 보도/차도 → 7-A 경계 → 7-B 연결 통로 폭) → 장애물 YOLO11s (선택 보행면에 접한 탐지만 원인 A~D) |
| 작업 처리 | DB 대기열 + 분석 워커 (제한 재시도) |
| 품질 | pytest (SQLite·PostgreSQL), ruff |

## 빠른 실행

Python 3.12 권장 (실제 모델은 3.12만). 자세한 순서와 Windows 명령은 [docs/SETUP.md](docs/SETUP.md).

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1   /   macOS·Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # Windows: copy .env.example .env
uvicorn app.main:app --reload     # http://localhost:8000 → API 문서
python scripts/seed_demo.py       # 다른 터미널에서: 시연 데이터 넣기
```

기본값은 **SQLite + dummy 엔진**(`ANALYSIS_ENGINE=dummy`)이라 GPU·가중치·DB 설치 없이 등록 → 분석 → 지도 → 검수 → 조치 → 전후 비교 흐름을 확인할 수 있습니다.
서버를 켜면 DB 테이블이 자동으로 만들어집니다 (Alembic 마이그레이션).

| 작업 | 방법 |
|---|---|
| 실제 모델 (`ANALYSIS_ENGINE=roadpush_ai`) | [SETUP.md 8장](docs/SETUP.md#8-실제-모델-실행-roadpush_ai-팀-파이프라인) · 점검 `python scripts/check_model.py 사진.jpg` |
| Supabase(PostgreSQL) 연결 | [DB.md 2장](docs/DB.md#2-supabase-연결-순서) · 확인 `python scripts/check_db.py` |
| 테이블 구조 변경 | [DB.md 5장](docs/DB.md#5-스키마-변경-절차) |
| 사진 폴더 일괄 분석·임계값 보정 | `python scripts/analyze_folder.py <폴더> --labels labels.csv --out outputs/x` |
| 분석 워커 분리 | `.env`에 `RUN_WORKER_IN_API=false` 후 `python -m app.services.worker` |
| 테스트 | `pytest` · `ruff check app tests scripts migrations` |
| PostgreSQL에서 테스트 | `TEST_DATABASE_URL=postgresql+psycopg://… pytest` |

## 프론트와 연결

| 항목 | 값 |
|---|---|
| API 기본 주소 | `http://localhost:8000/api/v1` (프론트 `.env`의 `VITE_API_BASE_URL`) |
| 응답 형식 | 마스터 문서 4.4 공통 분석 응답 + 화면용 필드 ([API.md](docs/API.md)) |
| 사진·overlay | 응답 `images`의 전체 URL (`/files/...`) |
| 허용 출처(CORS) | `http://localhost:5173`, `https://road-push-fe.vercel.app` (`.env`의 `CORS_ORIGINS`) |
| 배포 | 프론트가 HTTPS(Vercel)라 백엔드도 HTTPS 주소 필요 ([GUIDE.md 5장](docs/GUIDE.md#테스트-url-배포-구성)) |

## 화면별 API

| 프론트 화면 | 기능 | API |
|---|---|---|
| 관측 등록 | 사진 EXIF 읽기 · 구간 후보 확인 · 등록 | `POST /observations/inspect` · `GET /sites/candidates` · `POST /observations` |
| 위험 지도 | 구간별 최신 촬영 관측, 처리중이면 이전 결과 참고 | `GET /map` |
| 관측 목록 | 필터·정렬, 처리 상태 변경, 일괄 처리 | `GET /observations` · `POST /observations/{id}/action` · `POST /observations/bulk-action` |
| 판단 유보 | 사람 판정·재분석 요청과 취소 | `GET /observations?passability=undetermined` · `POST …/reviews` · `POST …/reviews/cancel` · `POST …/reanalyze` · `POST …/reanalyze/cancel` |
| 상세 | 원본·overlay·근거·이력·메모 | `GET /observations/{id}` · `GET /observations/{id}/analyses` · `POST /observations/{id}/notes` |
| 전후 비교 | 같은 구간 조치 전후 | `PATCH /actions/{id}` · `GET /sites/{id}/compare` |

## 폴더 구조

```
app/
  api/            라우터 · 응답 형식(schemas.py = 마스터 문서 4.4)
  services/       분석 워커 · 저장/조회 · EXIF · 파일 저장소 · 표준노드링크
  ai_engine/      roadpush.py(RoadPush_AI Phase 6→7-A→7-B 연결) · causes.py(장애물 원인) ·
                  passability/(7-B → 판정, dummy 규칙) · pipeline.py(엔진 선택)
  db/             DB 연결 · 테이블 정의
migrations/       DB 스키마 변경 이력 (Alembic)
scripts/          seed_demo · check_db · analyze_folder
model_weights/    (사용 안 함) 실제 모델은 RoadPush_AI 저장소 models/
storage/          원본·mask·overlay (Git 제외)
data/             표준노드링크 예시 지역 GeoJSON
docs/             가이드 문서
tests/
```

## 데이터·모델 출처

- 도로 링크 참고: 국토교통부 표준노드링크 (성남시 분당구 예시 지역, 프론트 저장소와 같은 변환본)
- 노면: [Mask2Former Swin-L Mapillary Vistas](https://huggingface.co/facebook/mask2former-swin-large-mapillary-vistas-semantic) (RoadPush_AI `manifests/model_assets_manifest.json`)
- 장애물 원인: RoadPush_AI 장애물 모델 `obstacle_yolo11s_full_os4` (YOLO11s, AI Hub 인도보행 영상 학습 — 국외 반출 금지) · Phase 6 차량 instance YOLO11n-seg
- [Ultralytics YOLO11](https://docs.ultralytics.com/) (AGPL-3.0) — 공개 서비스 전 라이선스 확인
