"""
실행 중인 코드 버전 (/health). 어떤 AI 코드로 분석했는지 재현할 수 있게 커밋 번호를 보여 준다.
백엔드·AI 저장소 폴더의 git에서 읽고, 서버를 다시 띄울 때까지 한 번만 읽는다.
"""

import subprocess
from functools import lru_cache
from pathlib import Path

from ..config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _git(path: Path) -> dict:
    def run(*args: str) -> str:
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=5, check=True)
        return out.stdout.strip()

    if not (path / ".git").exists():
        return {"commit": None, "error": f"git 저장소가 아님: {path}"}
    try:
        return {
            "commit": run("rev-parse", "HEAD"),
            # 추적 중인 파일이 커밋과 다르면 true (모델 등 Git 제외 파일은 보지 않음)
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no")),
        }
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "error": f"git을 읽지 못함: {path}"}


@lru_cache
def backend_version() -> dict:
    return _git(BACKEND_ROOT)


@lru_cache
def ai_version() -> dict:
    root = get_settings().roadpush_ai_root.resolve()
    if not root.exists():
        return {"commit": None, "error": f"ROADPUSH_AI_ROOT 폴더가 없음: {root}"}
    return _git(root)
