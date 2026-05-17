import os
import datetime
import logging

import pytz
from database import Database
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")

# 출석체크 설정
ATTENDANCE_MAX_PARTICIPANTS = int(os.environ.get("ATTENDANCE_MAX_PARTICIPANTS", "24"))
ATTENDANCE_ALLOW_EARLY_MINUTES = int(os.environ.get("ATTENDANCE_ALLOW_EARLY_MINUTES", "10"))
ATTENDANCE_START_TIME = datetime.time(hour=20, minute=50, tzinfo=KST)
ATTENDANCE_END_TIME = datetime.time(hour=23, minute=0, tzinfo=KST)
ATTENDANCE_LEADER_REMINDER_TIME = datetime.time(hour=21, minute=30, tzinfo=KST)
# 23시 세션 종료 시(정원 미달일 때만) 단톡에 보내는 안내 — 전체 문장을 한 번에 바꾸려면 환경변수 사용
_DEFAULT_SESSION_END_CHAT = "출석 세션이 종료되었습니다.\n오늘도 수고하셨습니다!"
ATTENDANCE_SESSION_END_MESSAGE = (
    os.environ.get("ATTENDANCE_SESSION_END_MESSAGE", _DEFAULT_SESSION_END_CHAT) or _DEFAULT_SESSION_END_CHAT
).strip()


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


def _parse_telegram_user_ids_env(env_key: str) -> list[int]:
    """쉼표 구분 텔레그램 user_id 목록 (예: 123,456,-789)."""
    raw = (os.environ.get(env_key) or "").strip()
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


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

    async def send_attendance_session_end(context: ContextTypes.DEFAULT_TYPE) -> None:
        """매주 일요일 23:00 — 정원(기본 24명) 미달일 때만 현황에서 버튼 제거 + 종료·인사 메시지 발송."""
        if datetime.datetime.now(KST).weekday() != 6:
            return

        chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
        if not chat_id_raw:
            return
        chat_id = int(chat_id_raw)

        session_date = datetime.datetime.now(KST).strftime("%Y-%m-%d")
        session = await db.attendance_get_session(session_date)
        if not session:
            return

        status_message_id = session.get("status_message_id")
        status_chat_id = session.get("status_message_chat_id")
        if not status_message_id or not status_chat_id:
            logger.warning("Attendance session end skipped | no status message | date=%s", session_date)
            return

        max_participants = int(session["max_participants"] or ATTENDANCE_MAX_PARTICIPANTS)
        records = await db.attendance_get_records(session_date)
        user_ids = [int(r["user_id"]) for r in records]
        display_names = await db.get_user_display_names(user_ids)

        display_lines: list[str] = []
        for idx, r in enumerate(records, start=1):
            uid = int(r["user_id"])
            name = Database.resolve_visible_name(
                uid, display_names, str(r.get("user_name") or "")
            )
            display_lines.append(f"{idx}. {name}")

        checked = len(records)
        if checked >= max_participants:
            logger.info(
                "Attendance session end skipped (정원 달성) | date=%s %s/%s",
                session_date,
                checked,
                max_participants,
            )
            return

        rate = _attendance_rate_percent(checked, max_participants)
        body = _attendance_status_text(display_lines, rate)

        try:
            await context.bot.edit_message_text(
                chat_id=int(status_chat_id),
                message_id=int(status_message_id),
                text=body,
                reply_markup=None,
            )
            logger.info(
                "Attendance session ended | date=%s checked=%s/%s",
                session_date,
                checked,
                max_participants,
            )
        except Exception:
            logger.exception(
                "Failed to finalize attendance message at session end | date=%s",
                session_date,
            )

        try:
            await context.bot.send_message(chat_id=chat_id, text=ATTENDANCE_SESSION_END_MESSAGE)
        except Exception:
            logger.exception("Failed to send attendance session end greeting | date=%s", session_date)

    async def _send_absentee_attendance_dms(
        context: ContextTypes.DEFAULT_TYPE, absent_ids: list[int], session_date: str
    ) -> tuple[int, int]:
        """미출석자에게 출석 독려 DM. (성공 수, 실패 수)"""
        if not absent_ids:
            return 0, 0
        dm_text = (
            "⏰ 출석체크 안내\n\n"
            f"오늘({session_date}) 일요일 출석체크가 진행 중입니다.\n"
            "아직 출석하지 않으셨다면, 단체방 출석 현황 메시지의 [출석] 버튼을 눌러 주세요.\n"
            "(출석 가능: 21시 ~ 23시, KST)"
        )
        sent, failed = 0, 0
        for uid in absent_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=dm_text)
                sent += 1
                logger.info("Attendance absentee DM sent | user_id=%s date=%s", uid, session_date)
            except Forbidden:
                failed += 1
                logger.info(
                    "Attendance absentee DM skipped | user_id=%s (1:1 대화 미연결)",
                    uid,
                )
            except Exception:
                failed += 1
                logger.exception("Attendance absentee DM failed | user_id=%s date=%s", uid, session_date)
        return sent, failed

    async def send_attendance_leader_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
        """매주 일요일 21:30 — 미출석자 DM + 조장에게 미출석 명단 알림."""
        if datetime.datetime.now(KST).weekday() != 6:
            return

        leader_raw = (os.environ.get("ATTENDANCE_LEADER_USER_ID") or "").strip()
        if not leader_raw:
            logger.warning("Attendance leader reminder skipped | ATTENDANCE_LEADER_USER_ID missing")
            return

        try:
            leader_id = int(leader_raw)
        except ValueError:
            logger.warning("Attendance leader reminder skipped | invalid ATTENDANCE_LEADER_USER_ID")
            return

        session_date = datetime.datetime.now(KST).strftime("%Y-%m-%d")
        session = await db.attendance_get_session(session_date)
        if not session:
            logger.info("Attendance leader reminder skipped | no session | date=%s", session_date)
            return

        max_participants = int(session["max_participants"] or ATTENDANCE_MAX_PARTICIPANTS)
        records = await db.attendance_get_records(session_date)
        checked_ids = {int(r["user_id"]) for r in records}

        roster_source_date, roster_records = await db.attendance_get_roster_from_latest_full_session(
            max_participants
        )
        roster_source_note = ""
        if roster_records:
            roster_ids = [int(r["user_id"]) for r in roster_records]
            roster_source_note = f"\n(명단 기준: {roster_source_date} 전원 출석일)"
        else:
            roster_ids = _parse_telegram_user_ids_env("ATTENDANCE_ROSTER_USER_IDS")
            if roster_ids:
                roster_source_note = "\n(명단 기준: 환경변수 ATTENDANCE_ROSTER_USER_IDS)"

        if not roster_ids:
            logger.warning(
                "Attendance leader reminder skipped | no roster "
                "(no full session in DB and ATTENDANCE_ROSTER_USER_IDS empty)"
            )
            return

        absent_ids = [uid for uid in roster_ids if uid not in checked_ids]

        roster_name_by_id = {
            int(r["user_id"]): str(r.get("user_name") or "") for r in roster_records
        }
        all_ids = sorted(set(roster_ids) | checked_ids)
        display_names = await db.get_user_display_names(all_ids)

        def _label(uid: int) -> str:
            fallback = roster_name_by_id.get(uid, "")
            for r in records:
                if int(r["user_id"]) == uid:
                    fallback = str(r.get("user_name") or "") or fallback
                    break
            return Database.resolve_visible_name(uid, display_names, fallback or str(uid))

        checked = len(checked_ids & set(roster_ids))
        roster_total = len(roster_ids)
        date_label = session_date

        dm_sent, dm_failed = await _send_absentee_attendance_dms(context, absent_ids, session_date)

        if not absent_ids:
            text = (
                f"✅ 출석체크 알림 ({date_label})\n\n"
                f"예정 {roster_total}명 전원 출석했습니다. ({checked}/{roster_total})"
                f"{roster_source_note}"
            )
        else:
            absent_lines = "\n".join(f"- {name}" for name in sorted(_label(uid) for uid in absent_ids))
            dm_note = f"\n(미출석자 DM: 전송 {dm_sent}명"
            if dm_failed:
                dm_note += f", 미전송 {dm_failed}명"
            dm_note += ")"
            text = (
                f"⏰ 출석체크 미출석 알림 ({date_label})\n\n"
                f"현재 {checked}/{roster_total}명 출석 · 미출석 {len(absent_ids)}명"
                f"{roster_source_note}{dm_note}\n\n"
                f"{absent_lines}"
            )

        try:
            await context.bot.send_message(chat_id=leader_id, text=text)
            logger.info(
                "Attendance leader reminder sent | date=%s leader_id=%s absent=%s dm_sent=%s dm_failed=%s",
                session_date,
                leader_id,
                len(absent_ids),
                dm_sent,
                dm_failed,
            )
        except Exception:
            logger.exception(
                "Failed to send attendance leader reminder | date=%s leader_id=%s",
                session_date,
                leader_id,
            )

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
            name = Database.resolve_visible_name(
                uid, display_names, str(r.get("user_name") or "")
            )
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
            name = Database.resolve_visible_name(
                uid, display_names, str(r.get("user_name") or "")
            )
            lines.append(f"{idx}. {name}")

        text = f"📋 출석 상태 ({session_date}) (출석율 {rate}%)\n\n"
        if lines:
            text += "\n".join(lines)
        await update.message.reply_text(text.strip())

    app.add_handler(CommandHandler("status", attendance_status_command))
    app.add_handler(CommandHandler("attendanceguide", attendance_help_command))
    app.add_handler(CallbackQueryHandler(attendance_callback, pattern=r"^attendance:"))

    # 스케줄 등록: 매주 일요일 20:50 시작, 21:30 조장 알림, 23:00 마감
    app.job_queue.run_daily(send_attendance_start, time=ATTENDANCE_START_TIME)
    app.job_queue.run_daily(send_attendance_leader_reminder, time=ATTENDANCE_LEADER_REMINDER_TIME)
    app.job_queue.run_daily(send_attendance_session_end, time=ATTENDANCE_END_TIME)


async def attendance_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """출석체크 안내 (개인방이 아닌 단체방에서만 권장)."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("출석체크 사용법은 단체방에서 확인해 주세요.")
        return

    help_text = (
        "📌 출석체크 사용법 안내\n\n"
        "- 출석 가능 시간: 일요일 21시 ~ 23시 (Asia/Seoul)\n"
        f"- 목표 인원: {ATTENDANCE_MAX_PARTICIPANTS}명\n\n"
        "✅ 출석하기\n"
        "- 단체방에서 출석 현황 메시지의 [출석] 버튼을 누르면 출석 기록과 함께 출석 명단이 업데이트 됩니다.\n\n"
        "ℹ️ 안내 메시지\n"
        "- 시간 외: `출석 시간이 아닙니다.`\n"
        "- 중복: `이미 출석 처리되었습니다.`\n"
        "- 완료 후: 정원이 꽉 차면 버튼이 사라지고, 새 메시지로 `100% 출석 완료! 오늘도 수고하셨습니다!`가 전송됩니다.\n"
        "- 23시 종료: 정원(예: 24명)이 안 찼을 때만 출석 현황에서 버튼이 사라지고, "
        "`출석 세션이 종료되었습니다.` / `오늘도 수고하셨습니다!` 안내가 올라옵니다. (정원 달성 시에는 생략)"
    )

    await update.message.reply_text(help_text, parse_mode="Markdown")

