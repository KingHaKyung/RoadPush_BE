"""
실행 중인 코드 버전 (/health). 어떤 AI 코드로 분석했는지 재현할 수 있게 커밋 번호를 보여 준다.

- 개발 PC: 저장소 폴더에 .git이 있으면 git으로 읽는다
- 배포 서버: AI 저장소는 clone하지 않고 묶어서 보내므로(.git 없음) 배포 스크립트가 넣은 VERSION.json을 읽는다
서버를 다시 띄울 때까지 한 번만 읽는다 (배포 스크립트가 서비스를 재시작한다).
"""

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from ..config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = "VERSION.json"


def _git(path: Path) -> dict | None:
    def run(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=5, check=True)
        return out.stdout.strip()

    if not (path / ".git").exists():
        return None
    try:
        return {
            "commit": run("rev-parse", "HEAD"),
            # 추적 중인 파일이 커밋과 다르면 true (모델 등 Git 제외 파일은 보지 않음)
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no")),
            "source": "git",
        }
    except (OSError, subprocess.SubprocessError):
        return None


def _version_file(path: Path) -> dict | None:
    try:
        data = json.loads((path / VERSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {**data, "source": "package"}


def _read(path: Path) -> dict:
    return _git(path) or _version_file(path) or {"commit": None, "source": None}


@lru_cache
def backend_version() -> dict:
    return _read(BACKEND_ROOT)


@lru_cache
def ai_version() -> dict:
    root = get_settings().roadpush_ai_root.resolve()
    if not root.exists():
        return {"commit": None, "source": None, "error": f"ROADPUSH_AI_ROOT 폴더가 없음: {root}"}
    return _read(root)
