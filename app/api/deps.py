from fastapi import Request

from ..config import get_settings


def file_url_for(request: Request):
    """저장 키 → 브라우저가 열 수 있는 전체 URL (프론트는 이 주소로 사진·overlay를 읽는다)"""
    base = (get_settings().public_base_url or str(request.base_url)).rstrip("/")
    return lambda key: f"{base}/files/{key}"
