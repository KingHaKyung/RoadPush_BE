# model_weights

서비스에서 쓰는 선택 가중치를 두는 곳입니다. 가중치 파일은 Git에 올리지 않습니다 (`.gitignore`).
크기·사용 조건에 따라 Git LFS 또는 별도 공유로 받아 이 폴더에 넣습니다.

| 파일 | 설정 | 비고 |
|---|---|---|
| `selectstar_deeplab.pt` | `SURFACE_MODEL=selectstar` | SelectStar DeepLab v3+를 TorchScript로 내보낸 파일. 입력 (1,3,H,W), 출력 (1,7,H,W) logits. 내보내는 방법은 `app/ai_engine/surface/selectstar.py` 맨 위 설명 |
| `yolo11n-seg.pt` | `OBSTACLE_MODEL=yolo` | 차량 기본선 (COCO) |
| `yoloe-26s-seg.pt` | `OBSTACLE_MODEL=yolo` | 비차량 구체 카테고리 (`경로|traffic cone,barricade,kick scooter,box,…`) |

이름에 버전을 붙여 실험 checkpoint와 구분합니다 (예: `selectstar_deeplab_ft-2026-10-25.pt`).
분석 결과에는 사용한 가중치 이름이 `model_version`으로 기록됩니다.

사용 조건 확인: SelectStar checkpoint 조건, Ultralytics AGPL-3.0/Enterprise (마스터 문서 12장).
