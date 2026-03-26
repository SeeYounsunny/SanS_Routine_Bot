import os
import aiosqlite
import asyncpg

DB_PATH = os.environ.get("DB_PATH", "routines.db")
DATABASE_URL = os.environ.get("DATABASE_URL")

# 실제 서비스 데이터 시작일(YYYY-MM-DD).
# - 루틴 통계/입력은 3/16부터 유효 (그 이전은 테스트로 취급)
# - 출석체크 통계는 3/23부터 유효 (그 이전은 테스트로 취급)
ROUTINE_DATA_MIN_DATE = os.environ.get("ROUTINE_DATA_MIN_DATE", "2026-03-16")
ATTENDANCE_DATA_MIN_DATE = os.environ.get("ATTENDANCE_DATA_MIN_DATE", "2026-03-23")


def _is_before(date_str: str, min_date: str) -> bool:
    return date_str < min_date


def _effective_range_clamped(start_date: str, end_date: str, min_date: str) -> tuple[str, str] | None:
    """집계/조회 구간을 min_date 이상으로 맞춤. 유효 구간이 없으면 None."""
    start = max(start_date, min_date)
    if start > end_date:
        return None
    return start, end_date


class Database:
    def __init__(self):
        # DATABASE_URL 이 있으면 PostgreSQL 사용, 없으면 로컬 SQLite 사용
        self.use_postgres = DATABASE_URL is not None

    async def init(self):
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS routines (
                        id           SERIAL PRIMARY KEY,
                        user_id      BIGINT    NOT NULL,
                        user_name    TEXT      NOT NULL,
                        date         TEXT      NOT NULL,
                        routine_type TEXT      NOT NULL,
                        content      TEXT      NOT NULL,
                        created_at   TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS prompt_messages (
                        message_id   BIGINT PRIMARY KEY,
                        prompt_type  TEXT NOT NULL,
                        date         TEXT NOT NULL
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS routine_selection_prompts (
                        message_id       BIGINT PRIMARY KEY,
                        user_id          BIGINT NOT NULL,
                        chat_id          BIGINT NOT NULL,
                        selection_date   TEXT NOT NULL,
                        items_json       TEXT NOT NULL,
                        prompt_type      TEXT NOT NULL
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        user_id      BIGINT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        updated_at   TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS attendance_sessions (
                        session_date TEXT PRIMARY KEY,
                        max_participants INTEGER NOT NULL,
                        status_message_chat_id BIGINT,
                        status_message_id BIGINT,
                        started_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS attendance_records (
                        session_date TEXT NOT NULL,
                        user_id BIGINT NOT NULL,
                        user_name TEXT NOT NULL,
                        checked_at TIMESTAMPTZ DEFAULT NOW(),
                        PRIMARY KEY (session_date, user_id)
                    )
                    """
                )
            finally:
                await conn.close()
            return

        # SQLite (로컬/테스트용)
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS routines (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    user_name    TEXT    NOT NULL,
                    date         TEXT    NOT NULL,
                    routine_type TEXT    NOT NULL,
                    content      TEXT    NOT NULL,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_messages (
                    message_id   INTEGER PRIMARY KEY,
                    prompt_type  TEXT NOT NULL,
                    date         TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS routine_selection_prompts (
                    message_id       INTEGER PRIMARY KEY,
                    user_id          INTEGER NOT NULL,
                    chat_id          INTEGER NOT NULL,
                    selection_date   TEXT NOT NULL,
                    items_json       TEXT NOT NULL,
                    prompt_type      TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id      INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance_sessions (
                    session_date TEXT PRIMARY KEY,
                    max_participants INTEGER NOT NULL,
                    status_message_chat_id INTEGER,
                    status_message_id INTEGER,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance_records (
                    session_date TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (session_date, user_id)
                )
                """
            )
            await conn.commit()

    # ── 알람 메시지 저장/조회 ──────────────────────────────

    async def set_user_display_name(self, user_id: int, display_name: str) -> None:
        display_name = (display_name or "").strip()
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, display_name, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id)
                    DO UPDATE SET display_name = EXCLUDED.display_name, updated_at = NOW()
                    """,
                    user_id,
                    display_name,
                )
            finally:
                await conn.close()
            return

        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO user_profiles (user_id, display_name, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (user_id, display_name),
            )
            await conn.commit()

    @staticmethod
    def resolve_visible_name(user_id: int, display_names: dict[int, str], telegram_fallback: str) -> str:
        """표시 이름: /setname 값 우선, 없으면 텔레그램 기반 fallback."""
        dn = (display_names.get(user_id) or "").strip()
        fb = (telegram_fallback or "").strip()
        return dn or fb or "이름없음"

    async def get_user_display_names(self, user_ids: list[int]) -> dict[int, str]:
        ids = [int(x) for x in user_ids if x is not None]
        if not ids:
            return {}

        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT user_id, display_name
                    FROM user_profiles
                    WHERE user_id = ANY($1::bigint[])
                    """,
                    ids,
                )
                return {int(r["user_id"]): (r["display_name"] or "") for r in rows}
            finally:
                await conn.close()

        placeholders = ",".join(["?"] * len(ids))
        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT user_id, display_name FROM user_profiles WHERE user_id IN ({placeholders})",
                ids,
            ) as cur:
                rows = await cur.fetchall()
                return {int(r["user_id"]): (r["display_name"] or "") for r in rows}

    # ── 출석체크 ──────────────────────────────────────────────

    async def attendance_get_session(self, session_date: str) -> dict | None:
        if _is_before(session_date, ATTENDANCE_DATA_MIN_DATE):
            return None
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    """
                    SELECT session_date, max_participants, status_message_chat_id, status_message_id, started_at
                    FROM attendance_sessions
                    WHERE session_date = $1
                    """,
                    session_date,
                )
                return dict(row) if row else None
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT session_date, max_participants, status_message_chat_id, status_message_id, started_at
                FROM attendance_sessions
                WHERE session_date = ?
                """,
                (session_date,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def attendance_create_session(self, session_date: str, max_participants: int) -> bool:
        """세션 생성. 이미 있으면 False 반환."""
        if _is_before(session_date, ATTENDANCE_DATA_MIN_DATE):
            return False
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                res = await conn.execute(
                    """
                    INSERT INTO attendance_sessions (session_date, max_participants)
                    VALUES ($1, $2)
                    ON CONFLICT (session_date) DO NOTHING
                    """,
                    session_date,
                    max_participants,
                )
                # asyncpg execute returns like "INSERT 0 1"
                return res.endswith("1")
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute(
                """
                INSERT OR IGNORE INTO attendance_sessions (session_date, max_participants)
                VALUES (?, ?)
                """,
                (session_date, max_participants),
            )
            await conn.commit()
            return cur.rowcount == 1

    async def attendance_set_status_message(
        self,
        session_date: str,
        chat_id: int,
        message_id: int,
    ) -> None:
        if _is_before(session_date, ATTENDANCE_DATA_MIN_DATE):
            return
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute(
                    """
                    UPDATE attendance_sessions
                    SET status_message_chat_id = $1, status_message_id = $2
                    WHERE session_date = $3
                    """,
                    chat_id,
                    message_id,
                    session_date,
                )
            finally:
                await conn.close()
            return

        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                """
                UPDATE attendance_sessions
                SET status_message_chat_id = ?, status_message_id = ?
                WHERE session_date = ?
                """,
                (chat_id, message_id, session_date),
            )
            await conn.commit()

    async def attendance_get_count(self, session_date: str) -> int:
        if _is_before(session_date, ATTENDANCE_DATA_MIN_DATE):
            return 0
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM attendance_records WHERE session_date = $1",
                    session_date,
                )
                return int(row["cnt"]) if row else 0
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            async with conn.execute(
                "SELECT COUNT(*) AS cnt FROM attendance_records WHERE session_date = ?",
                (session_date,),
            ) as cur:
                row = await cur.fetchone()
                return int(row[0]) if row else 0

    async def attendance_get_records(self, session_date: str) -> list[dict]:
        if _is_before(session_date, ATTENDANCE_DATA_MIN_DATE):
            return []
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT user_id, user_name, checked_at
                    FROM attendance_records
                    WHERE session_date = $1
                    ORDER BY checked_at ASC
                    """,
                    session_date,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT user_id, user_name, checked_at
                FROM attendance_records
                WHERE session_date = ?
                ORDER BY checked_at ASC
                """,
                (session_date,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def attendance_add_record(self, session_date: str, user_id: int, user_name: str) -> bool:
        """레코드 추가. 중복이면 False."""
        if _is_before(session_date, ATTENDANCE_DATA_MIN_DATE):
            return False
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                res = await conn.execute(
                    """
                    INSERT INTO attendance_records (session_date, user_id, user_name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (session_date, user_id) DO NOTHING
                    """,
                    session_date,
                    user_id,
                    user_name,
                )
                return res.endswith("1")
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute(
                """
                INSERT OR IGNORE INTO attendance_records (session_date, user_id, user_name)
                VALUES (?, ?, ?)
                """,
                (session_date, user_id, user_name),
            )
            await conn.commit()
            return cur.rowcount == 1

    async def save_prompt_message(self, message_id: int, prompt_type: str, date: str):
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute(
                    """
                    INSERT INTO prompt_messages (message_id, prompt_type, date)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (message_id)
                    DO UPDATE SET prompt_type = EXCLUDED.prompt_type, date = EXCLUDED.date
                    """,
                    message_id,
                    prompt_type,
                    date,
                )
            finally:
                await conn.close()
            return

        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO prompt_messages (message_id, prompt_type, date) VALUES (?, ?, ?)",
                (message_id, prompt_type, date),
            )
            await conn.commit()

    async def get_prompt_type(self, message_id: int) -> str | None:
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT prompt_type FROM prompt_messages WHERE message_id = $1",
                    message_id,
                )
                return row["prompt_type"] if row else None
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            async with conn.execute(
                "SELECT prompt_type FROM prompt_messages WHERE message_id = ?",
                (message_id,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def get_prompt_date(self, message_id: int) -> str | None:
        """prompt_messages.date 값을 가져옴.

        원래 코드에서는 get_prompt_type만 사용했기 때문에 date 포맷이 일관되지 않을 수 있어,
        봇 코드에서 파싱 실패 시 기본값으로(today) 폴백하도록 처리합니다.
        """
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    "SELECT date FROM prompt_messages WHERE message_id = $1",
                    message_id,
                )
                return row["date"] if row else None
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            async with conn.execute(
                "SELECT date FROM prompt_messages WHERE message_id = ?",
                (message_id,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    # ── 어제 루틴 선택용 프롬프트 (번호로 선택) ─────────────────

    async def save_selection_prompt(
        self,
        message_id: int,
        user_id: int,
        chat_id: int,
        selection_date: str,
        items_json: str,
        prompt_type: str,
    ):
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute(
                    """
                    INSERT INTO routine_selection_prompts
                    (message_id, user_id, chat_id, selection_date, items_json, prompt_type)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (message_id)
                    DO UPDATE SET user_id = EXCLUDED.user_id, chat_id = EXCLUDED.chat_id,
                                  selection_date = EXCLUDED.selection_date, items_json = EXCLUDED.items_json,
                                  prompt_type = EXCLUDED.prompt_type
                    """,
                    message_id,
                    user_id,
                    chat_id,
                    selection_date,
                    items_json,
                    prompt_type,
                )
            finally:
                await conn.close()
            return

        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO routine_selection_prompts
                (message_id, user_id, chat_id, selection_date, items_json, prompt_type)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, user_id, chat_id, selection_date, items_json, prompt_type),
            )
            await conn.commit()

    async def get_selection_prompt(self, message_id: int) -> dict | None:
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                row = await conn.fetchrow(
                    """
                    SELECT user_id, chat_id, selection_date, items_json, prompt_type
                    FROM routine_selection_prompts WHERE message_id = $1
                    """,
                    message_id,
                )
                return dict(row) if row else None
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT user_id, chat_id, selection_date, items_json, prompt_type
                FROM routine_selection_prompts WHERE message_id = ?
                """,
                (message_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def delete_selection_prompt(self, message_id: int):
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute(
                    "DELETE FROM routine_selection_prompts WHERE message_id = $1",
                    message_id,
                )
            finally:
                await conn.close()
            return

        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                "DELETE FROM routine_selection_prompts WHERE message_id = ?",
                (message_id,),
            )
            await conn.commit()

    # ── 루틴 저장/조회 ─────────────────────────────────────

    async def save_routine(
        self,
        user_id: int,
        user_name: str,
        date: str,
        routine_type: str,
        content: str,
    ):
        if _is_before(date, ROUTINE_DATA_MIN_DATE):
            return
        # 저장용: 앞뒤·연속 공백 정리. 비교용: 공백 전부 제거한 키 (기상 스트레칭 = 기상스트레칭)
        content = " ".join((content or "").split())
        content_key = "".join((content or "").split()).lower()

        # 오늘 해당 유저가 적은 모든 내용을 가져와서, 공백 제거 키가 하나라도 같으면 중복으로 저장 안 함
        existing = await self.get_user_routines(user_id, date)
        for row in existing:
            existing_key = "".join((row.get("content") or "").split()).lower()
            if existing_key == content_key:
                return

        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute(
                    """
                    INSERT INTO routines (user_id, user_name, date, routine_type, content)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    user_id,
                    user_name,
                    date,
                    routine_type,
                    content,
                )
            finally:
                await conn.close()
            return

        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                "INSERT INTO routines (user_id, user_name, date, routine_type, content) VALUES (?, ?, ?, ?, ?)",
                (user_id, user_name, date, routine_type, content),
            )
            await conn.commit()

    async def get_today_routines(self, date: str) -> list[dict]:
        if _is_before(date, ROUTINE_DATA_MIN_DATE):
            return []
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT * FROM routines
                    WHERE date = $1
                    ORDER BY user_name, routine_type
                    """,
                    date,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM routines WHERE date = ? ORDER BY user_name, routine_type",
                (date,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_user_routines(self, user_id: int, date: str) -> list[dict]:
        if _is_before(date, ROUTINE_DATA_MIN_DATE):
            return []
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT * FROM routines
                    WHERE user_id = $1 AND date = $2
                    ORDER BY routine_type
                    """,
                    user_id,
                    date,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM routines WHERE user_id = ? AND date = ? ORDER BY routine_type",
                (user_id, date),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_user_all_contents(self, user_id: int) -> list[str]:
        """해당 유저가 기록한 모든 루틴의 content 목록 (통계용)."""
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT content FROM routines
                    WHERE user_id = $1 AND date >= $2
                    """,
                    user_id,
                    ROUTINE_DATA_MIN_DATE,
                )
                return [r["content"] or "" for r in rows]
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            async with conn.execute(
                "SELECT content FROM routines WHERE user_id = ? AND date >= ?",
                (user_id, ROUTINE_DATA_MIN_DATE),
            ) as cur:
                rows = await cur.fetchall()
                return [r[0] or "" for r in rows]

    async def get_user_top_routines(self, user_id: int, limit: int = 5) -> list[dict]:
        """해당 유저가 자주 사용한 루틴 TOP N (공백 제거·소문자 통일 후 집계). 반환: [{"content": str, "count": int}, ...]"""
        contents = await self.get_user_all_contents(user_id)
        key_to_content = {}
        key_count = {}
        for c in contents:
            c = (c or "").strip()
            key = "".join(c.split()).lower()
            if not key:
                continue
            key_count[key] = key_count.get(key, 0) + 1
            if key not in key_to_content:
                key_to_content[key] = c
        sorted_keys = sorted(key_to_content.keys(), key=lambda k: -key_count[k])[:limit]
        return [{"content": key_to_content[k], "count": key_count[k]} for k in sorted_keys]

    async def get_user_top_routines_in_range(
        self, user_id: int, start_date: str, end_date: str, limit: int = 5
    ) -> list[dict]:
        """기간 [start_date, end_date] 내 루틴 content 빈도 집계 후 TOP limit (전체 기간 버전과 동일 규칙)."""
        rng = _effective_range_clamped(start_date, end_date, ROUTINE_DATA_MIN_DATE)
        if rng is None:
            return []
        start_date, end_date = rng

        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT content FROM routines
                    WHERE user_id = $1 AND date >= $2 AND date <= $3
                    """,
                    user_id,
                    start_date,
                    end_date,
                )
                contents = [r["content"] or "" for r in rows]
            finally:
                await conn.close()
        else:
            async with aiosqlite.connect(DB_PATH) as conn:
                async with conn.execute(
                    """
                    SELECT content FROM routines
                    WHERE user_id = ? AND date >= ? AND date <= ?
                    """,
                    (user_id, start_date, end_date),
                ) as cur:
                    rows = await cur.fetchall()
                    contents = [r[0] or "" for r in rows]

        key_to_content: dict[str, str] = {}
        key_count: dict[str, int] = {}
        for c in contents:
            c = (c or "").strip()
            key = "".join(c.split()).lower()
            if not key:
                continue
            key_count[key] = key_count.get(key, 0) + 1
            if key not in key_to_content:
                key_to_content[key] = c
        sorted_keys = sorted(key_to_content.keys(), key=lambda k: -key_count[k])[:limit]
        return [{"content": key_to_content[k], "count": key_count[k]} for k in sorted_keys]

    async def delete_user_routines_for_date(self, user_id: int, date: str) -> int:
        """해당 유저의 해당 날짜 루틴 전부 삭제. 삭제된 행 수 반환."""
        if _is_before(date, ROUTINE_DATA_MIN_DATE):
            return 0
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                result = await conn.execute(
                    "DELETE FROM routines WHERE user_id = $1 AND date = $2",
                    user_id,
                    date,
                )
                return int(result.split()[-1]) if result else 0
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute(
                "DELETE FROM routines WHERE user_id = ? AND date = ?",
                (user_id, date),
            )
            await conn.commit()
            return cur.rowcount

    async def delete_all_data(self) -> None:
        """루틴·프롬프트 메시지 전체 삭제 (초기화)"""
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                await conn.execute("DELETE FROM routines")
                await conn.execute("DELETE FROM prompt_messages")
            finally:
                await conn.close()
            return

        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("DELETE FROM routines")
            await conn.execute("DELETE FROM prompt_messages")
            await conn.commit()

    # ── 집계용 통계 쿼리 ─────────────────────────────────────

    async def get_top_users(self, start_date: str, end_date: str, limit: int) -> list[dict]:
        """기간(start_date~end_date) 동안 루틴을 가장 많이 기록한 사람 순위 (user_id 기준 집계)."""
        rng = _effective_range_clamped(start_date, end_date, ROUTINE_DATA_MIN_DATE)
        if rng is None:
            return []
        start_date, end_date = rng
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT
                        r.user_id,
                        (
                            SELECT r2.user_name
                            FROM routines r2
                            WHERE r2.user_id = r.user_id
                              AND r2.date BETWEEN $1 AND $2
                            ORDER BY r2.date DESC, r2.id DESC
                            LIMIT 1
                        ) AS user_name,
                        COUNT(*)::bigint AS count
                    FROM routines r
                    WHERE r.date BETWEEN $1 AND $2
                    GROUP BY r.user_id
                    ORDER BY count DESC
                    LIMIT $3
                    """,
                    start_date,
                    end_date,
                    limit,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT
                    r.user_id,
                    (
                        SELECT r2.user_name
                        FROM routines r2
                        WHERE r2.user_id = r.user_id
                          AND r2.date BETWEEN ? AND ?
                        ORDER BY r2.date DESC, r2.id DESC
                        LIMIT 1
                    ) AS user_name,
                    COUNT(*) AS count
                FROM routines r
                WHERE r.date BETWEEN ? AND ?
                GROUP BY r.user_id
                ORDER BY count DESC
                LIMIT ?
                """,
                (start_date, end_date, start_date, end_date, limit),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_top_routines(self, start_date: str, end_date: str, limit: int) -> list[dict]:
        """기간 동안 가장 많이 기록된 루틴 내용 순위"""
        rng = _effective_range_clamped(start_date, end_date, ROUTINE_DATA_MIN_DATE)
        if rng is None:
            return []
        start_date, end_date = rng
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT content, COUNT(*) AS count
                    FROM routines
                    WHERE date BETWEEN $1 AND $2
                    GROUP BY content
                    ORDER BY count DESC
                    LIMIT $3
                    """,
                    start_date,
                    end_date,
                    limit,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT content, COUNT(*) AS count
                FROM routines
                WHERE date BETWEEN ? AND ?
                GROUP BY content
                ORDER BY count DESC
                LIMIT ?
                """,
                (start_date, end_date, limit),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_top_attendance_users(
        self, start_date: str, end_date: str, limit: int
    ) -> list[dict]:
        """기간(start_date~end_date) 동안 출석(일요 세션) 횟수가 많은 사용자 순위."""
        rng = _effective_range_clamped(start_date, end_date, ATTENDANCE_DATA_MIN_DATE)
        if rng is None:
            return []
        start_date, end_date = rng
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT
                        ar.user_id,
                        (
                            SELECT ar2.user_name
                            FROM attendance_records ar2
                            WHERE ar2.user_id = ar.user_id
                              AND ar2.session_date BETWEEN $1 AND $2
                            ORDER BY ar2.session_date DESC
                            LIMIT 1
                        ) AS user_name,
                        COUNT(*)::bigint AS count
                    FROM attendance_records ar
                    WHERE ar.session_date BETWEEN $1 AND $2
                    GROUP BY ar.user_id
                    ORDER BY count DESC
                    LIMIT $3
                    """,
                    start_date,
                    end_date,
                    limit,
                )
                return [dict(r) for r in rows]
            finally:
                await conn.close()

        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT
                    ar.user_id,
                    (
                        SELECT ar2.user_name
                        FROM attendance_records ar2
                        WHERE ar2.user_id = ar.user_id
                          AND ar2.session_date BETWEEN ? AND ?
                        ORDER BY ar2.session_date DESC
                        LIMIT 1
                    ) AS user_name,
                    COUNT(*) AS count
                FROM attendance_records ar
                WHERE ar.session_date BETWEEN ? AND ?
                GROUP BY ar.user_id
                ORDER BY count DESC
                LIMIT ?
                """,
                (start_date, end_date, start_date, end_date, limit),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
