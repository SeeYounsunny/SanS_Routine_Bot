import os
import logging
import datetime
import pytz
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from database import Database
from ai_summary import generate_summary

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")
db = Database()

# ─────────────────────────────────────────
# 예약 알람
# ─────────────────────────────────────────

async def send_morning_alarm(context: ContextTypes.DEFAULT_TYPE):
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    today = datetime.datetime.now(KST).strftime("%m/%d")

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🌅 *모닝 루틴 타임!*\n\n"
            f"*{today}* 오늘 하루의 루틴을 시작해보아요! 💪\n\n"
            f"👇 *이 메시지에 답장*으로 오늘 할 일들을 자유롭게 적어주세요."
        ),
        parse_mode="Markdown",
    )
    await db.save_prompt_message(msg.message_id, "morning", today)
    logger.info(f"Morning alarm sent | message_id={msg.message_id}")


async def send_evening_alarm(context: ContextTypes.DEFAULT_TYPE):
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    today = datetime.datetime.now(KST).strftime("%m/%d")

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🌙 *저녁 회고 타임!*\n\n"
            f"*{today}* 오늘 하루를 돌아보아요 ✨\n\n"
            f"👇 *이 메시지에 답장*으로 완료한 일들과 오늘의 소감을 적어주세요."
        ),
        parse_mode="Markdown",
    )
    await db.save_prompt_message(msg.message_id, "evening", today)
    logger.info(f"Evening alarm sent | message_id={msg.message_id}")


# ─────────────────────────────────────────
# 메시지 핸들러
# ─────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """알람 메시지에 답장하면 루틴으로 저장"""
    msg = update.message
    if not msg or not msg.reply_to_message:
        return

    reply_to_id = msg.reply_to_message.message_id
    prompt_type = await db.get_prompt_type(reply_to_id)

    if not prompt_type:
        return  # 봇 알람에 대한 답장이 아님

    user = msg.from_user
    name = user.full_name or user.username or str(user.id)
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

    await db.save_routine(
        user_id=user.id,
        user_name=name,
        date=today_str,
        routine_type=prompt_type,
        content=msg.text or "",
    )

    type_label = "아침 계획" if prompt_type == "morning" else "저녁 회고"
    await msg.reply_text(f"✅ *{name}*님의 {type_label}이 기록됐어요!", parse_mode="Markdown")
    logger.info(f"Routine saved | user={name}, type={prompt_type}")


# ─────────────────────────────────────────
# 커맨드 핸들러
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 안내 메시지
    await update.message.reply_text(
        "👋 루틴 봇에 오신 걸 환영합니다!\n\n"
        "📌 사용법\n"
        "• 매일 아침 8시 알람 → 답장으로 오늘 루틴 작성\n"
        "• 매일 저녁 9시 알람 → 답장으로 하루 회고 작성\n\n"
        "📎 명령어 (영어로 입력)\n"
        "/summary — 오늘 전체 루틴 AI 요약 보기\n"
        "/myroutine — 나의 오늘 루틴 확인\n"
        "/testmorning — 아침 알람 즉시 테스트\n"
        "/testevening — 저녁 알람 즉시 테스트",
    )

    # chatid는 별도 /chatid 명령으로만 확인 가능하게 유지


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """이 채팅의 ID를 알려줌 (TELEGRAM_CHAT_ID 설정할 때 사용)"""
    try:
        chat = update.effective_chat
        if not chat:
            await update.message.reply_text("채팅 정보를 가져올 수 없어요.")
            return
        chat_id = chat.id
        await update.message.reply_text(
            f"📌 이 채팅방 ID: {chat_id}\n\n"
            "환경변수 TELEGRAM_CHAT_ID 에 위 숫자를 넣으면 알람이 여기로 옵니다."
        )
        logger.info(f"chatid sent: chat_id={chat_id}")
    except Exception as e:
        logger.exception("chatid_command error")
        await update.message.reply_text(f"오류 발생: {e}")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    routines = await db.get_today_routines(today_str)

    if not routines:
        await update.message.reply_text("📭 오늘 기록된 루틴이 아직 없어요.")
        return

    thinking_msg = await update.message.reply_text("⏳ AI가 요약을 생성 중입니다...")
    try:
        summary = await generate_summary(routines, today_str)
        await thinking_msg.edit_text(summary, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Summary generation failed: {e}")
        await thinking_msg.edit_text("❌ 요약 생성 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.")


async def my_routine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    routines = await db.get_user_routines(user.id, today_str)

    if not routines:
        await update.message.reply_text("📭 오늘 기록된 루틴이 아직 없어요.")
        return

    today_label = datetime.datetime.now(KST).strftime("%m/%d")
    text = f"📋 *{user.full_name}님의 {today_label} 루틴*\n\n"
    for r in routines:
        emoji = "🌅" if r["routine_type"] == "morning" else "🌙"
        label = "아침 계획" if r["routine_type"] == "morning" else "저녁 회고"
        text += f"{emoji} *{label}*\n{r['content']}\n\n"

    await update.message.reply_text(text.strip(), parse_mode="Markdown")


async def test_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """개발/테스트용: 아침 알람 즉시 실행"""
    await send_morning_alarm(context)


async def test_evening(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """개발/테스트용: 저녁 알람 즉시 실행"""
    await send_evening_alarm(context)


# ─────────────────────────────────────────
# 앱 초기화 & 실행
# ─────────────────────────────────────────

async def post_init(application: Application):
    await db.init()
    logger.info("Database initialized")


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    app = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    # 커맨드 등록
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("myroutine", my_routine_command))
    app.add_handler(CommandHandler("testmorning", test_morning))
    app.add_handler(CommandHandler("testevening", test_evening))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 스케줄 등록 (KST 기준)
    morning_time = datetime.time(hour=8, minute=0, tzinfo=KST)
    evening_time = datetime.time(hour=21, minute=0, tzinfo=KST)
    app.job_queue.run_daily(send_morning_alarm, time=morning_time)
    app.job_queue.run_daily(send_evening_alarm, time=evening_time)

    logger.info("Bot started. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
