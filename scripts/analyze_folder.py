"""
폴더 사진 일괄 분석 + (선택) 현장 판정과 비교. 임계값 보정(3단계)과 최종 평가에 쓴다. DB·서버 없이 AI 모듈만 돌린다.
.env의 ANALYSIS_ENGINE으로 분석한다 (roadpush_ai = AI 팀 파이프라인, dummy = 흐름 확인용).
--pass-ratio·--block-ratio는 7-B가 relative_only일 때 쓰는 백엔드 상대폭 기준을 바꿔 본다 (passability/phase7.py).

    python scripts/analyze_folder.py data/field/calib --labels data/field/calib/labels.csv --out outputs/calib_v1
    python scripts/analyze_folder.py data/field/calib --pass-ratio 0.55 --block-ratio 0.25   # 기준값 바꿔 보기

labels.csv (UTF-8) 열: file, label, sidewalk_width_m(선택)
    label: passable | narrowed | blocked | undetermined  (현장에서 사람이 판단한 값. AI에 맞춰 고치지 않는다)

결과: <out>/results.csv, <out>/confusion.csv, <out>/overlays/<사진>_surface.jpg · <사진>_passage.jpg
      roadpush_ai: <out>/work/<사진>/ (Phase 6 마스크), <out>/phase7b.jsonl (사진별 7-B 기록)
보정용 장소와 최종 평가 장소는 폴더를 나눠 따로 돌린다 (마스터 문서 11장).
"""

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai_engine import imageio, pipeline  # noqa: E402
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
    ap.add_argument("--footprint", type=float, help="dummy 엔진: 장애물 실루엣 중 점유로 볼 아래쪽 비율 (기본 1.0)")
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
    shutil.rmtree(args.out / "work", ignore_errors=True)  # roadpush_ai는 실행마다 빈 작업 폴더가 필요하다
    phase7b = (args.out / "phase7b.jsonl").open("w", encoding="utf-8")
    rows, conf = [], Counter()
    for p in sorted(x for x in args.folder.iterdir() if x.suffix.lower() in EXT):
        lab = labels.get(p.name, {})
        width = float(lab["sidewalk_width_m"]) if lab.get("sidewalk_width_m") else None
        try:
            out = pipeline.analyze(
                p,
                "image",
                work_dir=args.out / "work" / p.stem,
                cache_dir=args.out / "_semantic_cache",
                storage_root=args.out,
                sample_id=p.stem,
                reference_width_m=width,
                policy=policy,
            )
        except pipeline.InputError as e:
            print(f"[skip] {p.name}: {e}")
            continue
        r = out.result
        if "phase7b" in out.evidence:
            phase7b.write(json.dumps({"file": p.name, **out.evidence["phase7b"]}, ensure_ascii=False) + "\n")
        # 노면 결과는 항상, 통로·점유 결과는 판정이 나온 경우만 (판단 유보면 없음)
        imageio.imwrite(args.out / "overlays" / f"{p.stem}_surface.jpg", out.images["surface_overlay"])
        if "obstacle_overlay" in out.images:
            imageio.imwrite(args.out / "overlays" / f"{p.stem}_passage.jpg", out.images["obstacle_overlay"])
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
                "authority": out.evidence.get("phase7b", {}).get("result_authority", ""),
                "causes": " ".join(out.causes),
                "reason": r.reasons[0] if r.reasons else "",
            }
        )
        print(f"{p.name}: AI={r.verdict} field={gt or '-'} ratio={r.bottleneck_ratio}")

    phase7b.close()
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
