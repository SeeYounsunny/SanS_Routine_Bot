import os
import json
import logging
import datetime
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from database import Database
import attendance
from ai_summary import generate_summary

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")
db = Database()

# evening 알람은 "내일부터" 시작하도록, 최초 스케줄 실행 전 오늘/내일 기준을 제어함
EVENING_ALARM_START_DATE: str | None = None

# 출석체크 설정 (환경변수로 조절 가능)
ATTENDANCE_MAX_PARTICIPANTS = int(os.environ.get("ATTENDANCE_MAX_PARTICIPANTS", "24"))
# 정규 시작: 20:50, 허용 시작: 10분 일찍(20:40)
ATTENDANCE_ALLOW_EARLY_MINUTES = int(os.environ.get("ATTENDANCE_ALLOW_EARLY_MINUTES", "10"))
ATTENDANCE_START_TIME = datetime.time(hour=20, minute=50, tzinfo=KST)
ATTENDANCE_END_TIME = datetime.time(hour=23, minute=0, tzinfo=KST)


# ─────────────────────────────────────────
# 예약 알람
# ─────────────────────────────────────────

def _bot_tme_link() -> str:
    """1:1 루틴 입력 안내용 봇 링크. TELEGRAM_BOT_USERNAME 사용, 없으면 sans1_healthroutinebot."""
    username = (os.environ.get("TELEGRAM_BOT_USERNAME") or "sans1_healthroutinebot").strip()
    return f"https://t.me/{username}"


async def send_morning_alarm(context: ContextTypes.DEFAULT_TYPE):
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    today_label = datetime.datetime.now(KST).strftime("%m/%d")
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🌅 오늘 루틴을 적어볼까요?\n\n"
            f"{today_label} 오늘 하루에 실천하고 싶은/실천한 루틴을 자유롭게 적어주세요. 💪\n\n"
            f"아래 링크 클릭해서 각자 루틴 입력해 주세요.\n{_bot_tme_link()}"
        ),
    )
    await db.save_prompt_message(msg.message_id, "morning", today_str)
    logger.info(f"Morning alarm sent | message_id={msg.message_id}")


async def send_evening_alarm(context: ContextTypes.DEFAULT_TYPE):
    # "내일부터" 시작: 최초 실행일(today)은 스킵하고 다음날부터 동작
    if EVENING_ALARM_START_DATE:
        today = datetime.datetime.now(KST).strftime("%Y-%m-%d")
        if today < EVENING_ALARM_START_DATE:
            return
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    today_label = datetime.datetime.now(KST).strftime("%m/%d")
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🌙 오늘 루틴, 마무리해볼까요?\n\n"
            f"{today_label} 아직 오늘 루틴을 적지 않았다면 지금 적어주세요. ✨\n\n"
            f"아래 링크 클릭해서 각자 루틴 입력해 주세요.\n{_bot_tme_link()}"
        ),
    )
    await db.save_prompt_message(msg.message_id, "evening", today_str)
    logger.info(f"Evening alarm sent | message_id={msg.message_id}")


async def send_lunch_reminder(context: ContextTypes.DEFAULT_TYPE):
    """점심시간 단체방 리마인드: 오늘 입력한 사람별 루틴 내용 한 번 공지"""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        return
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    routines = await db.get_today_routines(today_str)

    by_user: dict[int, dict[str, object]] = {}
    for r in routines:
        uid = int(r.get("user_id") or 0)
        name = (r.get("user_name") or "").strip() or "이름 없음"
        content = (r.get("content") or "").strip()
        if uid not in by_user:
            by_user[uid] = {"fallback_name": name, "contents": []}
        if content:
            (by_user[uid]["contents"]).append(content)  # type: ignore[union-attr]

    if not by_user:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "☀️ 점심 리마인드\n\n"
                "아직 오늘 루틴을 입력한 사람이 없어요.\n"
                "봇과 1:1 대화에서 /add 를 입력해 주세요! 💪"
            ),
        )
    else:
        display_names = await db.get_user_display_names(list(by_user.keys()))
        lines = []
        items = []
        for uid, data in by_user.items():
            dn = (display_names.get(uid) or "").strip()
            fallback = str(data.get("fallback_name") or "이름 없음")
            contents = list(data.get("contents") or [])
            items.append((dn or fallback, contents))
        for name, contents in sorted(items, key=lambda x: x[0]):
            lines.append(f"• [{name}] {', '.join(contents)}")
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"☀️ 점심 리마인드 — 오늘 입력한 루틴 (참여인원 {len(items)}명)\n\n"
                + "\n".join(lines)
                + "\n\n"
                + "아직 입력 안 하신 분들은 지금 기록해주세요.\n"
                + f"{_bot_tme_link()}"
            ),
        )
    logger.info("Lunch reminder sent")


async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data if context.job else {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if chat_id is None or message_id is None:
        return
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        # 삭제 실패해도 출석/루틴 기능 동작은 지속
        logger.exception("Failed to delete ephemeral message")


async def _send_ephemeral_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    ttl_seconds: int = 60,
):
    msg = await context.bot.send_message(chat_id=chat_id, text=text)
    context.job_queue.run_once(
        _delete_message_job,
        when=ttl_seconds,
        data={"chat_id": chat_id, "message_id": msg.message_id},
    )


def _attendance_callback_data(session_date: str) -> str:
    return f"attendance:{session_date}"


def _parse_attendance_callback_data(data: str) -> str | None:
    if not data or not data.startswith("attendance:"):
        return None
    return data.split(":", 1)[1].strip() or None


def _get_attendance_time_window(session_date: str):
    """허용 시간 계산. session_date는 YYYY-MM-DD (KST 기준)."""
    base = datetime.datetime.strptime(session_date, "%Y-%m-%d").replace(tzinfo=KST)
    allow_start = (base + datetime.timedelta(minutes=-ATTENDANCE_ALLOW_EARLY_MINUTES)).time()
    # 위 줄은 time()만 취하는데 기준이 base의 00:00이라 의도대로 동작하지 않음.
    # 아래에서 분 단위 오프셋으로 계산해서 정확히 맞춤.
    start_dt = base.replace(
        hour=ATTENDANCE_START_TIME.hour,
        minute=ATTENDANCE_START_TIME.minute,
        second=0,
        microsecond=0,
    ) - datetime.timedelta(minutes=ATTENDANCE_ALLOW_EARLY_MINUTES)
    end_dt = base.replace(
        hour=ATTENDANCE_END_TIME.hour,
        minute=ATTENDANCE_END_TIME.minute,
        second=0,
        microsecond=0,
    )
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
        [[InlineKeyboardButton(text="출석 버튼", callback_data=_attendance_callback_data(session_date))]]
    )


async def send_attendance_start(context: ContextTypes.DEFAULT_TYPE):
    """매주 일요일 20:50에 출석체크 세션 시작 메시지를 전송."""
    if datetime.datetime.now(KST).weekday() != 6:
        return

    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id_raw:
        return
    chat_id = int(chat_id_raw)

    now = datetime.datetime.now(KST)
    session_date = now.strftime("%Y-%m-%d")  # 해당 일요일(세션 기준)

    # 세션이 이미 존재하면 재전송하지 않음
    created = await db.attendance_create_session(session_date=session_date, max_participants=ATTENDANCE_MAX_PARTICIPANTS)
    if not created:
        return

    await _send_ephemeral_message(
        context,
        chat_id=chat_id,
        text=(
            "📌 [출석체크 시작]\n"
            f"금일 세션 출석체크가 시작되었습니다. (오후 {ATTENDANCE_START_TIME.strftime('%H:%M')} ~ {ATTENDANCE_END_TIME.strftime('%H:%M')})\n"
            "아래 출석 버튼을 눌러 출석해 주세요!"
        ),
        ttl_seconds=90,
    )

    initial_rate = 0
    status_text = _attendance_status_text([], initial_rate)
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


async def attendance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """출석 버튼 클릭 처리 (CallbackQuery)."""
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    session_date = _parse_attendance_callback_data(data)
    if not session_date:
        await query.answer()
        return

    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id_raw:
        await query.answer()
        return
    chat_id = int(chat_id_raw)

    # 시간 체크
    now = datetime.datetime.now(KST)
    if not _attendance_allowed(now, session_date=session_date):
        await query.answer("출석 시간이 아닙니다.")
        return

    # 단체방 멤버만 허용
    if not await _is_allowed_user(context, query.from_user.id):
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
    # 이미 정원 이상이면 토스트 없이 무시
    if checked >= max_participants:
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

    lines: list[str] = []
    for idx, r in enumerate(records, start=1):
        uid = int(r["user_id"])
        dn = (display_names.get(uid) or "").strip()
        fallback = str(r.get("user_name") or "이름없음")
        name = dn or fallback
        lines.append(f"{idx}. {name}")

    rate = _attendance_rate_percent(checked, max_participants)
    new_text = _attendance_status_text(lines, rate)

    # 정원 달성 시: 버튼 제거 + 축하 메시지 발송
    if checked >= max_participants:
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


async def attendance_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(관리자용) 현재 세션 출석 상태 확인."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        await update.message.reply_text("출석 상태는 단체방에서 확인해 주세요.")
        return
    user = update.effective_user
    if user and not await _is_allowed_user(context, user.id):
        await update.message.reply_text("참여 권한이 없어요.")
        return

    now = datetime.datetime.now(KST)
    # 최근 일요일(오늘이 일요일이면 오늘)
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


def _dm_add_hint(context: ContextTypes.DEFAULT_TYPE) -> str:
    """1:1에서 /add 하라는 안내 문구. (Markdown 포맷 없이 순수 텍스트로 반환)"""
    return (
        "루틴 입력은 봇과 1:1 대화에서 해 주세요.\n"
        "아래 링크에서 /add 를 입력한 후, 봇의 메시지에 답장으로 적어 주세요.\n"
        f"{_bot_tme_link()}"
    )


def _parse_date_input(date_input: str) -> str | None:
    """/add 뒤에 붙일 날짜 파싱 (YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD 지원)."""
    s = (date_input or "").strip()
    if not s:
        return None

    candidates = [s]
    # 구분자 통일 (예: 2026.03.17)
    candidates.append(s.replace(".", "-").replace("/", "-"))
    # YYYYMMDD
    candidates.append(s.replace("-", "").replace("/", ""))

    for c in candidates:
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                dt = datetime.datetime.strptime(c, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def _format_date_label(date_str: str) -> str:
    """YYYY-MM-DD -> MM/DD 형태. 파싱 불가면 원문 반환."""
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%m/%d")
    except Exception:
        return date_str


def _get_allowed_group_chat_id() -> int | None:
    raw = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _is_allowed_user(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """우리 단체방 멤버만 DM 루틴 입력 허용."""
    group_chat_id = _get_allowed_group_chat_id()
    if group_chat_id is None:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=group_chat_id, user_id=user_id)
        return getattr(member, "status", None) in ("creator", "administrator", "member")
    except Exception:
        logger.exception("Failed to check chat member for allowlist")
        return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """알림에 답장하면 루틴 저장. 단체방에서는 저장하지 않고 1:1 유도. 개인채팅에서만 어제 루틴 선택·저장."""
    msg = update.message
    if not msg or not msg.reply_to_message:
        return

    reply_to_id = msg.reply_to_message.message_id
    user = msg.from_user
    name = user.full_name or user.username or str(user.id)
    chat = update.effective_chat
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    yesterday = (datetime.datetime.now(KST) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # 단체방에서는 루틴 저장/선택 없이 1:1 유도만 (포맷팅 없이 일반 텍스트 전송)
    if chat and chat.type in ("group", "supergroup"):
        sel = await db.get_selection_prompt(reply_to_id)
        prompt_type = await db.get_prompt_type(reply_to_id)
        if sel or prompt_type:
            await msg.reply_text(_dm_add_hint(context))
            return
    elif chat and chat.type == "private":
        if not await _is_allowed_user(context, user.id):
            await msg.reply_text("이 봇은 SanS 1조 단체방 멤버만 루틴 입력이 가능해요.")
            return

    # 1) 어제 루틴 선택용 메시지에 대한 답장인지 확인
    sel = await db.get_selection_prompt(reply_to_id)
    if sel:
        save_date = sel.get("selection_date") or today_str
        date_label = _format_date_label(save_date)
        items = json.loads(sel["items_json"])
        to_save = _parse_selection_reply(msg.text or "", items)
        for content in to_save:
            await db.save_routine(
                user_id=user.id,
                user_name=name,
                date=save_date,
                routine_type=sel["prompt_type"],
                content=content,
            )
        await db.delete_selection_prompt(reply_to_id)
        count = len(to_save)
        await msg.reply_text(
            f"✅ *{name}*님의 {date_label} 루틴 {count}개 기록했어요!",
            parse_mode="Markdown",
        )
        logger.info(f"Routine saved from selection | user={name}, count={count}")
        return

    # 2) 알람/시작 프롬프트에 대한 답장인지 확인
    prompt_type = await db.get_prompt_type(reply_to_id)
    if not prompt_type:
        return  # 봇 알람에 대한 답장이 아님

    prompt_date = await db.get_prompt_date(reply_to_id)
    save_date = prompt_date or today_str
    try:
        save_date_obj = datetime.datetime.strptime(save_date, "%Y-%m-%d").date()
    except Exception:
        # 과거에 저장된 prompt.date가 MM/DD 형태였을 가능성 대비
        save_date_obj = datetime.datetime.now(KST).date()
        save_date = today_str
    yesterday_for_items = (save_date_obj - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    date_label = _format_date_label(save_date)

    # 3) 어제 루틴이 있으면 번호 선택 메시지 전송 후 대기
    yesterday_routines = await db.get_user_routines(user.id, yesterday_for_items)
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
            f"📋 *어제 루틴에서 선택*\n\n"
            f"{list_text}\n\n"
            f"기존 건 번호를 *쉼표(,)*로 구분해서 쓰고, 새로 추가할 게 있으면 쉼표 뒤에 적어주세요.\n"
            f"예: 1,3,요가 10분\n\n"
            f"👇 *이 메시지에 답장*으로 보내주세요.",
            parse_mode="Markdown",
        )
        await db.save_selection_prompt(
            message_id=sent.message_id,
            user_id=user.id,
            chat_id=msg.chat_id,
            selection_date=save_date,
            items_json=json.dumps(items, ensure_ascii=False),
            prompt_type=prompt_type,
        )
        logger.info(f"Selection prompt sent | user={name}, items={len(items)}")
        return

    # 4) 어제 루틴 없음 → 기존처럼 한 번에 저장
    await db.save_routine(
        user_id=user.id,
        user_name=name,
        date=save_date,
        routine_type=prompt_type,
        content=msg.text or "",
    )
    await msg.reply_text(
        f"✅ *{name}*님의 {date_label} 루틴이 기록됐어요!",
        parse_mode="Markdown",
    )
    logger.info(f"Routine saved | user={name}, type={prompt_type}")


# ─────────────────────────────────────────
# 커맨드 핸들러
# ─────────────────────────────────────────

HELP_TEXT = """
📖 *루틴 기록 사용법 안내*

*▶ 루틴 입력*
• 루틴은 *봇과 1:1 대화*에서만 입력해 주세요.
• *봇과 1:1* 채팅을 연 뒤 `/add` 또는 `/add YYYY-MM-DD` 를 입력하세요.
• 어제 루틴이 있으면 번호 목록이 나옵니다. 기존 건은 *번호를 쉼표(,)*로 구분, 새로 넣을 건 *쉼표 뒤*에 적고, 그 메시지에 *답장*으로 보내면 됩니다. (예: 1,3,요가 10분)
• 단체방에서는 아침 8시·저녁 9시 알림, 12시 점심 리마인드가 올라옵니다.

*▶ 명령어*
/add [YYYY-MM-DD] — 루틴 추가 (1:1에서, 날짜 지정 가능)
/today — 오늘 내가 입력한 루틴 보기
/myroutine — 내가 자주 쓰는 루틴 TOP 5
/delete — 오늘 입력한 루틴 전부 삭제
/search YYYY-MM-DD — 해당 날짜 내 루틴 조회
/list [YYYY-MM-DD] — 해당 날짜 전체 루틴 목록 (이름별, 요약 없음)
/setname 이름 — /list 등에 표시될 내 이름 설정 (1:1에서)
/summary — 오늘 전체 루틴 AI 요약
/weekstats — 지난 7일 통계
/monthstats — 지난 30일 통계
/chatid — 이 채팅방 ID 확인 (설정용)
/help — 이 사용법 다시 보기
""".strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 루틴 봇에 오신 걸 환영합니다!\n\n"
        "📌 사용법\n"
        "• 매일 아침 8시·저녁 9시 알림이 단체방에 올라와요.\n"
        f"• 루틴 입력: 아래 링크 클릭해서 각자 입력해 주세요.\n{_bot_tme_link()}\n\n"
        "자세한 사용법은 루틴 기록 사용법 안내: /help 를 입력하세요. 😊",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """사용법 매뉴얼 안내"""
    await update.message.reply_text(HELP_TEXT)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """오늘 루틴 추가: 단체방이면 1:1 유도, 개인채팅에서만 어제 루틴 선택(본인 것만) 또는 입력 프롬프트"""
    chat = update.effective_chat
    user = update.message.from_user
    if not chat or not user:
        return

    # 단체방에서는 루틴 입력을 하지 않고 1:1 대화로 유도 (포맷팅 없이 일반 텍스트 전송)
    if chat.type in ("group", "supergroup"):
        await update.message.reply_text(
            _dm_add_hint(context) + "\n\n입력·저장이 끝나면 개인 대화창에서 안내해 드려요."
        )
        return
    if chat.type == "private":
        if not await _is_allowed_user(context, user.id):
            await update.message.reply_text("이 봇은 SanS 1조 단체방 멤버만 루틴 입력이 가능해요.")
            return

    target_date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    if context.args:
        target_date_candidate = (context.args[0] or "").strip()
        parsed = _parse_date_input(target_date_candidate)
        if not parsed:
            await update.message.reply_text("사용법: /add [YYYY-MM-DD] (예: /add 2026-03-17)")
            return
        target_date_str = parsed
    target_label = _format_date_label(target_date_str)

    target_date_obj = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    yesterday = (target_date_obj - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    # 개인채팅: 해당 유저의 어제 루틴만 표시 (남의 루틴 아님)
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
        msg = await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"📝 *{target_label} 루틴 추가*\n\n"
                f"어제 루틴:\n{list_text}\n\n"
                f"기존 건 번호를 *쉼표(,)*로 구분해서 쓰고, 새로 추가할 게 있으면 쉼표 뒤에 적어주세요.\n"
                f"예: 1,3,요가 10분\n\n"
                f"👇 *이 메시지에 답장*으로 보내주세요."
            ),
            parse_mode="Markdown",
        )
        await db.save_selection_prompt(
            message_id=msg.message_id,
            user_id=user.id,
            chat_id=chat.id,
            selection_date=target_date_str,
            items_json=json.dumps(items, ensure_ascii=False),
            prompt_type="morning",
        )
        logger.info(f"Add: selection prompt sent | user={user.id}, items={len(items)}")
    else:
        msg = await context.bot.send_message(
            chat_id=chat.id,
            text=(
                f"📝 *{target_label} 루틴을 추가해주세요!*\n\n"
                f"*{target_label}* 추가로 실천하고 싶은/실천한 루틴을 적어주세요.\n\n"
                f"👇 *이 메시지에 답장*으로 적어주시면 기록됩니다."
            ),
            parse_mode="Markdown",
        )
        await db.save_prompt_message(msg.message_id, "morning", target_date_str)


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


async def setname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """개인 대화에서 표시 이름 설정 (/list 등에 적용)"""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user:
        return
    if chat.type in ("group", "supergroup"):
        await update.message.reply_text("이름 설정은 봇과 1:1 대화에서만 가능해요.")
        return
    if not await _is_allowed_user(context, user.id):
        await update.message.reply_text("이 봇은 SanS 1조 단체방 멤버만 설정/입력이 가능해요.")
        return

    display_name = " ".join((context.args or []))
    display_name = (display_name or "").strip()
    if not display_name:
        await update.message.reply_text("사용법: /setname 표시이름 (예: /setname 홍길동)")
        return
    if len(display_name) > 40:
        await update.message.reply_text("표시 이름은 40자 이내로 입력해 주세요.")
        return

    await db.set_user_display_name(user.id, display_name)
    await update.message.reply_text(f"✅ 표시 이름이 `{display_name}` 로 설정됐어요.", parse_mode="Markdown")


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """해당 날짜(기본: 오늘) 기록된 모든 사람의 루틴을 이름별로 나열 (요약 없음)"""
    target_date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    if context.args:
        parsed = _parse_date_input((context.args[0] or "").strip())
        if not parsed:
            await update.message.reply_text("사용법: /list [YYYY-MM-DD] (예: /list 2026-03-15)")
            return
        target_date_str = parsed

    routines = await db.get_today_routines(target_date_str)
    if not routines:
        await update.message.reply_text(f"📭 {_format_date_label(target_date_str)} 기록된 루틴이 없어요.")
        return
    by_user: dict[int, dict[str, object]] = {}
    for r in routines:
        uid = int(r.get("user_id") or 0)
        name = (r.get("user_name") or "").strip() or "이름없음"
        content = (r.get("content") or "").strip()
        if not content:
            continue
        if uid not in by_user:
            by_user[uid] = {"fallback_name": name, "contents": []}
        (by_user[uid]["contents"]).append(content)  # type: ignore[union-attr]

    display_names = await db.get_user_display_names(list(by_user.keys()))

    date_label = _format_date_label(target_date_str)
    header = f"📋 {date_label} 루틴 기록 (참여인원 {len(by_user)}명)"

    lines: list[str] = []
    items = []
    for uid, data in by_user.items():
        dn = (display_names.get(uid) or "").strip()
        fallback = str(data.get("fallback_name") or "이름없음")
        contents = list(data.get("contents") or [])
        items.append((dn or fallback, contents))

    for i, (name, contents) in enumerate(sorted(items, key=lambda x: x[0]), start=1):
        lines.append(f"• [{name}] {', '.join(contents)}")

    text = header + "\n\n" + "\n".join(lines)
    await update.message.reply_text(text)


async def send_daily_routine_list_followup(context: ContextTypes.DEFAULT_TYPE):
    """밤 11시: 오늘까지 기록된 /list 메시지를 자동으로 단체방에 전송."""
    chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id_raw:
        return
    chat_id = int(chat_id_raw)

    target_date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    routines = await db.get_today_routines(target_date_str)

    if not routines:
        base = f"📭 {_format_date_label(target_date_str)} 기록된 루틴이 없어요."
    else:
        by_user: dict[int, dict[str, object]] = {}
        for r in routines:
            uid = int(r.get("user_id") or 0)
            name = (r.get("user_name") or "").strip() or "이름없음"
            content = (r.get("content") or "").strip()
            if not content:
                continue
            if uid not in by_user:
                by_user[uid] = {"fallback_name": name, "contents": []}
            (by_user[uid]["contents"]).append(content)  # type: ignore[union-attr]

        display_names = await db.get_user_display_names(list(by_user.keys()))
        date_label = _format_date_label(target_date_str)
        header = f"📋 {date_label} 루틴 기록 (참여인원 {len(by_user)}명)"

        items = []
        for uid, data in by_user.items():
            dn = (display_names.get(uid) or "").strip()
            fallback = str(data.get("fallback_name") or "이름없음")
            contents = list(data.get("contents") or [])
            items.append((dn or fallback, contents))

        lines: list[str] = []
        for _, (name, contents) in enumerate(sorted(items, key=lambda x: x[0]), start=1):
            lines.append(f"• [{name}] {', '.join(contents)}")

        base = header + "\n\n" + "\n".join(lines)

    praise = "루틴 열심히 하신 것 정말 대단해요. 오늘도 수고하셨습니다. 굿나잇! 🌙"
    await context.bot.send_message(chat_id=chat_id, text=base + "\n\n" + praise)
    logger.info("Routine list followup sent | date=%s", target_date_str)


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


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """오늘 내가 입력한 루틴 내용 보여주기"""
    user = update.message.from_user
    today_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
    routines = await db.get_user_routines(user.id, today_str)

    if not routines:
        await update.message.reply_text("📭 오늘 기록된 루틴이 아직 없어요.")
        return

    contents = [(r.get("content") or "").strip() for r in routines if (r.get("content") or "").strip()]
    today_label = datetime.datetime.now(KST).strftime("%m/%d")
    text = f"📋 오늘({today_label}) 내 루틴\n\n{', '.join(contents)}"
    await update.message.reply_text(text)


async def my_routine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """내가 그동안 자주 사용하는 루틴 목록 TOP 5"""
    user = update.message.from_user
    top = await db.get_user_top_routines(user.id, limit=5)

    if not top:
        await update.message.reply_text("📭 아직 기록된 루틴이 없어요. 루틴을 입력하면 자주 쓰는 항목이 여기 나타나요.")
        return

    text = "📌 *자주 사용하는 루틴 TOP 5*\n\n"
    for i, row in enumerate(top, 1):
        text += f"{i}. {row['content']} ({row['count']}회)\n"

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
    contents = [(r.get("content") or "").strip() for r in routines if (r.get("content") or "").strip()]
    date_label = f"{date_str[5:7]}/{date_str[8:]}"
    text = f"📋 {user.full_name}님의 {date_label} 루틴\n\n{', '.join(contents)}"
    await update.message.reply_text(text)


# ─────────────────────────────────────────
# 앱 초기화 & 실행
# ─────────────────────────────────────────

async def post_init(application: Application):
    await db.init()
    logger.info("Database initialized")


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    # 요청 타임아웃 명시 (배포 시 종료 단계에서 getUpdates Conflict 로그가 나올 수 있음 — 새 인스턴스가 이미 폴링 중이면 정상)
    app = (
        Application.builder()
        .token(token)
        .connect_timeout(10.0)
        .read_timeout(10.0)
        .write_timeout(10.0)
        .post_init(post_init)
        .build()
    )

    # 커맨드 등록
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("chatid", chatid_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("setname", setname_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("weekstats", week_stats_command))
    app.add_handler(CommandHandler("monthstats", month_stats_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("myroutine", my_routine_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 스케줄 등록 (KST 기준)
    morning_time = datetime.time(hour=8, minute=0, tzinfo=KST)
    evening_time = datetime.time(hour=20, minute=0, tzinfo=KST)
    lunch_time = datetime.time(hour=12, minute=0, tzinfo=KST)
    routine_list_time = datetime.time(hour=23, minute=30, tzinfo=KST)

    # evening 알람은 "내일부터" 시작하도록 첫 실행일 제어
    global EVENING_ALARM_START_DATE
    EVENING_ALARM_START_DATE = (datetime.datetime.now(KST).date() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    app.job_queue.run_daily(send_morning_alarm, time=morning_time)
    app.job_queue.run_daily(send_evening_alarm, time=evening_time)
    app.job_queue.run_daily(send_lunch_reminder, time=lunch_time)
    app.job_queue.run_daily(send_daily_routine_list_followup, time=routine_list_time)
    attendance.register_attendance(app, db, _is_allowed_user)

    logger.info("Bot started. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
