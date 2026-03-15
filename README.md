# 📅 SanS 루틴 봇

매일 아침/저녁 알림을 보내고, 그룹 멤버들의 **오늘 루틴**을 기록·요약해 주는 텔레그램 그룹 봇입니다.

---

## ✨ 주요 기능

| 기능 | 설명 |
|------|------|
| 🌅 아침 알림 (08:00 KST) | "오늘 루틴을 적어볼까요?" 메시지 전송 → 답장으로 루틴 기록 |
| 🌙 저녁 알림 (21:00 KST) | 아직 못 쓴 사람을 위한 리마인드 알림 → 답장으로 루틴 기록 |
| ✍️ 루틴 기록 | 아침/저녁 알림 메시지에 **답장**하면 그날 루틴으로 저장 (오전/저녁 모두 같은 날 루틴 기록) |
| 🤖 AI 요약 | `/summary` 로 오늘 전체 루틴을 Claude AI가 요약 |
| 📊 주간/월간 통계 | `/weekstats` (지난 7일), `/monthstats` (지난 30일) 로 기록 순위 확인 |
| 💾 영구 저장 | Railway PostgreSQL 사용 시 재배포해도 기록 유지 |

- 동일한 내용(같은 날·같은 타입·같은 텍스트)은 한 번만 저장됩니다.

---

## 🚀 배포 방법 (Railway)

### 1단계: 봇 토큰 발급

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 에게 `/newbot` 명령
2. 봇 이름 & 유저네임 설정
3. 발급된 토큰 복사

### 2단계: Chat ID 확인

1. 봇을 그룹에 초대한 뒤 그룹에서 메시지 한 번 전송
2. 봇이 켜진 상태에서 해당 채팅에서 `/chatid` 입력 → 봇이 채팅방 ID를 알려줌  
   (또는 봇을 끄고 `https://api.telegram.org/bot<토큰>/getUpdates` 에서 `chat.id` 확인)

### 3단계: Railway 배포

1. [railway.app](https://railway.app) 에서 GitHub 레포 연결 (`SeeYounsunny/SanS_Routine_Bot` 등)
2. **Database** → PostgreSQL 추가 후, 봇 서비스의 Variables 에 `DATABASE_URL` 추가  
   - Key: `DATABASE_URL`  
   - Value: `${{ Postgres.DATABASE_URL }}` (Postgres 서비스 이름에 맞게 조정)

### 4단계: 환경변수 설정

Railway → 봇 서비스 → **Variables** 탭에서 추가:

| 변수 | 설명 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급한 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 알림을 보낼 그룹/채팅 ID (숫자만) |
| `ANTHROPIC_API_KEY` | Claude 요약용 API 키 (선택, 없으면 `/summary` 비활성) |
| `DATABASE_URL` | PostgreSQL 연결 URL (위 참조, 없으면 로컬 SQLite 사용) |

선택:

| 변수 | 설명 |
|------|------|
| `ANTHROPIC_MODEL` | 사용할 Claude 모델 (기본: `claude-3-sonnet-20240229`) |
| `DB_PATH` | SQLite 사용 시 DB 파일 경로 (기본: `routines.db`) |

---

## 💬 사용법

### 루틴 입력

1. **`/start`** → 안내 메시지 + "오늘 루틴을 작성해보세요!" 프롬프트가 바로 옴 → **그 메시지에 답장**으로 오늘 루틴 작성
2. **아침 8시** 알림이 오면 → 해당 메시지에 **답장**으로 오늘 루틴 작성
3. **저녁 9시** 알림은 아직 안 쓴 사람용 리마인드 → **답장**하면 같은 날 루틴으로 추가 기록

모든 답장은 **그날 루틴 기록**으로 저장되며, 오전/저녁 구분은 "오전 기록"/"저녁 기록"으로만 표시됩니다.

### 명령어 (영어로 입력)

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 안내 + 오늘 루틴 작성 프롬프트 바로 전송 |
| `/add` | 오늘 루틴 추가 작성 (다시 작성 프롬프트 전송) |
| `/delete` | 오늘 작성한 루틴 전부 삭제 |
| `/reset 비밀번호` | 전체 데이터 초기화 (서버에 설정된 비밀번호 일치 시에만 실행. 비밀번호는 Railway Variables의 RESET_PASSWORD로 설정) |
| `/search YYYY-MM-DD` | 해당 날짜의 내 루틴 조회 (예: /search 2025-03-15) |
| `/summary` | 오늘 전체 루틴 AI 요약 (ANTHROPIC_API_KEY 필요) |
| `/myroutine` | 나의 오늘 루틴 기록 확인 |
| `/weekstats` | 지난 7일 통계 (가장 많이 기록한 사람 TOP 3, 가장 많이 기록된 루틴 TOP 3) |
| `/monthstats` | 지난 30일 통계 (사람 TOP 5, 루틴 TOP 5) |
| `/chatid` | 이 채팅방 ID 확인 (환경변수 설정용) |

---

## ⚠️ 참고 사항

- **동시에 봇 인스턴스는 하나만**: 같은 토큰으로 로컬 + Railway 동시 실행 시 `Conflict: only one bot instance` 에러가 납니다. 운영 시에는 Railway만 켜두고 로컬은 끄세요.
- **PostgreSQL 권장**: Railway에서 재배포 시 SQLite는 초기화될 수 있어, 영구 보존이 필요하면 PostgreSQL + `DATABASE_URL` 설정을 권장합니다.
- Railway 무료 플랜은 월 500시간 실행 제한이 있을 수 있습니다.

---

## 🗂️ 프로젝트 구조

```
SanS_Routine_Bot/
├── bot.py          # 메인 봇 로직 & 스케줄러
├── database.py     # PostgreSQL / SQLite 지원, 루틴 저장·통계 쿼리
├── ai_summary.py   # Claude AI 요약 생성
├── requirements.txt
├── Procfile        # Railway 배포 설정 (worker: python bot.py)
└── .env.example    # 환경변수 예시
```
