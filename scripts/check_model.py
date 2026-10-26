"""
AI 팀 모델을 넣은 뒤 서버를 켜기 전에 점검한다. DB·서버 없이 AI 모듈만 돌린다.

    python scripts/check_model.py                 # 설치·모델 파일만 점검
    python scripts/check_model.py 사진.jpg        # 실제 사진 1장을 Phase 6 → 7-A → 7-B → 원인까지 분석

확인하는 것
1. Python·torch·CUDA (roadpush_ai는 Python 3.12)
2. RoadPush_AI 위치와 모델 파일 (ROADPUSH_AI_ROOT/models, OBSTACLE_YOLO_ROOT/weights + SHA-256)
3. 장애물 모델 클래스 → 원인 배지 (labels.CAUSE_OF). 매핑이 없는 클래스는 배지가 나오지 않는다
4. 사진 한 장 분석 결과·걸린 시간 → outputs/check_model/<사진>/ 에 overlay·7-B 기록 저장
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", type=Path, help="분석할 사진 (없으면 설치·모델 파일만 점검)")
    ap.add_argument("--surface-width", type=float, help="보도 실폭(m). 주면 7-B가 m 단위 폭도 계산")
    ap.add_argument("--cctv", action="store_true", help="CCTV 입력 프로필로 분석 (기본: image_upload)")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs/check_model")
    args = ap.parse_args()
    os.environ["ANALYSIS_ENGINE"] = "roadpush_ai"  # 이 스크립트는 AI 팀 엔진을 점검한다

    from app.ai_engine import causes, imageio, pipeline, roadpush
    from app.ai_engine.labels import cause_of
    from app.config import get_settings

    s = get_settings()
    print("── 1. 실행 환경")
    print(f"Python {sys.version.split()[0]}")
    if sys.version_info[:2] != (3, 12):
        print("[!] roadpush_ai는 Python 3.12 기준입니다 (AI 저장소 pyproject.toml). 다른 버전은 설치가 안 될 수 있음")
    try:
        import torch

        cuda = torch.cuda.is_available()
        gpu = torch.cuda.get_device_name(0) if cuda else "없음"
        print(f"torch {torch.__version__} · CUDA {torch.version.cuda} · GPU {gpu}")
        device = roadpush.resolve_device(s.device)
        print(f"SEMANTIC_DEVICE={s.semantic_device} (AI 팀 기준 cpu) · 원인 모델 DEVICE={s.device} → {device}")
        if s.semantic_device.startswith("cuda") and not cuda:
            sys.exit("[X] SEMANTIC_DEVICE=cuda인데 GPU를 쓸 수 없습니다")
    except ImportError:
        sys.exit('[X] torch가 없습니다 → docs/SETUP.md 8장 (pip install -e ".[ai]")')

    print("\n── 2. RoadPush_AI 위치·모델")
    print(f"ROADPUSH_AI_ROOT={s.roadpush_ai_root.resolve()}")
    try:
        files = roadpush.model_files()
        import roadpush_ai

        print(f"[OK] roadpush_ai {roadpush_ai.__version__} ({Path(roadpush_ai.__file__).parent})")
    except ImportError as e:
        sys.exit(
            f"[X] roadpush_ai를 불러오지 못했습니다: {e} → ROADPUSH_AI_ROOT 또는 pip install -e <AI 폴더> --no-deps"
        )
    missing = False
    for name, p in files.items():
        print(f"{'[OK]' if p.exists() else '[X] 없음'} {name}: {p}")
        missing |= not p.exists()
    if missing:
        print("   → AI 모델 파일(models/)을 ROADPUSH_AI_ROOT 아래에 넣습니다")

    print("\n── 3. 장애물 원인 모델 (OBSTACLE_YOLO_ROOT)")
    try:
        st = causes.load()
    except Exception as e:  # 가중치 없음·SHA-256 불일치
        st = None
        print(f"[X] {e}")
    if st is None and not s.obstacle_yolo_root:
        print("OBSTACLE_YOLO_ROOT가 비어 있음 → 원인 배지 없이 통행 폭만 분석")
    elif st is not None:
        imgsz = st["cfg"]["model"]["imgsz"]
        print(f"[OK] {st['version']} · SHA-256 {st['sha256'][:12]}… · imgsz {imgsz} · {st['device']}")
        for i, name in st["model"].names.items():
            c = st["cls_cfg"].get(name, {})
            badge = cause_of(name)
            print(
                f"   {i} {name:<14} → {badge or '[!] 배지 없음 (labels.CAUSE_OF에 추가)'}"
                f" · 처리 {c.get('type', '?')} {c.get('action', '')} · conf {c.get('conf', '?')}"
            )

    if not args.image:
        return
    if missing:
        sys.exit("\n[X] 모델 파일이 없어 사진 분석을 건너뜁니다")

    print("\n── 4. 사진 분석")
    if imageio.imread(str(args.image)) is None:
        sys.exit(f"[X] 사진을 읽지 못했습니다: {args.image}")
    out_dir = args.out / args.image.stem
    if out_dir.exists():
        shutil.rmtree(out_dir)
    t = time.perf_counter()
    out = pipeline.analyze(
        args.image,
        "image",
        work_dir=out_dir / "ai",
        cache_dir=args.out / "_semantic_cache",
        storage_root=args.out,
        sample_id=args.image.stem,
        reference_width_m=args.surface_width,
        input_profile="cctv" if args.cctv else "image_upload",
    )
    dt = time.perf_counter() - t
    r, rec = out.result, out.evidence["phase7b"]
    print(f"판정 {r.verdict} · 위험도 {out.risk} · 원인 {out.causes or '없음'} · 유보 {r.uncertainty_code or '-'}")
    print(
        f"7-B: surface={rec['selected_surface']} · 7-A={rec['phase7a_status']}"
        f" · authority={rec['result_authority']} · scope={rec['measurement_scope']}"
        f" · ratio={rec['minimum_passable_ratio']} · state={rec['passability_state']}"
    )
    inferred = "함" if out.evidence["semantic_inference_run"] else "캐시"
    print(f"모델 {out.model_version} · 규칙 {out.rule_version} · {dt:.1f}초 (semantic 추론 {inferred})")
    for reason in r.reasons[:5]:
        print(f"   - {reason}")
    for k, img in out.images.items():
        if k != "class_map":
            imageio.imwrite(out_dir / f"{k}.jpg", img)
    (out_dir / "evidence.json").write_text(json.dumps(out.evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"overlay·기록 → {out_dir}")


if __name__ == "__main__":
    main()
