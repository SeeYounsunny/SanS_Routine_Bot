import os
import aiosqlite

DB_PATH = os.environ.get("DB_PATH", "routines.db")


class Database:
    async def init(self):
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS routines (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    user_name    TEXT    NOT NULL,
                    date         TEXT    NOT NULL,
                    routine_type TEXT    NOT NULL,
                    content      TEXT    NOT NULL,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_messages (
                    message_id   INTEGER PRIMARY KEY,
                    prompt_type  TEXT NOT NULL,
                    date         TEXT NOT NULL
                )
            """)
            await conn.commit()

    # ── 알람 메시지 저장/조회 ──────────────────────────────

    async def save_prompt_message(self, message_id: int, prompt_type: str, date: str):
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO prompt_messages (message_id, prompt_type, date) VALUES (?, ?, ?)",
                (message_id, prompt_type, date),
            )
            await conn.commit()

    async def get_prompt_type(self, message_id: int) -> str | None:
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
        async with aiosqlite.connect(DB_PATH) as conn:
            # 동일한 내용이 같은 날, 같은 타입으로 이미 기록되어 있으면 중복 저장하지 않음
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
        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM routines WHERE date = ? ORDER BY user_name, routine_type",
                (date,),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_user_routines(self, user_id: int, date: str) -> list[dict]:
        async with aiosqlite.connect(DB_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM routines WHERE user_id = ? AND date = ? ORDER BY routine_type",
                (user_id, date),
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
