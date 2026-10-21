from fastapi import Request


def file_url_for(request: Request):
    """저장 키 → 브라우저가 열 수 있는 전체 URL (프론트는 이 주소로 사진·overlay를 읽는다)"""
    base = str(request.base_url).rstrip("/")
    return lambda key: f"{base}/files/{key}"
