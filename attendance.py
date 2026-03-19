import os
import datetime
import logging

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")

# 출석체크 설정
ATTENDANCE_MAX_PARTICIPANTS = int(os.environ.get("ATTENDANCE_MAX_PARTICIPANTS", "24"))
ATTENDANCE_ALLOW_EARLY_MINUTES = int(os.environ.get("ATTENDANCE_ALLOW_EARLY_MINUTES", "10"))
ATTENDANCE_START_TIME = datetime.time(hour=20, minute=50, tzinfo=KST)
ATTENDANCE_END_TIME = datetime.time(hour=23, minute=0, tzinfo=KST)


async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data if context.job else {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if chat_id is None or message_id is None:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        logger.exception("Failed to delete ephemeral message")


async def _send_ephemeral_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    ttl_seconds: int = 90,
) -> None:
    # 안내 문구는 "토스트(toast)"처럼 자동 사라지는 UI가 아니라,
    # 일반 메시지이므로 삭제 로직 없이 그대로 둔다.
    await context.bot.send_message(chat_id=chat_id, text=text)


def _attendance_callback_data(session_date: str) -> str:
    return f"attendance:{session_date}"


def _parse_attendance_callback_data(data: str) -> str | None:
    if not data or not data.startswith("attendance:"):
        return None
    return data.split(":", 1)[1].strip() or None


def _get_attendance_time_window(session_date: str) -> tuple[datetime.datetime, datetime.datetime]:
    """허용 시간 계산. session_date는 YYYY-MM-DD (KST 기준)."""
    base_date = datetime.datetime.strptime(session_date, "%Y-%m-%d").date()

    # pytz localize로 정확한 KST 적용
    start_base = KST.localize(datetime.datetime.combine(base_date, datetime.time(ATTENDANCE_START_TIME.hour, ATTENDANCE_START_TIME.minute, 0)))
    start_dt = start_base - datetime.timedelta(minutes=ATTENDANCE_ALLOW_EARLY_MINUTES)

    end_dt = KST.localize(datetime.datetime.combine(base_date, datetime.time(ATTENDANCE_END_TIME.hour, ATTENDANCE_END_TIME.minute, 0)))
    return start_dt, end_dt


def _attendance_allowed(now_kst: datetime.datetime, session_date: str) -> bool:
    start_dt, end_dt = _get_attendance_time_window(session_date)
    return start_dt <= now_kst < end_dt


def _attendance_rate_percent(checked: int, max_participants: int) -> int:
    if max_participants <= 0:
        return 0
    return int(checked * 100 / max_participants)


def _attendance_status_text(display_lines: list[str], rate_percent: int) -> str:
    header = f"📋 출석 현황 (출석율 {rate_percent}%)"
    if not display_lines:
        return header
    return header + "\n\n" + "\n".join(display_lines)


def _attendance_keyboard(session_date: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="출석 버튼",
                    callback_data=_attendance_callback_data(session_date),
                )
            ]
        ]
    )


def register_attendance(
    app,
    db,
    is_allowed_user,
):
    """
    app: telegram.ext.Application
    db: Database instance
    is_allowed_user: async (context, user_id) -> bool
    """

    async def send_attendance_start(context: ContextTypes.DEFAULT_TYPE) -> None:
        """매주 일요일 20:50에 출석체크 세션 시작 메시지를 전송."""
        if datetime.datetime.now(KST).weekday() != 6:
            return

        chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
        if not chat_id_raw:
            return
        chat_id = int(chat_id_raw)

        now = datetime.datetime.now(KST)
        session_date = now.strftime("%Y-%m-%d")

        created = await db.attendance_create_session(
            session_date=session_date,
            max_participants=ATTENDANCE_MAX_PARTICIPANTS,
        )
        if not created:
            return

        await _send_ephemeral_message(
            context,
            chat_id=chat_id,
            text=(
                "📋 [출석체크 시작]\n"
                "금일 세션 출석체크가 시작되었습니다. (오후 21시 ~ 23시)\n"
                "아래 출석 버튼을 눌러 출석해 주세요!"
            ),
            ttl_seconds=120,
        )

        status_text = _attendance_status_text([], 0)
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=status_text,
            reply_markup=_attendance_keyboard(session_date),
        )
        await db.attendance_set_status_message(
            session_date=session_date,
            chat_id=chat_id,
            message_id=sent.message_id,
        )
        logger.info("Attendance session started | session_date=%s", session_date)

    async def attendance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """출석 버튼 클릭 처리 (CallbackQuery)."""
        query = update.callback_query
        if not query:
            return

        session_date = _parse_attendance_callback_data(query.data or "")
        if not session_date:
            await query.answer()
            return

        chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
        if not chat_id_raw:
            await query.answer()
            return
        chat_id = int(chat_id_raw)

        now = datetime.datetime.now(KST)
        if not _attendance_allowed(now, session_date=session_date):
            await query.answer("출석 시간이 아닙니다.")
            return

        if not await is_allowed_user(context, query.from_user.id):
            await query.answer("참여 권한이 없습니다.")
            return

        session = await db.attendance_get_session(session_date)
        if not session:
            await query.answer("출석 시간이 아닙니다.")
            return

        max_participants = int(session["max_participants"] or ATTENDANCE_MAX_PARTICIPANTS)
        status_message_id = session.get("status_message_id")
        status_chat_id = session.get("status_message_chat_id")
        if not status_message_id or not status_chat_id:
            await query.answer()
            return

        checked = await db.attendance_get_count(session_date)
        if checked >= max_participants:
            # 요구사항: 완료 이후 클릭은 토스트 불필요
            await query.answer()
            return

        user_name = query.from_user.full_name or query.from_user.username or str(query.from_user.id)
        added = await db.attendance_add_record(session_date, query.from_user.id, user_name)
        if not added:
            await query.answer("이미 출석 처리되었습니다.")
            return

        checked = await db.attendance_get_count(session_date)
        records = await db.attendance_get_records(session_date)
        user_ids = [int(r["user_id"]) for r in records]
        display_names = await db.get_user_display_names(user_ids)

        display_lines: list[str] = []
        for idx, r in enumerate(records, start=1):
            uid = int(r["user_id"])
            dn = (display_names.get(uid) or "").strip()
            fallback = str(r.get("user_name") or "이름없음")
            name = dn or fallback
            display_lines.append(f"{idx}. {name}")

        rate = _attendance_rate_percent(checked, max_participants)
        new_text = _attendance_status_text(display_lines, rate)

        if checked >= max_participants:
            # 정원 달성: 버튼 제거 + 축하 메시지(새 메시지) 발송
            await context.bot.edit_message_text(
                chat_id=status_chat_id,
                message_id=status_message_id,
                text=new_text,
                reply_markup=None,
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎉 100% 출석 완료! 오늘도 수고하셨습니다! 💪🌟",
            )
            await query.answer()
            return

        await context.bot.edit_message_text(
            chat_id=status_chat_id,
            message_id=status_message_id,
            text=new_text,
            reply_markup=_attendance_keyboard(session_date),
        )
        await query.answer()

    async def attendance_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """(관리자용) 현재 세션 출석 상태 확인. (가이드에는 노출하지 않음)"""
        chat = update.effective_chat
        if not chat or chat.type == "private":
            await update.message.reply_text("출석 상태는 단체방에서 확인해 주세요.")
            return

        now = datetime.datetime.now(KST)
        delta = (now.weekday() - 6) % 7
        session_date = (now.date() - datetime.timedelta(days=delta)).strftime("%Y-%m-%d")

        session = await db.attendance_get_session(session_date)
        if not session:
            await update.message.reply_text("📭 아직 출석 세션이 시작되지 않았어요.")
            return

        max_participants = int(session["max_participants"] or ATTENDANCE_MAX_PARTICIPANTS)
        checked = await db.attendance_get_count(session_date)
        rate = _attendance_rate_percent(checked, max_participants)

        records = await db.attendance_get_records(session_date)
        user_ids = [int(r["user_id"]) for r in records]
        display_names = await db.get_user_display_names(user_ids)

        lines: list[str] = []
        for idx, r in enumerate(records, start=1):
            uid = int(r["user_id"])
            dn = (display_names.get(uid) or "").strip()
            fallback = str(r.get("user_name") or "이름없음")
            name = dn or fallback
            lines.append(f"{idx}. {name}")

        text = f"📋 출석 상태 ({session_date}) (출석율 {rate}%)\n\n"
        if lines:
            text += "\n".join(lines)
        await update.message.reply_text(text.strip())

    app.add_handler(CommandHandler("status", attendance_status_command))
    app.add_handler(CommandHandler("attendanceguide", attendance_help_command))
    app.add_handler(CallbackQueryHandler(attendance_callback, pattern=r"^attendance:"))

    # 스케줄 등록: 매주 일요일 20:50
    app.job_queue.run_daily(send_attendance_start, time=ATTENDANCE_START_TIME)


async def attendance_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """출석체크 사용법 안내 (개인방이 아닌 단체방에서만 권장)."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("출석체크 사용법은 단체방에서 확인해 주세요.")
        return

    # 허용 시작 시각은 '정규 시작 - 여유분'으로 계산
    base_dt = datetime.datetime(2000, 1, 1, ATTENDANCE_START_TIME.hour, ATTENDANCE_START_TIME.minute)
    allow_start = (base_dt - datetime.timedelta(minutes=ATTENDANCE_ALLOW_EARLY_MINUTES)).strftime("%H:%M")
    end_time = ATTENDANCE_END_TIME.strftime("%H:%M")

    help_text = (
        "📌 출석체크 사용법\n\n"
        f"- 출석 가능 시간: 일요일 {allow_start} ~ {end_time} (Asia/Seoul)\n"
        f"- 목표 인원: {ATTENDANCE_MAX_PARTICIPANTS}명\n\n"
        "✅ 출석하기\n"
        "- 단체방에서 출석 메시지의 버튼을 눌러 출석해 주세요.\n\n"
        "ℹ️ 안내 메시지\n"
        "- 시간 외: `출석 시간이 아닙니다.`\n"
        "- 중복: `이미 출석 처리되었습니다.`\n"
        "- 완료 후: 정원이 꽉 차면 버튼이 사라지고, 새 메시지로 `100% 출석 완료! 오늘도 수고하셨습니다!`가 전송됩니다.\n\n"
        "참고: 출석 시작 안내는 위(상단)에 잠시 표시된 뒤 사라집니다."
    )

    await update.message.reply_text(help_text, parse_mode="Markdown")

