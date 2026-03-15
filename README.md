# 📅 텔레그램 루틴 봇

매일 아침/저녁 알람을 보내고, Claude AI로 멤버별 루틴을 요약해주는 텔레그램 그룹 봇입니다.

---

## ✨ 주요 기능

| 기능 | 설명 |
|------|------|
| 🌅 아침 알람 (08:00 KST) | "오늘 루틴을 시작해보아요!" 메시지 전송 |
| 🌙 저녁 알람 (21:00 KST) | "오늘 하루를 돌아보아요!" 회고 알람 전송 |
| ✍️ 루틴 기록 | 알람 메시지에 **답장**하면 자동 저장 |
| 🤖 AI 요약 | Claude AI로 사람별 루틴 요약 생성 |

---

## 🚀 배포 방법 (Railway)

### 1단계: 봇 토큰 발급

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 에게 `/newbot` 명령
2. 봇 이름 & 유저네임 설정
3. 발급된 토큰 복사

### 2단계: Chat ID 확인

1. 봇을 그룹에 초대
2. 그룹에서 아무 메시지나 전송
3. 아래 URL에서 Chat ID 확인 (`chat.id` 값, 음수임):
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```

### 3단계: Railway 배포

```bash
# 1. GitHub에 코드 푸시
git init && git add . && git commit -m "init"
git remote add origin https://github.com/YOUR_ID/REPO.git
git push -u origin main

# 2. railway.app 에서 GitHub 레포 연결
# 3. Variables 탭에서 환경변수 설정 (아래 참고)
```

### 4단계: 환경변수 설정

Railway → 프로젝트 → Variables 탭에서 추가:

```
TELEGRAM_BOT_TOKEN   = 봇토큰
TELEGRAM_CHAT_ID     = -100그룹아이디
ANTHROPIC_API_KEY    = sk-ant-...
```

---

## 💬 사용법

### 루틴 입력

1. 아침 8시에 봇 메시지가 도착
2. **해당 메시지에 답장(Reply)** 으로 오늘 할 일 작성
3. 저녁 9시에 회고 메시지 도착
4. **해당 메시지에 답장(Reply)** 으로 완료 내용 & 소감 작성

### 명령어 (영어로 입력)

```
/start        봇 소개 및 사용법
/summary      오늘 전체 루틴 AI 요약
/myroutine    나의 오늘 루틴 확인
/testmorning  아침 알람 즉시 테스트 전송
/testevening  저녁 알람 즉시 테스트 전송
```

---

## ⚠️ Railway 무료 플랜 주의사항

- SQLite DB는 재배포 시 초기화됩니다
- 데이터 영구 보존이 필요하면 **Railway PostgreSQL 플러그인** 추가를 권장합니다
- 무료 플랜은 월 500시간 실행 제한이 있습니다

---

## 🗂️ 프로젝트 구조

```
SanS_Routine_Bot/
├── bot.py          # 메인 봇 로직 & 스케줄러
├── database.py     # SQLite 비동기 DB 레이어
├── ai_summary.py   # Claude AI 요약 생성
├── requirements.txt
├── Procfile        # Railway/Render 배포 설정
└── .env.example    # 환경변수 예시
```
