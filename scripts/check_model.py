"""
모델을 넣은 뒤 서버를 켜기 전에 점검한다. DB·서버 없이 AI 모듈만 돌린다.

    python scripts/check_model.py                        # .env 설정 그대로, 가짜 이미지로
    python scripts/check_model.py 사진.jpg               # 실제 사진으로
    python scripts/check_model.py 사진.jpg --obstacle-weights model_weights/team_v1.pt   # .env를 고치기 전에 시험

확인하는 것
1. torch·CUDA (DEVICE=cuda인데 GPU를 못 쓰면 알려 준다)
2. 가중치 파일이 있는지
3. 장애물 모델 클래스 이름 → 원인 배지 (labels.CAUSE_OF). 매핑이 없는 이름은 분석에서 버려지므로 여기서 먼저 확인
   segmentation(-seg)인지 detection(박스만)인지도 표시한다
4. 사진 한 장 분석 결과·걸린 시간 → outputs/check_model/ 에 overlay 저장
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", type=Path, help="분석할 사진 (없으면 가짜 이미지)")
    ap.add_argument("--surface", help="SURFACE_MODEL 대신 (dummy | selectstar | hf)")
    ap.add_argument("--surface-weights", help="SURFACE_WEIGHTS 대신")
    ap.add_argument("--obstacle-weights", nargs="+", help="OBSTACLE_WEIGHTS 대신 (주면 OBSTACLE_MODEL=yolo)")
    ap.add_argument("--device", help="DEVICE 대신 (cuda | cpu)")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs/check_model")
    args = ap.parse_args()

    # 설정은 환경변수가 .env보다 앞선다 → app을 불러오기 전에 넣는다
    if args.surface:
        os.environ["SURFACE_MODEL"] = args.surface
    if args.surface_weights:
        os.environ["SURFACE_WEIGHTS"] = args.surface_weights
    if args.obstacle_weights:
        os.environ["OBSTACLE_MODEL"] = "yolo"
        os.environ["OBSTACLE_WEIGHTS"] = json.dumps(args.obstacle_weights)
    if args.device:
        os.environ["DEVICE"] = args.device

    import cv2
    import numpy as np

    from app.ai_engine import imageio, pipeline
    from app.ai_engine.labels import CAUSE_OF, PERSON_NAMES
    from app.ai_engine.obstacles import load_obstacles
    from app.config import get_settings

    s = get_settings()
    print("── 1. 실행 환경")
    try:
        import torch

        cuda = torch.cuda.is_available()
        gpu = torch.cuda.get_device_name(0) if cuda else "없음"
        print(f"torch {torch.__version__} · CUDA {torch.version.cuda} · GPU {gpu}")
        if s.device.startswith("cuda") and not cuda:
            sys.exit("[X] DEVICE=cuda인데 GPU를 쓸 수 없습니다 → docs/SETUP.md 8-1 (CPU용 torch가 깔렸을 수 있음)")
    except ImportError:
        if s.surface_model != "dummy" or s.obstacle_model != "none":
            sys.exit('[X] torch가 없습니다 → docs/SETUP.md 8-1, pip install -e ".[ml]"')
        print("torch 없음 (dummy 설정이라 괜찮음)")

    print("\n── 2. 설정·가중치")
    print(f"SURFACE_MODEL={s.surface_model}  SURFACE_WEIGHTS={s.surface_weights}")
    print(f"OBSTACLE_MODEL={s.obstacle_model}  OBSTACLE_WEIGHTS={s.obstacle_weights}")
    local = [s.surface_weights] if s.surface_model == "selectstar" else []
    local += [w.split("|")[0] for w in s.obstacle_weights] if s.obstacle_model == "yolo" else []
    for w in local:
        p = Path(w) if Path(w).is_absolute() else ROOT / w
        print(f"{'[OK]' if p.exists() else '[!] 없음'} {w}")

    print("\n── 3. 장애물 클래스 → 원인 배지")
    obstacles = load_obstacles(s.obstacle_model, tuple(s.obstacle_weights), s.device)
    if obstacles is None:
        print("장애물 모델 없음 (노면 모델만. dummy는 가짜 장애물을 직접 그린다)")
    else:
        for spec, m in zip(s.obstacle_weights, obstacles.models, strict=True):
            kind = "segmentation" if m.task == "segment" else f"{m.task} → 박스를 영역으로 사용 (점유율 과대)"
            print(f"{spec.split('|')[0]}  [{kind}]")
            unknown = []
            for name in m.names.values():
                n = str(name).strip().lower()
                if n in CAUSE_OF:
                    print(f"   {name:<20} → {CAUSE_OF[n]}")
                elif n in PERSON_NAMES:
                    print(f"   {name:<20} → 이동체 (장애물 아님)")
                else:
                    unknown.append(str(name))
            if unknown:
                more = f" 외 {len(unknown) - 15}개" if len(unknown) > 15 else ""
                print(f"   버림 {len(unknown)}개: {', '.join(unknown[:15])}{more}")
                print("   ※ 장애물인데 버려지는 이름이 있으면 app/ai_engine/labels.py CAUSE_OF에 추가")

    print("\n── 4. 분석")
    if args.image:
        img = imageio.imread(str(args.image))
        if img is None:
            sys.exit(f"[X] 사진을 읽지 못했습니다: {args.image}")
        stem = args.image.stem
    else:
        img = np.random.default_rng(0).integers(60, 200, (720, 960, 3), dtype=np.uint8)
        stem = "synthetic"
    pipeline.run([img])  # 첫 실행은 모델 올리기·CUDA 준비가 섞여 느리다
    t = time.perf_counter()
    out = pipeline.run([img])
    dt = time.perf_counter() - t
    r = out.result
    print(f"판정 {r.verdict} · 위험도 {out.risk} · 원인 {out.causes or '없음'} · 유보 {r.uncertainty_code or '-'}")
    print(f"모델 {out.model_version} · 규칙 {out.rule_version} · {dt:.2f}초/장")
    for reason in r.reasons[:5]:
        print(f"   - {reason}")
    args.out.mkdir(parents=True, exist_ok=True)
    for k in ("surface_overlay", "obstacle_overlay"):
        if k in out.images:
            cv2.imwrite(str(args.out / f"{stem}_{k}.jpg"), out.images[k])
    print(f"overlay → {args.out}")


if __name__ == "__main__":
    main()
