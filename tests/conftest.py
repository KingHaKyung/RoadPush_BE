import os
import tempfile
from pathlib import Path

# 앱을 불러오기 전에 임시 DB·저장소를 지정한다
_tmp = Path(tempfile.mkdtemp(prefix="roadpush-test-"))
# TEST_DATABASE_URL을 주면 그 DB(예: 로컬 PostgreSQL)로 같은 테스트를 돌린다
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["ANALYSIS_ENGINE"] = "dummy"
# roadpush_ai를 import하는 테스트가 AI 저장소에 캐시 폴더(work/)를 만들지 않게 모델·캐시 위치를 임시 폴더로
# (코드 위치는 tests/test_roadpush_ai.py가 sys.path에 넣는다)
os.environ.setdefault("ROADPUSH_AI_ROOT", str(_tmp / "roadpush_ai_root"))
os.environ["RUN_WORKER_IN_API"] = "false"
os.environ["NODELINK_PATH"] = str(Path(__file__).resolve().parents[1] / "data/roadpush_seongnam_nodelink.geojson")
