"""분석 1건 실행: DB·파일 저장소에서 입력을 꺼내 AI 모듈에 넘기고, 결과를 새 analysis 행과 파일로 저장한다"""

from ..ai_engine import pipeline
from ..db.models import Analysis, Observation
from .storage import LocalStorage


def run_analysis(storage: LocalStorage, ana: Analysis, obs: Observation) -> None:
    frames = pipeline.read_frames(str(storage.path(obs.original_key)), obs.media_type)
    mask_key = f"observations/{obs.id}/mask.png"
    mask = storage.read_image(mask_key) if obs.media_type == "image" and storage.path(mask_key).exists() else None

    out = pipeline.run(frames, reference_width_m=obs.site.sidewalk_width_m, precomputed_mask=mask)
    res = out.result

    base = f"analyses/{ana.id}"
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
        "frames": len(frames),
    }
