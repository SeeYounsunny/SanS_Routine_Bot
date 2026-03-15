import os
import aiosqlite
import asyncpg

DB_PATH = os.environ.get("DB_PATH", "routines.db")
DATABASE_URL = os.environ.get("DATABASE_URL")


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
            await conn.commit()

    # ── 알람 메시지 저장/조회 ──────────────────────────────

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

    # ── 루틴 저장/조회 ─────────────────────────────────────

    async def save_routine(
        self,
        user_id: int,
        user_name: str,
        date: str,
        routine_type: str,
        content: str,
    ):
        # 동일한 내용이 같은 날, 같은 타입으로 이미 기록되어 있으면 중복 저장하지 않음
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                exists = await conn.fetchrow(
                    """
                    SELECT 1 FROM routines
                    WHERE user_id = $1 AND date = $2 AND routine_type = $3 AND content = $4
                    LIMIT 1
                    """,
                    user_id,
                    date,
                    routine_type,
                    content,
                )
                if exists:
                    return

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
            async with conn.execute(
                """
                SELECT 1 FROM routines
                WHERE user_id = ? AND date = ? AND routine_type = ? AND content = ?
                LIMIT 1
                """,
                (user_id, date, routine_type, content),
            ) as cur:
                exists = await cur.fetchone()

            if exists:
                return

            await conn.execute(
                "INSERT INTO routines (user_id, user_name, date, routine_type, content) VALUES (?, ?, ?, ?, ?)",
                (user_id, user_name, date, routine_type, content),
            )
            await conn.commit()

    async def get_today_routines(self, date: str) -> list[dict]:
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

    async def delete_user_routines_for_date(self, user_id: int, date: str) -> int:
        """해당 유저의 해당 날짜 루틴 전부 삭제. 삭제된 행 수 반환."""
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

    # ── 집계용 통계 쿼리 ─────────────────────────────────────

    async def get_top_users(self, start_date: str, end_date: str, limit: int) -> list[dict]:
        """기간(start_date~end_date) 동안 루틴을 가장 많이 기록한 사람 순위"""
        if self.use_postgres:
            conn = await asyncpg.connect(DATABASE_URL)
            try:
                rows = await conn.fetch(
                    """
                    SELECT user_id, user_name, COUNT(*) AS count
                    FROM routines
                    WHERE date BETWEEN $1 AND $2
                    GROUP BY user_id, user_name
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
                SELECT user_id, user_name, COUNT(*) AS count
                FROM routines
                WHERE date BETWEEN ? AND ?
                GROUP BY user_id, user_name
                ORDER BY count DESC
                LIMIT ?
                """,
                (start_date, end_date, limit),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_top_routines(self, start_date: str, end_date: str, limit: int) -> list[dict]:
        """기간 동안 가장 많이 기록된 루틴 내용 순위"""
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
