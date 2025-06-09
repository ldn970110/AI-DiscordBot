import discord
import os
from discord.ext import commands
from discord import app_commands
from openai import OpenAI
from typing import Optional, Literal
import sqlite3
import datetime

# --- 新增：定義預設設定值，方便管理 ---
DEFAULT_SETTINGS = {
    "model": "gpt-4-turbo",
    "remember_context": True,
    "system_prompt": "請你之後的回應一律使用繁體中文。"
}

class ChatGPTCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = bot.config
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.db_path = "/data/user_chat_history.db" 
        self.listen_channel_ids = [int(channel_id) for channel_id in self.config.get("listen_channel_ids", [])]
        self._init_db()

    # --- 修改：初始化資料庫，新增 user_settings 表格 ---
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # 聊天歷史紀錄表格
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model_used TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_id_timestamp ON chat_history (user_id, timestamp);")

        # 新增：使用者個人化設定表格
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                model TEXT,
                remember_context INTEGER,
                system_prompt TEXT
            )
        """)
        conn.commit()
        conn.close()

    # --- 新增：讀取與寫入使用者設定的函式 ---
    def _get_user_settings(self, user_id: str) -> dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()
        conn.close()

        if user_row:
            # 將 0/1 轉換回 True/False
            settings = dict(user_row)
            settings["remember_context"] = bool(settings["remember_context"])
            return settings
        else:
            # 如果使用者不存在，返回全域預設值
            global_prompt = self.config.get("default_system_prompt", DEFAULT_SETTINGS["system_prompt"])
            return {**DEFAULT_SETTINGS, "system_prompt": global_prompt}

    def _update_user_setting(self, user_id: str, key: str, value):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # 將 True/False 轉換為 1/0 存入資料庫
        if isinstance(value, bool):
            value = 1 if value else 0
        
        # 使用 INSERT OR IGNORE 確保使用者存在，然後用 UPDATE 更新
        cursor.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (user_id,))
        cursor.execute(f"UPDATE user_settings SET {key} = ? WHERE user_id = ?", (value, user_id))
        conn.commit()
        conn.close()

    # --- 資料庫相關函式 (chat_history) ---
    def _add_message_to_db(self, user_id: str, role: str, content: str, model_used: Optional[str] = None):
        # ... 此函式維持原樣 ...
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_history (user_id, role, content, model_used, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, role, content, model_used, datetime.datetime.now()))
        conn.commit()
        conn.close()

    # --- 修改：讓 get_user_history 讀取使用者的個人化系統提示 ---
    def _get_user_history_from_db(self, user_id: str, limit: int = 11) -> list:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        messages = []
        
        # 獲取使用者的個人設定
        user_settings = self._get_user_settings(user_id)
        system_prompt_content = user_settings["system_prompt"]
        
        # 檢查歷史紀錄中是否已有 system prompt，沒有則新增
        cursor.execute("SELECT 1 FROM chat_history WHERE user_id = ? AND role = 'system'", (user_id,))
        system_prompt_exists = cursor.fetchone()

        if not system_prompt_exists:
            self._add_message_to_db(user_id, "system", system_prompt_content)

        # 將系統提示加入 messages
        messages.append({"role": "system", "content": system_prompt_content})

        num_user_assistant_to_fetch = max(0, limit - 1)
        if num_user_assistant_to_fetch > 0:
            cursor.execute("""
                SELECT role, content FROM chat_history
                WHERE user_id = ? AND role IN ('user', 'assistant')
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (user_id, num_user_assistant_to_fetch))
            user_assistant_history = cursor.fetchall()
            for row in reversed(user_assistant_history):
                messages.append({"role": row["role"], "content": row["content"]})
        conn.close()
        return messages

    def _clear_user_history_in_db(self, user_id: str):
        # ... 此函式維持原樣 ...
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def _get_raw_user_history_for_viewing(self, user_id: str, limit: int = 10) -> list:
        # ... 此函式維持原樣 ...
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content, model_used, timestamp FROM chat_history
            WHERE user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, limit))
        history_rows = cursor.fetchall()
        conn.close()
        return history_rows

    # --- 核心對話邏輯 (維持原樣) ---
    async def _call_chatgpt_api(self, user_id: str, prompt: str, model: str, remember_context: bool) -> str:
        # ... 此函式維持原樣 ...
        messages_for_api = []
        if remember_context:
            messages_for_api = self._get_user_history_from_db(user_id, limit=11)
            messages_for_api.append({"role": "user", "content": prompt})
        else:
            user_settings = self._get_user_settings(user_id)
            system_prompt = user_settings.get("system_prompt", DEFAULT_SETTINGS["system_prompt"])
            messages_for_api = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]

        response = self.client.chat.completions.create(
            model=model,
            messages=messages_for_api
        )
        reply_content = response.choices[0].message.content.strip()

        if remember_context:
            self._add_message_to_db(user_id, "user", prompt)
            self._add_message_to_db(user_id, "assistant", reply_content, model_used=model)

        return reply_content

    # --- 修改：訊息監聽事件，讓它讀取使用者設定 ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user or message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_in_listen_channel = message.channel.id in self.listen_channel_ids

        if not is_dm and not is_in_listen_channel:
            return
        
        if message.content.startswith(self.bot.command_prefix):
            return
            
        prompt = message.content.strip()
        if not prompt:
            return

        user_id_str = str(message.author.id)
        
        # 讀取使用者的個人化設定
        user_settings = self._get_user_settings(user_id_str)
        model = user_settings.get("model", DEFAULT_SETTINGS["model"])
        remember_context = user_settings.get("remember_context", DEFAULT_SETTINGS["remember_context"])

        try:
            async with message.channel.typing():
                reply_content = await self._call_chatgpt_api(
                    user_id=user_id_str,
                    prompt=prompt,
                    model=model,
                    remember_context=remember_context
                )
            await message.reply(reply_content)
        except Exception as