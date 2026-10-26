"""분석 1건 실행: DB·파일 저장소에서 입력을 꺼내 AI 모듈에 넘기고, 결과를 새 analysis 행과 파일로 저장한다"""

from ..ai_engine import pipeline
from ..db.models import Analysis, Observation
from .storage import LocalStorage


def run_analysis(storage: LocalStorage, ana: Analysis, obs: Observation) -> None:
    mask_key = f"observations/{obs.id}/mask.png"
    mask = storage.read_image(mask_key) if obs.media_type == "image" and storage.path(mask_key).exists() else None

    base = f"analyses/{ana.id}"
    out = pipeline.analyze(
        storage.path(obs.original_key),
        obs.media_type,
        # AI 중간 마스크. 자동 재시도마다 새 폴더 (AI 계약: 기존 결과 폴더를 덮어쓰지 않는다)
        work_dir=storage.path(f"{base}/ai/attempt-{ana.attempts}"),
        # semantic 캐시는 관측 폴더에 둔다 → 재분석이 빠르고, 관측을 지우면 같이 지워진다
        cache_dir=storage.path(f"observations/{obs.id}/semantic"),
        storage_root=storage.root,
        sample_id=obs.id,
        reference_width_m=obs.site.sidewalk_width_m,
        input_profile="cctv" if obs.source == "cctv" else "image_upload",
        precomputed_mask=mask,
    )
    res = out.result

    ext = {"class_map": "png", "surface_overlay": "jpg", "obstacle_overlay": "jpg"}
    ana.files = {k: storage.put_image(f"{base}/{k}.{ext[k]}", img) for k, img in out.images.items()}
    ana.model_version = out.model_version
    ana.rule_version = out.rule_version
    ana.passability = res.verdict
    ana.risk_score = out.risk
    ana.occupancy = res.occupancy
    ana.bottleneck_ratio = res.bottleneck_ratio
    ana.bottleneck_m_est = res.bottleneck_m_est
    ana.confidence = res.confidence
    ana.uncertainty_code = res.uncertainty_code
    ana.uncertainty_reason = res.reasons[0] if res.verdict == "undetermined" else None
    ana.causes = out.causes
    ana.evidence = {
        "reasons": res.reasons,
        "stats": res.stats,
        "risk": out.risk_detail,
        "quality": out.quality,
        "frames": out.frames,
        **out.evidence,
    }
