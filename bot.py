import os
import json
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
            f"🌅 *오늘 루틴을 적어볼까요?*\n\n"
            f"*{today}* 오늘 하루에 실천하고 싶은/실천한 루틴을 자유롭게 적어주세요. 💪\n\n"
            f"👇 *이 메시지에 답장*으로 오늘 루틴을 적어주세요."
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
            f"🌙 *오늘 루틴, 마무리해볼까요?*\n\n"
            f"*{today}* 아직 오늘 루틴을 적지 않았다면 지금 적어주세요. ✨\n\n"
            f"👇 *이 메시지에 답장*으로 오늘 실천한 루틴을 적어주세요."
        ),
        parse_mode="Markdown",
    )
    await db.save_prompt_message(msg.message_id, "evening", today)
    logger.info(f"Evening alarm sent | message_id={msg.message_id}")


# ─────────────────────────────────────────
# 메시지 핸들러
# ─────────────────────────────────────────

def _parse_selection_reply(text: str, items: list[str]) -> list[str]:
    """메시지에서 번호(어제 루틴 인덱스)와 새 텍스트를 파싱해 저장할 content 목록 반환."""
    if not (text or text.strip()):
        return []
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    result = []
    n = len(items)
    for p in parts:
        if p.isdigit() and 1 <= int(p) <= n:
            result.append(items[int(p) - 1])
        elif p:
            result.append(p)
    return result


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """알림/시작 메시지에 답장하면 루틴으로 저장. 어제 루틴이 있으면 번호 선택 메시지를 보낸 뒤, 그 답장으로 저장."""
    msg = update.message
    if not msg or not msg.reply_to_message:
        return

    reply_to_id = msg.reply_to_message.message_id
    user = msg.from_user
    name = user.full_name or user.username or str(user.id)
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    yesterday = (datetime.datetime.now(KST) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # 1) 어제 루틴 선택용 메시지에 대한 답장인지 확인
    sel = await db.get_selection_prompt(reply_to_id)
    if sel:
        items = json.loads(sel["items_json"])
        to_save = _parse_selection_reply(msg.text or "", items)
        for content in to_save:
            await db.save_routine(
                user_id=user.id,
                user_name=name,
                date=today_str,
                routine_type=sel["prompt_type"],
                content=content,
            )
        await db.delete_selection_prompt(reply_to_id)
        count = len(to_save)
        await msg.reply_text(f"✅ *{name}*님의 오늘 루틴 {count}개 기록했어요!", parse_mode="Markdown")
        logger.info(f"Routine saved from selection | user={name}, count={count}")
        return

    # 2) 알람/시작 프롬프트에 대한 답장인지 확인
    prompt_type = await db.get_prompt_type(reply_to_id)
    if not prompt_type:
        return  # 봇 알람에 대한 답장이 아님

    # 3) 어제 루틴이 있으면 번호 선택 메시지 전송 후 대기
    yesterday_routines = await db.get_user_routines(user.id, yesterday)
    seen_keys = set()
    items = []
    for row in yesterday_routines:
        c = (row.get("content") or "").strip()
        key = "".join(c.split()).lower()
        if key and key not in seen_keys:
            seen_keys.add(key)
            items.append(c)

    if items:
        lines = [f"{i}. {item}" for i, item in enumerate(items, 1)]
        list_text = "\n".join(lines)
        sent = await msg.reply_text(
            f"📋 *어제 루틴에서 선택* (번호 입력 예: 1,3) 또는 새로 적어주세요.\n\n"
            f"{list_text}\n\n"
            f"👇 *이 메시지에 답장*으로 번호 또는 새 루틴을 적어주세요.",
            parse_mode="Markdown",
        )
        await db.save_selection_prompt(
            message_id=sent.message_id,
            user_id=user.id,
            chat_id=msg.chat_id,
            selection_date=yesterday,
            items_json=json.dumps(items, ensure_ascii=False),
            prompt_type=prompt_type,
        )
        logger.info(f"Selection prompt sent | user={name}, items={len(items)}")
        return

    # 4) 어제 루틴 없음 → 기존처럼 한 번에 저장
    await db.save_routine(
        user_id=user.id,
        user_name=name,
        date=today_str,
        routine_type=prompt_type,
        content=msg.text or "",
    )
    await msg.reply_text(f"✅ *{name}*님의 오늘 루틴이 기록됐어요!", parse_mode="Markdown")
    logger.info(f"Routine saved | user={name}, type={prompt_type}")


# ─────────────────────────────────────────
# 커맨드 핸들러
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 안내 + 바로 오늘 루틴 입력 유도
    await update.message.reply_text(
        "👋 루틴 봇에 오신 걸 환영합니다!\n\n"
        "📌 사용법\n"
        "• 매일 아침 8시 알람 → 답장으로 오늘 루틴 작성\n"
        "• 매일 저녁 9시 알람 → 아직 못 쓴 사람을 위한 리마인드 알림\n\n"
        "아래 메시지에 오늘의 루틴을 바로 적어보세요. 😊\n\n"
        "나중에 추가로 적고 싶으면 /add 를 입력하세요.",
    )

    # 오늘 루틴을 바로 받을 프롬프트 메시지 전송
    chat = update.effective_chat
    if chat:
        today_label = datetime.datetime.now(KST).strftime("%m/%d")
        msg = await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"📝 *오늘 루틴을 작성해보세요!*\n\n"
                f"*{today_label}* 오늘 하루에 실천하고 싶은/실천한 루틴을 자유롭게 적어주세요.\n\n"
                f"👇 *이 메시지에 답장*으로 적어주시면 기록됩니다."
            ),
            parse_mode="Markdown",
        )
        await db.save_prompt_message(msg.message_id, "morning", today_label)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """오늘 루틴 추가: /start 이후에 다시 루틴 작성 프롬프트를 띄움"""
    chat = update.effective_chat
    if not chat:
        return
    today_label = datetime.datetime.now(KST).strftime("%m/%d")
    msg = await context.bot.send_message(
        chat_id=chat.id,
        text=(
            f"📝 *오늘 루틴을 추가해주세요!*\n\n"
            f"*{today_label}* 추가로 실천하고 싶은/실천한 루틴을 적어주세요.\n\n"
            f"👇 *이 메시지에 답장*으로 적어주시면 기록됩니다."
        ),
        parse_mode="Markdown",
    )
    await db.save_prompt_message(msg.message_id, "morning", today_label)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """오늘 날짜 루틴만 전부 삭제"""
    user = update.message.from_user
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    deleted = await db.delete_user_routines_for_date(user.id, today_str)
    if deleted > 0:
        await update.message.reply_text(f"✅ 오늘 기록된 루틴 {deleted}개가 삭제되었어요.")
    else:
        await update.message.reply_text("📭 오늘 삭제할 루틴이 없어요.")


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
    if not os.environ.get("ANTHROPIC_API_KEY"):
        await update.message.reply_text(
            "❌ 요약 기능을 쓰려면 서버에 ANTHROPIC_API_KEY 환경변수가 설정되어 있어야 해요."
        )
        return

    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    routines = await db.get_today_routines(today_str)

    if not routines:
        await update.message.reply_text("📭 오늘 기록된 루틴이 아직 없어요.")
        return

    thinking_msg = await update.message.reply_text("⏳ AI가 요약을 생성 중입니다...")
    try:
        summary = await generate_summary(routines, today_str)
        try:
            await thinking_msg.edit_text(summary, parse_mode="Markdown")
        except Exception:
            await thinking_msg.edit_text(summary)
    except ValueError as e:
        await thinking_msg.edit_text(f"❌ {e}")
    except Exception as e:
        logger.exception("Summary generation failed")
        await thinking_msg.edit_text(
            "❌ 요약 생성 중 오류가 발생했어요. 잠시 후 다시 시도해주세요. "
            "(관리자: Railway 로그에서 Summary generation failed 확인)"
        )


async def week_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """지난 7일간 통계"""
    today = datetime.datetime.now(KST).date()
    start_date = today - datetime.timedelta(days=6)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    top_users = await db.get_top_users(start_str, end_str, limit=3)
    top_routines = await db.get_top_routines(start_str, end_str, limit=3)

    if not top_users and not top_routines:
        await update.message.reply_text("📭 지난 7일 동안 기록된 루틴이 아직 없어요.")
        return

    text = "📊 *지난 7일 루틴 통계*\n\n"

    if top_users:
        text += "👤 *가장 많이 기록한 사람 TOP 3*\n"
        for idx, row in enumerate(top_users, start=1):
            text += f"{idx}위 {row['user_name']} ({row['count']}회)\n"
        text += "\n"

    if top_routines:
        text += "✅ *가장 많이 기록된 루틴 TOP 3*\n"
        for idx, row in enumerate(top_routines, start=1):
            content = row["content"]
            text += f"{idx}위 {content} ({row['count']}회)\n"

    await update.message.reply_text(text.strip(), parse_mode="Markdown")


async def month_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """지난 30일간 통계"""
    today = datetime.datetime.now(KST).date()
    start_date = today - datetime.timedelta(days=29)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    top_users = await db.get_top_users(start_str, end_str, limit=5)
    top_routines = await db.get_top_routines(start_str, end_str, limit=5)

    if not top_users and not top_routines:
        await update.message.reply_text("📭 지난 30일 동안 기록된 루틴이 아직 없어요.")
        return

    text = "📊 *지난 30일 루틴 통계*\n\n"

    if top_users:
        text += "👤 *가장 많이 기록한 사람 TOP 5*\n"
        for idx, row in enumerate(top_users, start=1):
            text += f"{idx}위 {row['user_name']} ({row['count']}회)\n"
        text += "\n"

    if top_routines:
        text += "✅ *가장 많이 기록된 루틴 TOP 5*\n"
        for idx, row in enumerate(top_routines, start=1):
            content = row["content"]
            text += f"{idx}위 {content} ({row['count']}회)\n"

    await update.message.reply_text(text.strip(), parse_mode="Markdown")


async def my_routine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    routines = await db.get_user_routines(user.id, today_str)

    if not routines:
        await update.message.reply_text("📭 오늘 기록된 루틴이 아직 없어요.")
        return

    # 타입별로 묶어서 한 줄에 쉼표로 표시
    by_type = {"morning": [], "evening": []}
    for r in routines:
        t = r.get("routine_type") or "morning"
        if t not in by_type:
            by_type[t] = []
        by_type[t].append((r.get("content") or "").strip())

    today_label = datetime.datetime.now(KST).strftime("%m/%d")
    text = f"📋 *{user.full_name}님의 {today_label} 루틴 기록*\n\n"
    if by_type["morning"]:
        text += f"🌅 *오전 기록*\n{', '.join(by_type['morning'])}\n\n"
    if by_type["evening"]:
        text += f"🌙 *저녁 기록*\n{', '.join(by_type['evening'])}\n\n"

    await update.message.reply_text(text.strip(), parse_mode="Markdown")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """비밀번호 확인 후 전체 데이터 초기화. 사용법: /reset 비밀번호"""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("사용법: /reset 비밀번호")
        return
    password = os.environ.get("RESET_PASSWORD", "0537")
    if context.args[0] != password:
        await update.message.reply_text("❌ 비밀번호가 올바르지 않아요.")
        return
    await db.delete_all_data()
    await update.message.reply_text("✅ 모든 데이터가 초기화되었어요.")
    logger.info("Full reset executed by user_id=%s", update.effective_user.id)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """해당 날짜의 내 루틴 조회. 사용법: /search YYYY-MM-DD"""
    if not context.args or len(context.args) != 1:
        await update.message.reply_text("사용법: /search YYYY-MM-DD (예: /search 2025-03-15)")
        return
    date_str = context.args[0].strip()
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("날짜 형식이 올바르지 않아요. YYYY-MM-DD 로 입력해 주세요. (예: 2025-03-15)")
        return
    user = update.message.from_user
    routines = await db.get_user_routines(user.id, date_str)
    if not routines:
        await update.message.reply_text(f"📭 {date_str}에 기록된 루틴이 없어요.")
        return
    by_type = {"morning": [], "evening": []}
    for r in routines:
        t = r.get("routine_type") or "morning"
        if t not in by_type:
            by_type[t] = []
        by_type[t].append((r.get("content") or "").strip())
    date_label = f"{date_str[5:7]}/{date_str[8:]}"
    text = f"📋 *{user.full_name}님의 {date_label} 루틴 기록*\n\n"
    if by_type["morning"]:
        text += f"🌅 *오전 기록*\n{', '.join(by_type['morning'])}\n\n"
    if by_type["evening"]:
        text += f"🌙 *저녁 기록*\n{', '.join(by_type['evening'])}\n\n"
    await update.message.reply_text(text.strip(), parse_mode="Markdown")


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
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("weekstats", week_stats_command))
    app.add_handler(CommandHandler("monthstats", month_stats_command))
    app.add_handler(CommandHandler("myroutine", my_routine_command))
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
