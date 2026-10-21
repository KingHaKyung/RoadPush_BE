import os
import tempfile
from pathlib import Path

# 앱을 불러오기 전에 임시 DB·저장소를 지정한다
_tmp = Path(tempfile.mkdtemp(prefix="roadpush-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_tmp / "storage")
os.environ["SURFACE_MODEL"] = "dummy"
os.environ["RUN_WORKER_IN_API"] = "false"
os.environ["NODELINK_PATH"] = str(Path(__file__).resolve().parents[1] / "data/roadpush_seongnam_nodelink.geojson")
