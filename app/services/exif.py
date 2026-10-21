"""사진 EXIF에서 촬영시각·GPS 읽기. 원본 전송 중 메타데이터가 지워졌을 수 있으므로 없으면 None"""

import io
from datetime import UTC, datetime, timedelta, timezone

from PIL import ExifTags, Image

GPS_IFD = 0x8825
EXIF_IFD = 0x8769


def _deg(v, ref: str | None) -> float | None:
    try:
        d, m, s = (float(x) for x in v)
    except (TypeError, ValueError):
        return None
    out = d + m / 60 + s / 3600
    return -out if ref in ("S", "W") else out


def read_exif(data: bytes, default_offset_hours: int = 9) -> dict:
    try:
        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
    except Exception:  # 이미지가 아니거나 EXIF 없음
        return {"captured_at": None, "lat": None, "lng": None}

    sub = exif.get_ifd(EXIF_IFD)
    raw_t = sub.get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
    offset = sub.get(ExifTags.Base.OffsetTimeOriginal)
    captured = None
    if raw_t:
        try:
            t = datetime.strptime(str(raw_t), "%Y:%m:%d %H:%M:%S")
            if offset:
                sign = -1 if offset.startswith("-") else 1
                hh, mm = offset.lstrip("+-").split(":")
                tz = timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
            else:
                tz = timezone(timedelta(hours=default_offset_hours))  # 폰 사진은 대개 현지 시각
            captured = t.replace(tzinfo=tz).astimezone(UTC)
        except ValueError:
            captured = None

    gps = exif.get_ifd(GPS_IFD)
    lat = _deg(gps.get(2), gps.get(1)) if gps else None
    lng = _deg(gps.get(4), gps.get(3)) if gps else None
    if lat is not None and lng is not None and lat == 0 and lng == 0:
        lat = lng = None
    return {"captured_at": captured, "lat": lat, "lng": lng}
