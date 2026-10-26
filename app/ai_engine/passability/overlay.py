"""근거 이미지: 보도·차도·점유 색칠(surface_overlay)과 남은 연속 통로·병목 표시(passage_overlay)"""

import cv2
import numpy as np

from ..labels import L
from .rules import PassabilityResult

# BGR
COLORS = {
    L.SIDEWALK: (90, 200, 90),
    L.ROAD: (150, 110, 60),
    L.CURB: (0, 200, 255),
    L.OBSTACLE: (60, 60, 230),
    L.TRANSIENT: (230, 160, 40),
    L.BIKE_LANE: (200, 80, 200),
    L.UNKNOWN: (128, 128, 128),
}


def surface_overlay(img: np.ndarray, class_map: np.ndarray, alpha: float = 0.45) -> np.ndarray:
    color = np.zeros_like(img)
    for lab, c in COLORS.items():
        color[class_map == lab] = c
    painted = class_map != L.BACKGROUND
    out = img.copy()
    out[painted] = cv2.addWeighted(img, 1 - alpha, color, alpha, 0)[painted]
    return out


def passage_overlay(img: np.ndarray, res: PassabilityResult) -> np.ndarray:
    out = img.copy()
    if res.occupied_mask is not None:
        red = out.copy()
        red[res.occupied_mask] = (40, 40, 220)
        out = cv2.addWeighted(out, 0.5, red, 0.5, 0)
    green = out.copy()
    for r, a, b in res.path:
        if res.transposed:
            green[a:b, r] = (60, 220, 60)
        else:
            green[r, a:b] = (60, 220, 60)
    out = cv2.addWeighted(out, 0.45, green, 0.55, 0)

    row = res.stats.get("bottleneck_row")
    if row is not None:
        seg = next(((a, b) for r, a, b in res.path if r == row), None)
        if seg:
            p1, p2 = ((seg[0], row), (seg[1], row)) if not res.transposed else ((row, seg[0]), (row, seg[1]))
            cv2.line(out, p1, p2, (0, 255, 255), 3)
            cv2.putText(
                out,
                "bottleneck",
                (p1[0], max(12, p1[1] - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )
    return out


def phase7_surface_overlay(img: np.ndarray, surface: np.ndarray | None, blocked: np.ndarray | None) -> np.ndarray:
    """roadpush_ai: 확정된 보행면(초록)과 측정 점유(빨강)"""
    out = img.copy()
    for mask, color, alpha in ((surface, (90, 200, 90), 0.45), (blocked, (40, 40, 220), 0.6)):
        if mask is not None and mask.any():
            painted = cv2.addWeighted(out, 1 - alpha, np.full_like(out, color), alpha, 0)
            out[mask] = painted[mask]
    return out


def phase7_passage_overlay(img: np.ndarray, record: dict) -> np.ndarray:
    """roadpush_ai: 7-B가 고른 연결 통로(초록)와 3행 병목(노랑). 원인 overlay(덩어리·박스) 위에 덧그려도 된다"""
    out = img.copy()
    green = out.copy()
    for node in record.get("selected_corridor_path") or []:
        a, b = node["interval"]
        green[node["y"], a : b + 1] = (60, 220, 60)
    out = cv2.addWeighted(out, 0.45, green, 0.55, 0)
    bottleneck = record.get("bottleneck") or {}
    row = bottleneck.get("center_row", bottleneck.get("row"))
    seg = bottleneck.get("selected_interval")
    if row is not None and seg:
        cv2.line(out, (seg[0], row), (seg[1], row), (0, 255, 255), 3)
        ratio = record.get("minimum_passable_ratio")
        label = "bottleneck" + (f" {ratio:.0%}" if ratio is not None else "")
        cv2.putText(
            out, label, (seg[0], max(12, row - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA
        )
    return out


def image_quality(img: np.ndarray) -> dict:
    """야간·흐림 판단용 간단한 지표. 판정 유보 사유(night)를 정할 때 쓴다"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return {
        "brightness": round(float(gray.mean()), 1),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
        "width": int(img.shape[1]),
        "height": int(img.shape[0]),
    }
