# 📅 SanS 루틴/출석 봇

매일 아침/저녁 알림으로 **루틴**을 기록·요약해 주고, 일요일에는 **출석체크**도 버튼으로 처리해 주는 텔레그램 그룹 봇입니다.  
봇 유저네임: [@sans1_healthroutinebot](https://t.me/sans1_healthroutinebot)

---

## ✨ 주요 기능

| 기능 | 설명 |
|------|------|
| 🌅 아침 알림 (08:00 KST) | 단체방에 "오늘 루틴을 적어볼까요?" 전송. 루틴 입력은 **봇과 1:1**에서 `/add` |
| 🌙 저녁 알림 (20:00 KST) | 단체방 리마인드. 루틴 입력은 **봇과 1:1**에서 `/add` |
| ☀️ 점심 리마인드 (12:00 KST) | 단체방에 오늘 입력한 사람별 루틴 한 번 공지 |
| ✍️ 루틴 기록 | **봇과 1:1 대화**에서 `/add` 로만 입력 (단체방에서는 입력 불가, 1:1 유도 메시지 표시) |
| 📋 어제 루틴 번호 선택 | 1:1에서 `/add` 시, **본인이 어제 입력한** 루틴만 번호로 표시. 번호(쉼표 구분) + 새 루틴 답장 (예: `1,3,요가 10분`) |
| 🤖 AI 요약 | `/summary` 로 오늘 전체 루틴을 Claude AI가 요약 |
| 📊 주간/월간 통계 | `/weekstats` (지난 7일), `/monthstats` (지난 30일) 로 기록 순위 확인 |
| 📖 사용법 안내 | `/help` 로 사용법 매뉴얼 표시 |
| 💾 영구 저장 | Railway PostgreSQL 사용 시 재배포해도 기록 유지 |

- 동일한 내용(같은 날·같은 유저·같은 텍스트)은 한 번만 저장됩니다.

---

## 🚀 배포 방법 (Railway)

### 1단계: 봇 토큰 발급

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 에게 `/newbot` 명령
2. 봇 이름 & 유저네임 설정
3. 발급된 토큰 복사

### 2단계: Chat ID 확인

1. [@sans1_healthroutinebot](https://t.me/sans1_healthroutinebot) 을 그룹에 초대한 뒤 그룹에서 메시지 한 번 전송
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

- **루틴은 봇과 1:1 대화에서만** 입력할 수 있습니다. 단체방에서 `/add` 를 누르면 1:1로 이동하라는 안내가 나옵니다.
- **외부 유입 차단**: 1:1에서 루틴을 저장할 때, **`TELEGRAM_CHAT_ID`로 지정한 단체방의 멤버만** 입력이 허용됩니다. (멤버가 아니면 저장되지 않음)
- **봇과 1:1** 채팅을 연 뒤 **`/add`** 또는 **`/add YYYY-MM-DD`** 를 입력하세요.
  - **어제 루틴이 있는 경우**: **본인이 어제 입력한** 루틴만 번호 목록으로 나옵니다. 기존 건은 **번호를 쉼표(,)** 로 구분, 새로 넣을 건 **쉼표 뒤**에 적고, 봇이 보낸 메시지에 **답장**으로 보내면 저장됩니다. (예: `1,3,요가 10분`)
  - **어제 루틴이 없는 경우**: "해당 날짜 루틴을 추가해주세요!" 메시지에 **답장**으로 적으면 저장됩니다.
- **단체방**에는 매일 **08:00** 아침 알림, **12:00** 점심 리마인드(오늘 입력한 사람별 루틴 공지), **20:00** 저녁 리마인드가 올라옵니다.

모든 기록은 **그날 루틴**으로 저장되며, `/today` 및 `/search`는 **오전/저녁 구분 없이 오늘 루틴 하나**로 보여줍니다. **루틴 기록 자세한 사용법은 `/help`, 출석체크 사용법은 `/attendanceguide` 를 입력하세요.**

### 명령어

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 환영 메시지. 루틴 추가는 봇과 1:1에서 `/add` |
| `/help` | 루틴 기록 사용법 안내 |
| `/attendanceguide` | 출석체크 사용법 안내 |
| `/status` | (관리자용) 현재 출석 세션 상태 확인 |
| `/add [YYYY-MM-DD]` | 루틴 추가 (1:1에서만. 날짜 지정 시 해당 날짜로 기록. 어제 루틴 있으면 번호 선택지 표시) |
| `/today` | 오늘 내가 입력한 루틴 보기 |
| `/myroutine` | 내가 자주 사용하는 루틴 TOP 5 |
| `/delete` | 오늘 작성한 루틴 전부 삭제 |
| `/reset 비밀번호` | 전체 데이터 초기화 (서버 비밀번호 일치 시에만 실행) |
| `/search YYYY-MM-DD` | 해당 날짜의 내 루틴 조회 (예: /search 2025-03-15) |
| `/list [YYYY-MM-DD]` | 해당 날짜의 전체 루틴 목록 (기본: 오늘). 헤더(`MM/DD 루틴 기록`) + `• [이름]: 루틴1, 루틴2` 형식 |
| `/setname 표시이름` | 목록·통계·요약·출석·`/today`·`/myroutine` 등에 표시될 내 이름 설정 (1:1에서만) |
| `/summary` | 오늘 전체 루틴 AI 요약 (ANTHROPIC_API_KEY 필요) |
| `/weekstats` | 지난 7일 통계 (사람 TOP 3, 루틴 TOP 3) |
| `/monthstats` | 지난 30일 통계 (사람 TOP 5, 루틴 TOP 5) |
| `/chatid` | 이 채팅방 ID 확인 (환경변수 설정용) |

---

## 📌 출석체크 봇
- 매주 일요일 출석체크 세션이 시작되면 단체방 상단에 안내 메시지가 표시됩니다. (자동 삭제되지 않음)
- 이후 출석은 버튼으로만 참여하며, 출석 현황은 같은 메시지에서 계속 업데이트됩니다.
- 이름 표시는 `/setname` 설정을 우선해서 표시됩니다.
- 안내/사용법: `/attendanceguide`
- 관리자용 상태 확인: `/status`

## ⚠️ 참고 사항

- **동시에 봇 인스턴스는 하나만**: 같은 토큰으로 로컬 + Railway 동시 실행 시 `Conflict: only one bot instance` 에러가 납니다. 운영 시에는 Railway만 켜두고 로컬은 끄세요.
- **PostgreSQL 권장**: Railway에서 재배포 시 SQLite는 초기화될 수 있어, 영구 보존이 필요하면 PostgreSQL + `DATABASE_URL` 설정을 권장합니다.
- Railway 무료/트라이얼 플랜은 **30일** 또는 **$5 사용량** 중 먼저 도달하는 쪽으로 제한될 수 있습니다. 24시간 서비스 시 유료 플랜(Hobby 등)이 필요할 수 있어요.

---

## 🗂️ 프로젝트 구조

```
SanS_Routine_Bot/
├── bot.py          # 메인 봇 로직 & 스케줄러
├── database.py     # PostgreSQL / SQLite 지원, 루틴 저장·통계 쿼리
├── ai_summary.py   # Claude AI 요약 생성
├── attendance.py   # 출석체크(버튼/세션/현황) 기능
├── requirements.txt
├── Procfile        # Railway 배포 설정 (worker: python bot.py)
└── .env.example    # 환경변수 예시
```
