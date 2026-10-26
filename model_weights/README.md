# model_weights

**지금은 쓰지 않습니다.** 실제 분석 모델은 RoadPush_AI 저장소 쪽에 둡니다 (Git 제외).

| 파일 | 위치 | 설정 |
|---|---|---|
| Mask2Former Swin-L Mapillary (노면 semantic) | `RoadPush_AI/surface_passability/models/mask2former_swin_large_mapillary/` | `ROADPUSH_AI_ROOT` |
| YOLO11n-seg (Phase 6 차량 instance) | `RoadPush_AI/surface_passability/models/yolo11n-seg.pt` | `ROADPUSH_AI_ROOT` |
| YOLO11s 장애물 원인 `obstacle_v2_yolo11s_full_os4` | `RoadPush_AI/obstacle_yolo/weights/obstacle_yolo11s_full_os4.pt` (SHA-256은 `service/config.yaml`) | `OBSTACLE_YOLO_ROOT` |

설치·점검: [docs/SETUP.md 8장](../docs/SETUP.md#8-실제-모델-실행-roadpush_ai-팀-파이프라인), `python scripts/check_model.py 사진.jpg`.
예전 임시 모델(SelectStar DeepLab TorchScript, 공개 YOLO11n·YOLOE) 연결 코드는 지웠습니다. 이 폴더에 남은 `.pt`는 지워도 됩니다.
