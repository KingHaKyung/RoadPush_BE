import numpy as np

from app.ai_engine.labels import L
from app.ai_engine.passability.risk import risk_score
from app.ai_engine.passability.rules import Policy, analyze_passability
from app.ai_engine.scene import fuse_frames
from app.ai_engine.types import SceneResult

H, W = 200, 200
SW0, SW1 = 63, 160  # 보도 열 범위 [63, 160) = 97px


def scene() -> np.ndarray:
    """차도 | 연석 | 보도 | 건물 (보도가 화면 세로로 뻗음)"""
    m = np.full((H, W), L.BACKGROUND, np.uint8)
    m[:, :60] = L.ROAD
    m[:, 60:SW0] = L.CURB
    m[:, SW0:SW1] = L.SIDEWALK
    return m


def with_obstacle(c1: int, rows=(80, 141), label=L.OBSTACLE, c0: int = SW0) -> np.ndarray:
    m = scene()
    m[rows[0] : rows[1], c0:c1] = label
    return m


def test_clear_sidewalk_is_passable():
    r = analyze_passability(scene())
    assert r.verdict == "passable"
    assert r.bottleneck_ratio == 1.0
    assert r.occupancy == 0


def test_partial_occupation_is_narrowed():
    r = analyze_passability(with_obstacle(111))  # 남은 49/97 ≈ 0.51
    assert r.verdict == "narrowed"
    assert 0.45 < r.bottleneck_ratio < 0.55
    assert r.occupied_mask is not None and r.occupied_mask.any()


def test_heavy_occupation_is_blocked():
    r = analyze_passability(with_obstacle(141))  # 남은 19/97 ≈ 0.2
    assert r.verdict == "blocked"
    assert any("차도 우회" in s for s in r.reasons)


def test_full_width_obstacle_breaks_continuity():
    r = analyze_passability(with_obstacle(SW1))
    assert r.verdict == "blocked"
    assert r.bottleneck_ratio == 0.0


def test_two_side_gaps_are_not_merged():
    """왼쪽 40% + 상자 + 오른쪽 30%: 통로는 두 개이고 70%로 합치지 않는다 (마스터 문서 9장 예)"""
    left = SW0 + int(97 * 0.4)
    r = analyze_passability(with_obstacle(left + 29, c0=left))
    assert 0.38 < r.bottleneck_ratio < 0.42
    assert r.verdict == "narrowed"


def test_zigzag_without_overlap_is_blocked():
    """행마다 절반은 비어 있어도 좌우가 엇갈려 이어지지 않으면 연속 통로가 아니다"""
    m = scene()
    mid = (SW0 + SW1) // 2
    m[50:100, SW0:mid] = L.OBSTACLE
    m[100:150, mid:SW1] = L.OBSTACLE
    assert analyze_passability(m).verdict == "blocked"


def test_pedestrian_occlusion_is_undetermined():
    """보행자에 가려진 곳은 점유인지 모르므로 낙관·보수 판정이 갈리면 판단 유보"""
    r = analyze_passability(with_obstacle(141, label=L.TRANSIENT))
    assert r.verdict == "undetermined"
    assert r.uncertainty_code == "occluded"


def test_vehicle_at_road_edge_without_curb_is_ambiguous():
    """연석이 안 보이고 차도에 붙은 차량: 보도 위인지 차도 위인지 애매 → 경계 불확실로 기록"""
    m = scene()
    m[:, 60:SW0] = L.ROAD
    m[80:141, 40:120] = L.OBSTACLE
    r = analyze_passability(m)
    assert r.stats["edge_uncertain_ratio"] > 0
    assert r.verdict == "undetermined"


def test_too_little_sidewalk_is_undetermined():
    m = np.full((H, W), L.ROAD, np.uint8)
    m[190:, 100:120] = L.SIDEWALK
    r = analyze_passability(m)
    assert r.verdict == "undetermined" and r.uncertainty_code == "low_coverage"


def test_bike_lane_mix_is_undetermined():
    m = scene()
    m[:, SW0:110] = L.BIKE_LANE
    assert analyze_passability(m).uncertainty_code == "mixed"


def test_horizontal_sidewalk_is_transposed():
    r = analyze_passability(with_obstacle(111).T.copy())
    assert r.transposed
    assert r.verdict == "narrowed"


def test_reference_width_only_adds_estimate():
    """실측 보도폭은 추정 절대폭만 덧붙이고 판정은 상대폭으로 한다"""
    a = analyze_passability(with_obstacle(111))
    b = analyze_passability(with_obstacle(111), reference_width_m=2.0)
    assert a.verdict == b.verdict and a.bottleneck_ratio == b.bottleneck_ratio
    assert a.bottleneck_m_est is None
    assert abs(b.bottleneck_m_est - 2.0 * b.bottleneck_ratio) < 0.01


def test_risk_uses_width_term_and_marks_length_unmeasured():
    r = analyze_passability(with_obstacle(111))
    score, detail = risk_score(r)
    assert score == round(100 * (1 - r.bottleneck_ratio))
    assert detail["length_term"] is None and detail["complete"] is False
    full, d2 = risk_score(r, length_ratio=1.0)
    assert d2["complete"] is True and full != score

    score, detail = risk_score(analyze_passability(with_obstacle(141, label=L.TRANSIENT)))
    assert score is None  # 측정 불가를 0점으로 쓰지 않음


def test_video_fusion_removes_moving_people():
    frames = []
    for x in (70, 100, 130):
        m = scene()
        m[80:140, x : x + 20] = L.TRANSIENT
        frames.append(SceneResult(m, None, None))
    fused = fuse_frames(frames).class_map
    assert not (fused == L.TRANSIENT).any()
    assert analyze_passability(fused).verdict == "passable"


def test_video_fusion_keeps_persistent_obstacle():
    frames = [SceneResult(with_obstacle(141), None, None) for _ in range(4)]
    frames.append(SceneResult(scene(), None, None))
    assert analyze_passability(fuse_frames(frames).class_map).verdict == "blocked"


def test_policy_footprint_is_full_silhouette_by_default():
    assert Policy().footprint_frac == 1.0
