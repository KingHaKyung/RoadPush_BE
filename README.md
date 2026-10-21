# RoadPush Backend

**RoadPush — 보행자를 '차도로 밀어내는 길' AI 자동탐지**의 백엔드입니다.
사진에서 보도·차도와 장애물을 분석해 **남은 연속 통로**를 계산하고, 통과 가능 / 좁아짐 / 단절·우회 필요 / 판단 유보와
근거를 지자체 담당자에게 제공합니다. 검수 → 현장조치 → 재촬영 전후 비교까지 기록합니다.

- 가이드·흐름·제출 준비: [docs/GUIDE.md](docs/GUIDE.md)
- API (마스터 문서 4.4 응답 형식): [docs/API.md](docs/API.md)
- 통과 여부·위험도 계산: [docs/PASSABILITY.md](docs/PASSABILITY.md)
- 프론트엔드: [KingHaKyung/RoadPush_FE](https://github.com/KingHaKyung/RoadPush_FE)

## 실행

Python 3.11 이상.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   /   macOS·Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # Windows: copy .env.example .env
uvicorn app.main:app --reload   # http://localhost:8000/docs
```

기본값은 **SQLite + dummy 모델**이라 GPU·가중치 없이 등록 → 분석 → 지도 → 검수 → 조치 → 전후 비교 흐름을 확인할 수 있습니다.

### 실제 모델

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124   # CUDA 버전은 pytorch.org에서 확인
pip install -e ".[ml]"
```
가중치를 `model_weights/`에 넣고 `.env`에서 `SURFACE_MODEL`, `OBSTACLE_MODEL`을 바꿉니다 ([model_weights/README.md](model_weights/README.md)).

### 분석 워커 분리 (선택)

`.env`에 `RUN_WORKER_IN_API=false`를 넣고 `python -m app.services.worker`를 따로 띄웁니다.

### PostgreSQL (선택)

```bash
docker compose up -d && pip install -e ".[postgres]"
# .env: DATABASE_URL=postgresql+psycopg://roadpush:roadpush@localhost:5432/roadpush
```

## 폴더 일괄 분석 (임계값 보정·평가)

```bash
python scripts/analyze_folder.py <사진 폴더> --labels <labels.csv> --out outputs/calib_v1
```

## 테스트

```bash
pytest
ruff check app tests scripts
```

## 데이터·모델 출처

- 도로 링크 참고: 국토교통부 표준노드링크 (성남시 분당구 예시 지역, 프론트 저장소와 같은 변환본)
- 노면: [SelectStar DeepLab v3+](https://github.com/selectstarofficial/segmentation-selectstar) (AI Hub 인도보행 영상)
- 장애물: [Ultralytics YOLO11 / YOLOE](https://docs.ultralytics.com/) (AGPL-3.0)
