# 트러블슈팅 기록

실행·개발 중 발생한 문제와 원인, 해결, 재발 방지를 기록합니다. 최신 항목이 위에 옵니다.
증상별 빠른 해결은 [SETUP.md 12장](SETUP.md#12-자주-나는-문제)에도 있습니다.

| # | 날짜 | 환경 | 증상 | 상태 |
|---|---|---|---|---|
| [T6](#t6-서버-주소로-접속하면-detailnot-found) | 10/21 | 전체 | `http://127.0.0.1:8000` 접속 시 `{"detail":"Not Found"}` | 해결 |
| [T5](#t5-uvicorn-실행-시-unicodedecodeerror-cp949) | 10/21 | Windows (한국어) | `uvicorn` 실행 시 `UnicodeDecodeError: 'cp949' codec …` | 해결 |
| [T4](#t4-env-한글-주석을-cp949로-읽는-문제) | 10/21 | Windows (한국어) | `.env` 한글 주석을 cp949로 읽어 값이 깨질 수 있음 | 예방 조치 |
| [T3](#t3-한글이-들어간-폴더에서-이미지를-못-읽는-문제) | 10/21 | Windows | 폴더 경로에 한글이 있으면 OpenCV가 사진을 못 읽음 | 예방 조치 |
| [T2](#t2-촬영시각이-9시간-어긋남) | 10/21 | SQLite | 촬영시각 `+09:00`이 사라져 9시간 어긋남 | 해결 |
| [T1](#t1-보도-폭-전체가-막힌-구간이-통과로-나옴) | 10/21 | 판정 규칙 | 폭 전체가 막힌 구간이 분석에서 빠져 "통과"로 나옴 | 해결 |

---

## T6. 서버 주소로 접속하면 `{"detail":"Not Found"}`

**증상**
`uvicorn app.main:app --reload` 후 로그의 `http://127.0.0.1:8000`을 브라우저로 열면 `{"detail":"Not Found"}`.

**원인**
서버는 정상입니다. API는 모두 `/api/v1/…` 아래에 있고 `/`(루트) 주소에는 연결된 화면이 없었습니다.

**해결**
`/`로 접속하면 API 문서 화면(`/docs`)으로 이동하도록 추가 (`app/main.py`).

| 주소 | 내용 |
|---|---|
| http://localhost:8000 | → `/docs`로 이동 |
| http://localhost:8000/docs | API 문서·직접 호출 화면 (Swagger) |
| http://localhost:8000/api/v1/health | 상태 확인 `{"status":"ok", …}` |
| http://localhost:8000/api/v1/map | 지도용 데이터 (시연 데이터를 넣은 뒤) |

**재발 방지** `tests/test_api.py::test_root_redirects_to_docs`

---

## T5. `uvicorn` 실행 시 `UnicodeDecodeError: 'cp949'`

**증상** (한국어 Windows)
```
UnicodeDecodeError: 'cp949' codec can't decode byte 0xec in position 17: illegal multibyte sequence
```
서버가 시작되지 않음. `alembic upgrade head`도 같은 오류.

**원인**
서버가 시작할 때 DB 마이그레이션 설정 파일 `alembic.ini`를 읽습니다. Alembic은 이 파일을 **OS 기본 인코딩**으로 읽는데,
한국어 Windows의 기본 인코딩은 cp949입니다. 파일 첫 줄의 한글 주석이 UTF-8로 저장돼 있어 cp949로 해석하다 실패했습니다.
Linux·macOS는 기본 인코딩이 UTF-8이라 재현되지 않았습니다.

**해결**
1. `alembic.ini`를 ASCII(영문)만으로 바꾸고, 첫 줄에 "ASCII만 쓸 것" 주석 추가
2. 서버 시작(`init_db`)과 `scripts/check_db.py`는 `alembic.ini`를 아예 읽지 않고 코드에서 설정을 만들도록 변경 (`app/db/__init__.py`의 `alembic_config()`)

같은 조건(EUC-KR 로캘, 이전 코드)에서 오류를 재현했고, 수정 후 서버 시작과 전체 테스트가 통과했습니다.

**재발 방지**
- `tests/test_windows_compat.py::test_alembic_ini_is_ascii` — `alembic.ini`에 한글이 들어가면 테스트 실패
- `tests/test_windows_compat.py::test_server_does_not_read_alembic_ini`

**이미 로컬에서 `alembic.ini`를 직접 고친 경우** `git pull`이 충돌하므로, 고친 내용을 버리고 받습니다.
```powershell
git checkout -- alembic.ini
git pull
```

**참고: 로그의 한글이 깨져 보일 때**
서버는 정상이며 터미널 표시 문제입니다. Windows Terminal(PowerShell 7)을 쓰거나, 실행 전에 `chcp 65001`을 입력하면 한글이 바르게 보입니다.

---

## T4. `.env` 한글 주석을 cp949로 읽는 문제

**증상 (예방)**
`.env.example`을 복사한 `.env`에는 한글 주석이 있습니다. 설정 라이브러리(pydantic-settings)가 인코딩을 지정하지 않으면
OS 기본 인코딩(cp949)으로 읽어 T5와 같은 오류가 나거나 값이 깨질 수 있습니다.

**해결** `.env`를 항상 UTF-8로 읽도록 지정 (`app/config.py`의 `env_file_encoding="utf-8"`).
메모장으로 `.env`를 저장할 때도 인코딩은 **UTF-8**을 선택합니다.

**재발 방지** `tests/test_windows_compat.py::test_env_file_is_read_as_utf8`

---

## T3. 한글이 들어간 폴더에서 이미지를 못 읽는 문제

**증상 (예방)**
프로젝트가 `C:\Users\홍길동\…`, `바탕 화면`처럼 한글이 들어간 경로에 있으면 Windows에서 OpenCV(`cv2.imread`/`imwrite`)가 파일을 열지 못해
모든 분석이 "이미지를 읽을 수 없습니다"로 실패합니다.

**해결**
파일을 바이트로 읽고 쓴 뒤 OpenCV로 디코딩·인코딩하도록 변경 (`app/ai_engine/imageio.py`). 서버·일괄 분석 스크립트 모두 이 함수를 씁니다.
영상(`VideoCapture`)은 바이트로 열 수 없어 한글이 없는 상대 경로로 바꿔 엽니다. 그래도 실패하면 프로젝트를 `C:\dev\RoadPush_BE`처럼 영문 경로로 옮깁니다.

**재발 방지** `tests/test_windows_compat.py::test_image_io_with_korean_path`

---

## T2. 촬영시각이 9시간 어긋남

**증상**
`captured_at=2026-10-21T10:00:00+09:00`으로 등록했는데 응답이 `10:00:00Z`(UTC 10시 = 한국 19시)로 나옴.

**원인** SQLite는 시간대 정보를 저장하지 않고 버립니다. `+09:00`이 떨어진 채 "10:00"만 남았습니다.

**해결** 저장 전에 UTC로 변환(`01:00 UTC`)하고, 응답할 때 `+09:00`으로 다시 바꿔 보냅니다. PostgreSQL에서도 같은 방식으로 동작합니다.

**재발 방지** `tests/test_api.py::test_response_follows_master_spec_4_4` (응답 `captured_at`이 입력과 같은지 확인)

---

## T1. 보도 폭 전체가 막힌 구간이 "통과"로 나옴

**증상** 보도 폭 전체를 장애물이 덮은 장면이 `passable`로 판정됨.

**원인** 단면마다 빈 보도 픽셀을 기준으로 보도 범위를 찾는데, 폭 전체가 덮인 단면은 빈 픽셀이 없어 분석에서 빠졌고,
막힌 구간 앞뒤의 멀쩡한 보도만 분석됐습니다.

**해결** 위아래 단면의 보도 경계를 보간해 "덮인 보도" 단면으로 포함 (`rules.py`의 `_fill_covered_rows`). 연속 통로가 끊겨 `blocked`로 판정됩니다.

**재발 방지** `tests/test_passability.py::test_full_width_obstacle_breaks_continuity`

---

## 새 항목 작성 양식

```markdown
## T7. 한 줄 증상

**증상** 무엇을 했을 때 무엇이 나왔는지 (오류 메시지 그대로)
**원인** 왜 그랬는지
**해결** 무엇을 바꿨는지 (파일 이름)
**재발 방지** 추가한 테스트 또는 확인 방법
```
위 목록 표에도 한 줄 추가합니다.
