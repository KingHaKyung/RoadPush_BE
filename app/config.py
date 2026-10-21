from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./roadpush.db"
    storage_dir: Path = Path("./storage")
    # 프론트 개발 서버 + Vercel 배포 주소
    cors_origins: list[str] = ["http://localhost:5173", "https://road-push-fe.vercel.app"]
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
