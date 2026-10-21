"""
실행 중인 백엔드에 시연용 데이터를 넣는다 (프론트 연결 확인용). 표준 라이브러리만 쓴다.

    python scripts/seed_demo.py                       # http://localhost:8000/api/v1
    python scripts/seed_demo.py --base http://서버주소/api/v1

만드는 것: 프론트 예시 지역(성남시 분당구 서현·수내) 구간 4곳, 관측 8건, 검수 1건, 조치 + 재촬영 전후 비교 1건.
dummy 모델이면 사진 내용과 상관없이 판정이 골고루 나온다. 사진은 무늬만 있는 가짜 이미지다.
"""

import argparse
import json
import sys
import time
import urllib.request
import uuid

import cv2
import numpy as np

# 프론트 예시 관측과 같은 좌표. 이름은 시연용
SITES = [
    ("시연 구간 1 · 서현동", 37.385672, 127.119483),
    ("시연 구간 2 · 수내동", 37.379498, 127.113499),
    ("시연 구간 3 · 이매동", 37.386851, 127.127334),
    ("시연 구간 4 · 서현동", 37.383236, 127.120959),
]


def call(method: str, url: str, body: dict | None = None, files: dict | None = None):
    headers, data = {}, None
    if files is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (body or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (name, content) in files.items():
            head = f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
            parts.append(head.encode() + b"Content-Type: image/jpeg\r\n\r\n" + content + b"\r\n")
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def photo(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    img = rng.integers(40, 220, (720, 960, 3), dtype=np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


def upload(base: str, seed: int, captured_at: str, lat: float, lng: float, **site) -> dict:
    form = {"captured_at": captured_at, "lat": lat, "lng": lng, "location_source": "manual", "source": "upload"}
    form.update(site)
    return call("POST", f"{base}/observations", form, {"file": (f"demo{seed}.jpg", photo(seed))})


def wait(base: str, obs_id: str, timeout: float = 60) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        o = call("GET", f"{base}/observations/{obs_id}")
        if o["processing_status"] in ("completed", "failed"):
            return o
        time.sleep(1)
    raise TimeoutError(f"분석이 끝나지 않았습니다: {obs_id} (워커가 돌고 있는지 확인)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000/api/v1")
    base = ap.parse_args().base.rstrip("/")
    try:
        call("GET", f"{base}/health")
    except OSError as e:
        sys.exit(f"백엔드에 연결할 수 없습니다 ({base}): {e}\n먼저 uvicorn app.main:app 을 실행하세요")

    seed = int(time.time()) % 10000
    first: list[dict] = []
    for i, (name, lat, lng) in enumerate(SITES):
        o = upload(base, seed + i, f"2026-10-1{6 + i}T09:{10 + i}:00+09:00", lat, lng, new_site_name=name)
        first.append(o)
        print(f"구간 등록: {name} → {o['site_id']}")
    for i, (_, lat, lng) in enumerate(SITES):
        upload(base, seed + 10 + i, f"2026-10-1{1 + i}T15:00:00+09:00", lat, lng, site_id=first[i]["site_id"])

    results = [wait(base, o["observation_id"]) for o in first]
    for o in results:
        print(f"  {o['site_name']}: {o['passability']} (상대 위험도 {o['risk_score']})")

    # 검수: 첫 관측 확인
    target = results[0]
    call("POST", f"{base}/observations/{target['observation_id']}/reviews",
         {"status": "confirmed", "comment": "시연 데이터 확인", "reviewer": "교통행정과"})  # fmt: skip

    # 조치 → 재촬영 → 전후 비교
    b = results[1]
    req = {"state": "inspection_requested", "dept": "주차관리과", "content": "보도 위 차량 이동 요청"}
    act = call("POST", f"{base}/observations/{b['observation_id']}/action", req)
    action_id = act["action"]["action_id"]
    call("PATCH", f"{base}/actions/{action_id}", {"state": "done", "note": "차량 이동 확인"})
    _, lat, lng = SITES[1]
    after = upload(base, seed + 99, "2026-10-21T17:00:00+09:00", lat, lng, site_id=b["site_id"])
    wait(base, after["observation_id"])
    call("PATCH", f"{base}/actions/{action_id}", {"after_observation_id": after["observation_id"]})
    cmp = call(
        "GET", f"{base}/sites/{b['site_id']}/compare?before={b['observation_id']}&after={after['observation_id']}"
    )
    print(f"전후 비교: {cmp['site_id']} comparable={cmp['comparable']} delta={cmp['delta']}")
    print(f"\n완료. 지도 확인: {base}/map")


if __name__ == "__main__":
    main()
