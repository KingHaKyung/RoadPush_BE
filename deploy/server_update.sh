#!/usr/bin/env bash
# 배포 서버에서 실행하는 갱신 단계. PC의 deploy/deploy.ps1이 이 파일을 올려 실행한다 (직접 실행해도 됨).
#   bash server_update.sh [--ai <AI 묶음.tgz>] [--storage <사진 묶음.tgz>] [--check-model]
# 1) AI 코드(+모델) 풀기  2) 백엔드 git pull  3) 패키지 설치  4) 사진 풀기  5) 서비스 재시작·health  6) 모델 점검
set -euo pipefail

BE="$HOME/RoadPush_BE"
AI="$HOME/RoadPush_AI"
AI_TGZ=""
STORAGE_TGZ=""
CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ai) AI_TGZ="$2"; shift 2 ;;
    --storage) STORAGE_TGZ="$2"; shift 2 ;;
    --check-model) CHECK=1; shift ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done

if [ -n "$AI_TGZ" ]; then
  echo "== 1. AI 코드 풀기"
  mkdir -p "$AI"
  # 코드는 지우고 새로 푼다 (AI 저장소에서 지운 파일이 서버에 남지 않게). models/는 묶음에 있을 때만 바꾼다
  rm -rf "$AI/src" "$AI/configs" "$AI/schemas" "$AI/surface_passability" "$AI/pyproject.toml" "$AI/VERSION.json"
  if tar -tzf "$AI_TGZ" | grep -q '^RoadPush_AI/models/'; then
    rm -rf "$AI/models"
  fi
  tar -xzf "$AI_TGZ" -C "$HOME"
  cat "$AI/VERSION.json"; echo
fi

echo "== 2. 백엔드 git pull"
git -C "$BE" pull --ff-only

echo "== 3. 패키지 설치"
cd "$BE"
# shellcheck disable=SC1091
. .venv/bin/activate
if [ -d "$AI/src" ]; then
  pip install -q -e ".[ai]"
  pip install -q -e "$AI" --no-deps
  pip install -q -e "$AI/surface_passability" --no-deps
else
  pip install -q -e .  # AI 코드를 아직 안 올렸으면 dummy 엔진용만
fi

if [ -n "$STORAGE_TGZ" ]; then
  echo "== 4. 사진·overlay 풀기 (storage/)"
  tar -xzf "$STORAGE_TGZ" -C "$BE"
fi

echo "== 5. 서비스 재시작"
sudo systemctl restart roadpush
for _ in $(seq 1 60); do
  if curl -fsS localhost:8000/api/v1/health; then
    echo
    break
  fi
  sleep 2
done
if ! curl -fsS localhost:8000/api/v1/health >/dev/null; then
  echo "[X] 백엔드가 뜨지 않았습니다. 최근 로그:" >&2
  sudo journalctl -u roadpush -n 40 --no-pager >&2
  exit 1
fi

if [ "$CHECK" = 1 ]; then
  echo "== 6. 모델 점검"
  python scripts/check_model.py
fi
echo "[OK] 서버 갱신 완료"
