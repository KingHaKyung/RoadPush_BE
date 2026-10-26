"""
roadpush_ai 엔진 연결 (RoadPush_AI 통합 파이프라인 RoadPushAnalyzer).
- 7-B 결과 → 백엔드 판정 규칙 (AI 계약의 소비 규칙)
- AI 접점 규칙(roadpush_ai/attribution.py) → 원인 배지
- 파이프라인: 실제 AI 코드(Phase 6 → 7-A → 7-B → 접점 판정)를 합성 semantic 지도로 실행
  (Mask2Former·YOLO 모델만 가짜. AI 저장소가 옆 폴더에 없으면 건너뜀)
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.ai_engine.passability.phase7 import provisional_estimate, to_result
from app.ai_engine.passability.risk import risk_score
from app.config import get_settings

AI_ROOT = Path(__file__).resolve().parents[2] / "RoadPush_AI"
AI_SRC = (AI_ROOT / "src", AI_ROOT / "surface_passability" / "src")


def _record(**kw) -> dict:
    """phase7_b_result_v2 최소 형태 (authoritative · relative_only 기본)"""
    r = {
        "schema_version": "phase7_b_result_v2",
        "selected_surface": "sidewalk",
        "phase7a_status": "selected",
        "result_authority": "authoritative",
        "measurement_scope": "full",
        "measurement_confidence": "reliable",
        "human_review_required": False,
        "abstention_reasons": [],
        "minimum_passable_ratio": 0.7,
        "raw_minimum_passable_ratio": 0.1,
        "absolute_passable_width_m": None,
        "agent_width_m": None,
        "required_safe_width_m": None,
        "passability_state": "relative_only",
        "bottleneck": {"cause": "recognized_obstacle"},
        "flags": [],
    }
    r.update(kw)
    return r


# ── 7-B → 판정 (AI docs/PRODUCTION_SYSTEM.md 10~12장)


def test_relative_only_uses_backend_ratio_policy_not_raw_minimum():
    assert to_result(_record(minimum_passable_ratio=0.7)).verdict == "passable"
    assert to_result(_record(minimum_passable_ratio=0.45)).verdict == "narrowed"
    res = to_result(_record(minimum_passable_ratio=0.2))
    assert res.verdict == "blocked" and res.occupancy == 80 and res.bottleneck_ratio == 0.2
    # raw_minimum_passable_ratio(0.1)로 상태를 정하지 않는다
    assert to_result(_record(minimum_passable_ratio=0.65, raw_minimum_passable_ratio=0.01)).verdict == "passable"


def test_ai_physical_state_wins_when_widths_were_given():
    rec = _record(
        minimum_passable_ratio=0.5,
        passability_state="passable",
        absolute_passable_width_m=1.2,
        agent_width_m=0.9,
        required_safe_width_m=1.08,
    )
    res = to_result(rec)
    assert res.verdict == "passable"  # 상대폭 기준(0.6)이면 좁아짐이지만 AI 실폭 판정을 따른다
    assert res.bottleneck_m_est == 1.2
    assert "1.08m" in res.reasons[0]


def test_provisional_review_is_never_a_verdict_or_risk():
    rec = _record(
        phase7a_status="ambiguous_hold",
        result_authority="provisional_review",
        measurement_scope="provisional",
        human_review_required=True,
        minimum_passable_ratio=0.2,
    )
    res = to_result(rec)
    assert res.verdict == "undetermined" and res.uncertainty_code == "boundary_review"
    assert res.bottleneck_ratio is None and res.occupancy is None
    assert risk_score(res)[0] is None  # 집계·우선순위에 넣지 않는다
    assert any("검토용 가계산" in r and "20%" in r for r in res.reasons)
    # 화면에는 '추정 (검토 필요)'로만 보인다: 확정 판정이 아님
    assert provisional_estimate(res.stats) == {"passability": "blocked", "bottleneck_ratio": 0.2, "occupancy": 80}
    assert provisional_estimate(to_result(_record(minimum_passable_ratio=0.9)).stats) is None


def test_typed_null_maps_abstention_reason_without_zero():
    res = to_result(
        _record(
            phase7a_status="failed",
            result_authority="none",
            measurement_scope="none",
            measurement_confidence="insufficient",
            abstention_reasons=["phase7a_failed"],
            minimum_passable_ratio=None,
            passability_state="undetermined",
        )
    )
    assert res.verdict == "undetermined" and res.uncertainty_code == "boundary_failed"
    assert res.bottleneck_ratio is None  # null을 0으로 바꾸지 않는다
    assert (
        to_result(_record(selected_surface=None, minimum_passable_ratio=None)).uncertainty_code == "surface_uncertain"
    )


def test_zero_ratio_is_blocked_and_partial_scope_is_noted():
    res = to_result(_record(minimum_passable_ratio=0.0, passability_state="blocked"))
    assert res.verdict == "blocked" and res.occupancy == 100
    res = to_result(_record(measurement_scope="partial", human_review_required=True))
    assert res.verdict == "passable" and any("앞부분만" in r for r in res.reasons)


# ── AI 접점 규칙 → 원인 배지


@pytest.fixture
def ai_code():
    if not (AI_ROOT / "src" / "roadpush_ai" / "analyzer.py").is_file():
        pytest.skip("RoadPush_AI 저장소가 옆 폴더에 없음")
    pytest.importorskip("scipy")
    # conftest가 ROADPUSH_AI_ROOT(모델·캐시 위치)를 임시 폴더로 돌려 두었으므로 코드 위치만 직접 넣는다
    for src in AI_SRC:
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
    from app.ai_engine import roadpush

    roadpush.prepare_runtime()
    return roadpush


def _detection(i: int, cls: str, kind: str, box: tuple[float, float, float, float]):
    from roadpush_ai.obstacle import ObstacleDetection

    return ObstacleDetection(i, cls, cls, kind, "", 0.9, box)


def test_ai_attribution_maps_to_cause_badges(ai_code):
    from roadpush_ai.attribution import attribute_detections

    from app.ai_engine import causes

    blocker = np.zeros((100, 100), bool)
    blocker[60:80, 10:30] = True  # 차량 바닥
    blocker[60:70, 60:90] = True  # 클래스 없는 물체
    dets = (
        _detection(0, "vehicle", "C", (8, 20, 32, 80)),
        _detection(1, "micromobility", "A", (0, 0, 5, 5)),  # 길을 막지 않는 탐지
    )
    res = attribute_detections(blocker, dets, original_size=(100, 100), analysis_size=(100, 100))
    assert res.status == "partially_attributed" and res.matched_detection_ids == (0,)
    comps = list(res.components)
    assert causes.badges(comps) == ["차량"]
    assert causes.badges([c for c in comps if not c["causes"]]) == ["장애물"]
    assert causes.badges([]) == []


# ── 파이프라인: 실제 AI 코드 + 합성 semantic 지도 → 판정·overlay·근거


def _synthetic_labels(w: int, h: int, obstacle: bool = True) -> np.ndarray:
    """사다리꼴 보도 + 가운데 차량 (Mapillary 라벨: 15 sidewalk, 17 building, 27 sky, 55 car)"""
    top = int(h * 0.23)
    labels = np.full((h, w), 17, np.uint8)
    labels[:top] = 27
    for y in range(top, h):
        t = (y - top) / (h - top)
        labels[y, int(w * (0.42 - 0.30 * t)) : int(w * (0.58 + 0.30 * t))] = 15
    if obstacle:
        car = np.zeros((h, w), bool)
        car[int(h * 0.63) : int(h * 0.8), int(w * 0.29) : int(w * 0.47)] = True
        labels[car & (labels == 15)] = 55
    return labels


CAR_BOX = (0.27, 0.55, 0.49, 0.82)  # 원본 대비 비율 (x0, y0, x1, y1)


class FakeSemantic:
    """Mask2Former 대신 합성 지도를 낸다 (AI SemanticEvidence 계약 그대로)"""

    def __init__(self, obstacle: bool = True):
        self.obstacle = obstacle

    def provide(self, prepared):
        from surface_passability.contracts.semantic import SemanticEvidence, SemanticModelIdentity

        w, h = prepared.analysis_size
        z = "0" * 64
        return SemanticEvidence(
            class_map=_synthetic_labels(w, h, self.obstacle),
            native_size=prepared.original_size,
            analysis_size=(w, h),
            native_array_sha256=z,
            analysis_array_sha256=z,
            cache_key="synthetic",
            cache_path=Path("synthetic.png"),
            cache_file_sha256=z,
            cache_hit=True,
            inference_run=False,
            inference_metadata=None,
            cache_reuse_policy="synthetic",
            requested_device="cpu",
            producer_provenance={},
            model=SemanticModelIdentity("synthetic", "synthetic", z, z, z, z, z, 65),
        )


class NoVehicleModel:
    def get_model(self):
        raise AssertionError("합성 장면에서는 차량 instance 모델이 필요 없어야 한다")


class FakeDetector:
    """장애물 YOLO11s 대신 차량 박스 1개 (+ 길 밖 PM 1개)"""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def detect(self, native_rgb):
        from roadpush_ai.obstacle import ObstacleDetectionResult

        if self.fail:
            raise RuntimeError("CUDA out of memory")
        h, w = native_rgb.shape[:2]
        x0, y0, x1, y1 = CAR_BOX
        dets = (
            _detection(0, "vehicle", "C", (x0 * w, y0 * h, x1 * w, y1 * h)),
            _detection(1, "micromobility", "A", (0.0, 0.0, w * 0.05, h * 0.05)),
        )
        return ObstacleDetectionResult(w, h, "fake_obstacle_v0", "f" * 64, "cpu", 1.0, dets)


@pytest.fixture
def fake_models(ai_code, monkeypatch):
    """roadpush_ai 엔진 모드. 모델(Mask2Former·YOLO)만 가짜로 바꾸고 AI 코드는 실제로 돈다. 요청을 모은다"""
    from roadpush_ai import RoadPushAnalyzer
    from surface_passability import PipelineDependencies, SurfacePassabilityAnalyzer

    roadpush = ai_code
    calls: list = []

    def install(obstacle: bool = True, fail: bool = False):
        deps = PipelineDependencies(
            semantic_provider=FakeSemantic(obstacle),
            vehicle_model_provider=NoVehicleModel(),
            class_names={i: f"class_{i}" for i in range(65)},
        )
        surface = SurfacePassabilityAnalyzer(dependencies=deps)
        original = surface.analyze_photo_with_evidence

        def recording(request):
            calls.append(request)
            return original(request)

        surface.analyze_photo_with_evidence = recording
        capture = roadpush._CapturingSurface(surface)
        monkeypatch.setattr(roadpush, "_capture", capture)
        monkeypatch.setattr(
            roadpush, "_analyzer", RoadPushAnalyzer(surface_analyzer=capture, obstacle_detector=FakeDetector(fail))
        )

    install()
    monkeypatch.setattr(get_settings(), "analysis_engine", "roadpush_ai")
    return calls, install


def _photo(tmp_path: Path, w: int = 350, h: int = 263) -> Path:
    photo = tmp_path / "photo.png"
    cv2.imwrite(str(photo), np.full((h, w, 3), 128, np.uint8))
    return photo


def test_pipeline_roadpush_engine_end_to_end(tmp_path, fake_models):
    from app.ai_engine import pipeline

    calls, _ = fake_models
    out = pipeline.analyze(_photo(tmp_path), "image", work_dir=tmp_path / "w", storage_root=tmp_path, sample_id="obs_1")
    rec = out.evidence["phase7b"]
    assert rec["schema_version"] == "phase7_b_result_v2" and rec["result_authority"] == "authoritative"
    assert 0 < rec["minimum_passable_ratio"] < 1  # 가운데 차량이 통로를 줄인다
    assert out.result.verdict in ("passable", "narrowed", "blocked")
    assert out.result.bottleneck_ratio == round(rec["minimum_passable_ratio"], 3)
    assert out.risk is not None
    # 원인: 선택 보도의 blocker와 겹친 차량만. 길 밖 PM은 진단(all_detections)에만 남는다
    assert out.causes == ["차량"]
    cause = out.evidence["cause"]
    assert cause["types"] == ["C"] and cause["auto_routable"] is True
    assert [o["class"] for o in cause["obstacles"]] == ["vehicle"]
    # AI 원본 응답 전체를 보관한다 (surface · surface_obstacles · diagnostics)
    resp = out.evidence["response"]
    assert resp["schema_version"] == "roadpush_analysis_response_v1" and resp["status"] == "complete"
    assert set(resp) >= {"surface", "surface_obstacles", "diagnostics"}
    assert resp["surface"]["passability"] == rec
    assert len(resp["diagnostics"]["all_detections"]) == 2  # 사진 전체 탐지 (길 밖 PM 포함)
    assert out.rule_version.startswith("phase7_b_result_v2/")
    assert set(out.images) == {"class_map", "surface_overlay", "obstacle_overlay"}
    assert out.images["obstacle_overlay"].shape == (263, 350, 3)  # 분석 크기
    assert calls[0].input_profile == "image_upload" and calls[0].surface_width_m is None
    assert out.evidence["engine"] == "roadpush_ai" and len(out.evidence["idempotency_key"]) == 64
    text = json.dumps(out.evidence, allow_nan=False)  # analyses.evidence(JSON)에 그대로 저장된다
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "\\\\") not in text  # 로컬 경로를 내지 않는다


def test_pipeline_without_obstacle_is_wider(tmp_path, fake_models):
    from app.ai_engine import pipeline

    _, install = fake_models
    blocked = pipeline.analyze(_photo(tmp_path), "image", work_dir=tmp_path / "a", storage_root=tmp_path, sample_id="a")
    install(obstacle=False)
    clear = pipeline.analyze(_photo(tmp_path), "image", work_dir=tmp_path / "b", storage_root=tmp_path, sample_id="b")
    assert clear.evidence["phase7b"]["minimum_passable_ratio"] > blocked.evidence["phase7b"]["minimum_passable_ratio"]
    assert clear.causes == [] and clear.evidence["cause"]["status"] == "no_blocker"


def test_obstacle_stage_failure_is_a_runtime_error(tmp_path, fake_models):
    from app.ai_engine import pipeline

    _, install = fake_models
    install(fail=True)  # AI는 status=partial을 낸다 → 원인 없는 결과를 저장하지 않고 재시도 대상으로
    with pytest.raises(RuntimeError, match="장애물 원인 단계 실패"):
        pipeline.analyze(_photo(tmp_path), "image", work_dir=tmp_path / "w", storage_root=tmp_path, sample_id="x")


def test_missing_models_raise_model_missing(ai_code, monkeypatch):
    monkeypatch.setattr(ai_code, "_analyzer", None)
    # conftest: ROADPUSH_AI_ROOT는 빈 임시 폴더 → models/가 없다
    with pytest.raises(ai_code.ModelMissing, match="semantic"):
        ai_code.get_analyzer()


def test_worker_saves_result_through_api(fake_models):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import worker

    calls, _ = fake_models
    api = "/api/v1"
    jpg = cv2.imencode(".jpg", np.full((360, 480, 3), 128, np.uint8))[1].tobytes()
    with TestClient(app) as c:
        site = c.post(
            f"{api}/sites", json={"name": "7-B 확인 구간", "lat": 37.3868, "lng": 127.1273, "sidewalk_width_m": 2.4}
        )
        data = {"captured_at": "2026-10-26T10:00:00+09:00", "lat": 37.3868, "lng": 127.1273, "source": "cctv"}
        data["site_id"] = site.json()["id"]
        oid = c.post(f"{api}/observations", data=data, files={"file": ("p.jpg", jpg, "image/jpeg")}).json()[
            "observation_id"
        ]
        while worker.run_once():
            pass
        req = calls[-1]
        assert req.sample_id == oid and req.input_profile == "cctv"
        assert req.surface_width_m == 2.4  # 구간 보도 폭 → 7-B 실폭

        got = c.get(f"{api}/observations/{oid}").json()
        assert got["processing_status"] == "completed", got
        assert got["passability"] in ("passable", "narrowed", "blocked")
        assert got["bottleneck_m_est"] is not None and got["confidence"] is None
        assert got["images"]["obstacle_overlay"]
        ana = c.get(f"{api}/observations/{oid}/analyses").json()[-1]
        assert ana["evidence"]["phase7b"]["schema_version"] == "phase7_b_result_v2"
        assert ana["evidence"]["phase7b"]["surface_width_m"] == 2.4
        assert ":\\" not in json.dumps(ana["evidence"])  # 로컬 절대 경로(C:\…)를 응답에 내지 않는다


def test_unknown_engine_is_rejected(tmp_path, monkeypatch):
    from app.ai_engine import pipeline

    monkeypatch.setattr(get_settings(), "analysis_engine", "selectstar")
    with pytest.raises(ValueError, match="ANALYSIS_ENGINE"):
        pipeline.analyze(tmp_path / "x.jpg", "image", work_dir=tmp_path, storage_root=tmp_path, sample_id="x")
