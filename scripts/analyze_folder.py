"""
폴더 사진 일괄 분석 + (선택) 현장 판정과 비교. 임계값 보정(3단계)과 최종 평가에 쓴다. DB·서버 없이 AI 모듈만 돌린다.

    python scripts/analyze_folder.py data/field/calib --labels data/field/calib/labels.csv --out outputs/calib_v1
    python scripts/analyze_folder.py data/field/calib --pass-ratio 0.55 --block-ratio 0.25   # 기준값 바꿔 보기

labels.csv (UTF-8) 열: file, label, sidewalk_width_m(선택)
    label: passable | narrowed | blocked | undetermined  (현장에서 사람이 판단한 값. AI에 맞춰 고치지 않는다)

결과: <out>/results.csv, <out>/confusion.csv, <out>/overlays/*.jpg
보정용 장소와 최종 평가 장소는 폴더를 나눠 따로 돌린다 (마스터 문서 11장).
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_engine import pipeline  # noqa: E402
from app.ai_engine.passability.rules import Policy  # noqa: E402

CLASSES = ["passable", "narrowed", "blocked", "undetermined"]
EXT = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--out", type=Path, default=Path("outputs/analyze_folder"))
    ap.add_argument("--pass-ratio", type=float)
    ap.add_argument("--block-ratio", type=float)
    ap.add_argument("--footprint", type=float, help="장애물 실루엣 중 점유로 볼 아래쪽 비율 (기본 1.0)")
    args = ap.parse_args()

    policy = Policy()
    if args.pass_ratio is not None:
        policy.passable_min_ratio = args.pass_ratio
    if args.block_ratio is not None:
        policy.blocked_below_ratio = args.block_ratio
    if args.footprint is not None:
        policy.footprint_frac = args.footprint
    p = policy
    p.version += f"(pass={p.passable_min_ratio},block={p.blocked_below_ratio},fp={p.footprint_frac})"

    labels: dict[str, dict] = {}
    if args.labels:
        with args.labels.open(encoding="utf-8-sig") as f:
            labels = {row["file"]: row for row in csv.DictReader(f)}

    (args.out / "overlays").mkdir(parents=True, exist_ok=True)
    rows, conf = [], Counter()
    for p in sorted(x for x in args.folder.iterdir() if x.suffix.lower() in EXT):
        lab = labels.get(p.name, {})
        width = float(lab["sidewalk_width_m"]) if lab.get("sidewalk_width_m") else None
        try:
            out = pipeline.run(pipeline.read_frames(str(p), "image"), reference_width_m=width, policy=policy)
        except pipeline.InputError as e:
            print(f"[skip] {p.name}: {e}")
            continue
        r = out.result
        if "obstacle_overlay" in out.images:
            cv2.imwrite(str(args.out / "overlays" / f"{p.stem}.jpg"), out.images["obstacle_overlay"])
        gt = lab.get("label")
        if gt:
            conf[(gt, r.verdict)] += 1
        rows.append(
            {
                "file": p.name,
                "ai": r.verdict,
                "field": gt or "",
                "bottleneck_ratio": r.bottleneck_ratio,
                "bottleneck_m_est": r.bottleneck_m_est,
                "risk": out.risk,
                "confidence": r.confidence,
                "uncertainty": r.uncertainty_code or "",
                "reason": r.reasons[0] if r.reasons else "",
            }
        )
        print(f"{p.name}: AI={r.verdict} field={gt or '-'} ratio={r.bottleneck_ratio}")

    if not rows:
        print("분석한 사진이 없습니다")
        return
    with (args.out / "results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    if conf:
        with (args.out / "confusion.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["현장 \\ AI", *CLASSES])
            for g in CLASSES:
                w.writerow([g, *(conf[(g, a)] for a in CLASSES)])
        n = sum(conf.values())
        agree = sum(conf[(c, c)] for c in CLASSES)
        missed = conf[("blocked", "passable")] + conf[("narrowed", "passable")]
        print(f"\n일치 {agree}/{n} · 단절·좁아짐을 통과로 놓침 {missed} · 정책 {policy.version}")
    print(f"결과: {args.out}")


if __name__ == "__main__":
    main()
