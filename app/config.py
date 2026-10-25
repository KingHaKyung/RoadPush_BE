from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env는 UTF-8로 읽는다 (한국어 주석이 있어도 Windows cp949 기본값에 영향받지 않게)
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./data/roadpush.db"
    storage_dir: Path = Path("./storage")
    # 프론트 개발 서버 + Vercel 배포 주소
    cors_origins: list[str] = ["http://localhost:5173", "https://road-push-fe.vercel.app"]
    # Vercel 미리보기 주소(road-push-fe-git-<브랜치>-….vercel.app)도 허용. 비우면 cors_origins만
    cors_origin_regex: str = r"https://road-push-fe[a-z0-9-]*\.vercel\.app"
    # 응답의 사진·overlay 주소 앞부분. 배포 서버에서 https 주소를 넣는다 (예: https://1-2-3-4.sslip.io)
    # 비우면 요청 주소를 쓴다 (리버스 프록시 뒤에서는 http로 나와 HTTPS 페이지가 이미지를 막을 수 있음)
    public_base_url: str = ""
    # 응답의 captured_at 표시 시간대 (DB는 UTC 저장)
    display_utc_offset_hours: int = 9

    # 노면 모델: dummy | selectstar | hf
    surface_model: str = "dummy"
    surface_weights: str = "model_weights/selectstar_deeplab.pt"
    selectstar_mean: list[float] = [0.485, 0.456, 0.406]
    selectstar_std: list[float] = [0.229, 0.224, 0.225]
    selectstar_input: list[int] | None = None  # [W, H]. 비우면 비율 유지
    # 장애물 모델: none | yolo
    obstacle_model: str = "none"
    obstacle_weights: list[str] = []
    device: str = "cuda"
    infer_short_side: int = 768

    run_worker_in_api: bool = True
    worker_poll_sec: float = 1.0
    max_attempts: int = 3  # 같은 분석 작업의 자동 재시도 한도 (첫 시도 포함)

    # 영상(선택): 몇 초마다 프레임을 뽑아 융합할지
    video_frame_interval_sec: float = 2.0
    video_max_frames: int = 15

    # 구간 후보 검색 반경(m). 통과·위험도 임계값과 무관하며 실제 위치 오차를 보고 정한다
    site_search_radius_m: float = 50.0
    nodelink_path: Path = Path("data/roadpush_seongnam_nodelink.geojson")
    link_match_max_m: float = 40.0

    # 행정구역·주소 자동 채우기 (브이월드 역지오코딩). 비우면 등록 때 넣은 값만 쓴다
    vworld_key: str = ""
    vworld_domain: str = "http://localhost:5173"  # 인증키에 등록한 서비스 URL
    geocode_timeout_sec: float = 5.0

    # 배포용 CCTV 프록시 (app/api/proxy.py). 프론트 .env와 같은 키를 쓴다. 비우면 그 경로는 502·503
    utic_api_key: str = ""  # UTIC는 등록 IP 대역에서만 응답한다 → 이 서버 공인 IP를 UTIC에 등록
    its_api_key: str = ""

    # 참조 레이어 v2 (ref_seongnam_link 성남 · ref_walk_link 서울, 팀원 제공). 테이블이 없으면 쓰지 않는다
    ref_match_max_m: float = 20.0  # 구간 위치와 보행 링크 선형의 최대 거리
    ref_bbox_margin_m: float = 300.0  # 중심점 인덱스로 거를 때 긴 링크를 위한 여유
    # 구간 보도 폭으로 채울 출처. ngii = 성남 연속수치지형도 인도 폭, building_no = 서울 건물번호 매칭
    # route_approx(서울 노선 근사, 평균 오차 1.31m)는 기본 제외
    ref_width_sources: list[str] = ["ngii", "building_no"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
