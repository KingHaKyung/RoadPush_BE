# 구성도

| 파일 | 내용 |
|---|---|
| `roadpush_architecture.svg` · `.png` | 서비스 아키텍처: 사용자 → 프론트(Vercel) → 백엔드 서버(HTTPS · REST API · 대기열 · **AI 분석 엔진**) → Supabase · 파일 저장소 · 브이월드. ①~⑤는 서비스 흐름 순서 |

- **PNG**: 발표 자료·보고서용 (3840×2160)
- **SVG**: 고칠 때. Figma에 파일을 끌어다 놓으면 상자·화살표·아이콘·글자가 각각 편집 가능한 레이어로 들어옵니다.
  글꼴은 Noto Sans KR(한글)과 JetBrains Mono(코드)입니다.
- 백엔드는 Tailscale Funnel로 HTTPS 공개 (`<서버>.ts.net`). 서버 종류는 "Backend Server"로 묶었고, 배포 방식이 바뀌면 Tailscale 칸과 오른쪽 위 설명 한 줄만 고칩니다.
- 아이콘: [Simple Icons](https://simpleicons.org) (CC0, 각 상표는 해당 회사 소유) · [Lucide](https://lucide.dev) (ISC)
