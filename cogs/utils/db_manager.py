import sqlite3
import datetime
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger("discord_bot")


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = DATA_DIR / "user_chat_history.db"

# --- 初始化函式 ---
def init_db():
    """初始化所有資料庫表格"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # 聊天歷史紀錄表格
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, model_used TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id_timestamp ON chat_history (user_id, timestamp);")

        # 使用者個人化設定表格
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY, model TEXT, remember_context INTEGER, system_prompt TEXT
            )
        """)
        
        # 監聽頻道列表表格
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS listened_channels (
                channel_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL,
                added_by_id TEXT NOT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    logger.info("資料庫表格初始化或檢查完畢。")

# --- 使用者設定 (user_settings) ---
def get_user_settings(user_id: str, default_settings: dict) -> dict:
    """獲取指定使用者的設定，若無則返回預設值"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()

        if user_row:
            settings = dict(user_row)
            settings["remember_context"] = bool(settings["remember_context"])
            return settings
        else:
            return default_settings

def update_user_setting(user_id: str, key: str, value):
    """更新使用者的單一設定"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        if isinstance(value, bool):
            value = 1 if value else 0
        
        cursor.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
        cursor.execute(f"UPDATE user_settings SET {key} = ? WHERE user_id = ?", (value, user_id))

# --- 聊天歷史 (chat_history) ---
def add_message_to_db(user_id: str, role: str, content: str, model_used: Optional[str] = None):
    """新增一筆聊天紀錄"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_history (user_id, role, content, model_used, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, role, content, model_used, datetime.datetime.now()))

def get_user_history_from_db(user_id: str, system_prompt: str, limit: int = 11) -> list:
    """獲取使用者的對話歷史以傳送給API"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        messages = []
        
        cursor.execute("SELECT 1 FROM chat_history WHERE user_id = ? AND role = 'system'", (user_id,))
        system_prompt_exists = cursor.fetchone()

        if not system_prompt_exists:
            add_message_to_db(user_id, "system", system_prompt)

        messages.append({"role": "system", "content": system_prompt})

        num_to_fetch = max(0, limit - 1)
        if num_to_fetch > 0:
            cursor.execute("""
                SELECT role, content FROM chat_history
                WHERE user_id = ? AND role IN ('user', 'assistant')
                ORDER BY timestamp DESC LIMIT ?
            """, (user_id, num_to_fetch))
            for row in reversed(cursor.fetchall()):
                messages.append({"role": row["role"], "content": row["content"]})
        return messages

def clear_user_history_in_db(user_id: str):
    """清除使用者的對話歷史"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))

def get_raw_user_history_for_viewing(user_id: str, limit: int = 10) -> list:
    """獲取原始對話歷史以供檢視"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content, model_used, timestamp FROM chat_history
            WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?
        """, (user_id, limit))
        return cursor.fetchall()

# --- 監聽頻道 (listened_channels) ---
def load_listened_channels_to_cache() -> set:
    """從資料庫載入所有監聽頻道的ID到一個集合中"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT channel_id FROM listened_channels")
        ids = {int(row[0]) for row in cursor.fetchall()}
        logger.info(f"從資料庫載入 {len(ids)} 個監聽頻道至快取。")
        return ids

def add_listened_channel(channel_id: str, guild_id: str, user_id: str) -> bool:
    """新增一個監聽頻道，如果已存在則返回 False"""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO listened_channels (channel_id, guild_id, added_by_id, timestamp) VALUES (?, ?, ?, ?)",
                (channel_id, guild_id, user_id, datetime.datetime.now())
            )
        return True
    except sqlite3.IntegrityError:
        # PRIMARY KEY a-zsta-raint，表示頻道已存在
        return False

def remove_listened_channel(channel_id: str) -> bool:
    """移除一個監聽頻道，如果成功移除返回 True"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM listened_channels WHERE channel_id = ?", (channel_id,))
        return conn.total_changes > 0

def get_listened_channels_for_guild(guild_id: str) -> list:
    """獲取指定伺服器的所有監聽頻道"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT channel_id FROM listened_channels WHERE guild_id = ?", (guild_id,))
        return cursor.fetchall()