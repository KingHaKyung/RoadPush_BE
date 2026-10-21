# RoadPush Backend

**RoadPush — 보행자를 '차도로 밀어내는 길' AI 자동탐지**의 백엔드입니다.
사진에서 보도·차도와 장애물을 분석해 **남은 연속 통로**를 계산하고, 통과 가능 / 좁아짐 / 단절·우회 필요 / 판단 유보와
근거를 지자체 담당자에게 제공합니다. 검수 → 현장조치 → 재촬영 전후 비교까지 기록합니다.

| 문서 | 내용 |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | **처음 받는 팀원용 로컬 실행 가이드** (설치 → 실행 → 시연 데이터 → 확인 → 문제 해결) |
| [docs/GUIDE.md](docs/GUIDE.md) | 전체 처리 흐름, 폴더 구조, 모델 운용, 제출 준비, 일정 |
| [docs/API.md](docs/API.md) | API (마스터 문서 4.4 응답 형식), 프론트 필드 매핑 |
| [docs/DB.md](docs/DB.md) | 테이블 정의·관계도, Supabase 연결, 스키마 변경 방법 |
| [docs/PASSABILITY.md](docs/PASSABILITY.md) | 통과 여부·상대 위험도 계산, 상대/절대 수치, 한계와 극복 |

프론트엔드: [KingHaKyung/RoadPush_FE](https://github.com/KingHaKyung/RoadPush_FE)

## 빠른 실행

Python 3.11~3.12 권장. 자세한 순서와 Windows 명령은 [docs/SETUP.md](docs/SETUP.md).

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1   /   macOS·Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # Windows: copy .env.example .env
uvicorn app.main:app --reload     # http://localhost:8000/docs
python scripts/seed_demo.py       # 다른 터미널에서: 시연 데이터 넣기
```

기본값은 **SQLite + dummy 모델**이라 GPU·가중치·DB 설치 없이 등록 → 분석 → 지도 → 검수 → 조치 → 전후 비교 흐름을 확인할 수 있습니다.
서버를 켜면 DB 테이블이 자동으로 만들어집니다 (Alembic 마이그레이션).

| 하고 싶은 것 | 방법 |
|---|---|
| 실제 모델(GPU) | [SETUP.md 8장](docs/SETUP.md#8-실제-모델로-돌리기-gpu가-있는-팀원) |
| Supabase(PostgreSQL) 연결 — 배포 서버만 | [DB.md 2장](docs/DB.md#2-supabase-연결-순서) |
| 테이블 구조 변경 | [DB.md 5장](docs/DB.md#5-스키마를-바꿀-때) |
| 사진 폴더 일괄 분석·임계값 보정 | `python scripts/analyze_folder.py <폴더> --labels labels.csv --out outputs/x` |
| 분석 워커 분리 | `.env`에 `RUN_WORKER_IN_API=false` 후 `python -m app.services.worker` |
| 테스트 | `pytest` · `ruff check app tests scripts migrations` |
| PostgreSQL에서 테스트 | `TEST_DATABASE_URL=postgresql+psycopg://… pytest` |

## 데이터·모델 출처

- 도로 링크 참고: 국토교통부 표준노드링크 (성남시 분당구 예시 지역, 프론트 저장소와 같은 변환본)
- 노면: [SelectStar DeepLab v3+](https://github.com/selectstarofficial/segmentation-selectstar) (AI Hub 인도보행 영상)
- 장애물: [Ultralytics YOLO11 / YOLOE](https://docs.ultralytics.com/) (AGPL-3.0)
