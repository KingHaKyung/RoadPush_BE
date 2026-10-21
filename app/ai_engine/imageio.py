"""
이미지·영상 파일 입출력. Windows에서 cv2.imread/imwrite/VideoCapture는 경로에 한글이 있으면
(예: C:\\Users\\홍길동\\…, 바탕 화면) 파일을 열지 못하므로 바이트로 읽고 쓴 뒤 디코딩·인코딩한다.
"""

import os
from pathlib import Path

import cv2
import numpy as np


def imread(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags) if data.size else None


def imwrite(path: str | Path, img: np.ndarray) -> bool:
    ok, buf = cv2.imencode(Path(path).suffix or ".png", img)
    if ok:
        buf.tofile(str(path))
    return bool(ok)


def video_path(path: str | Path) -> str:
    """VideoCapture는 바이트로 열 수 없으므로, 가능하면 한글이 없는 상대 경로로 바꿔 넘긴다"""
    p = str(path)
    if p.isascii():
        return p
    try:
        rel = os.path.relpath(p)
    except ValueError:  # 다른 드라이브
        return p
    return rel if rel.isascii() else p
