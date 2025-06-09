import discord
import os
from discord.ext import commands
from discord import app_commands
from openai import OpenAI
from typing import Optional, Literal
import sqlite3
import datetime
import logging # 引入 logging

# 獲取日誌記錄器
logger = logging.getLogger("discord_bot")

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
        
        user_settings = self._get_user_settings(user_id)
        system_prompt_content = user_settings["system_prompt"]
        
        cursor.execute("SELECT 1 FROM chat_history WHERE user_id = ? AND role = 'system'", (user_id,))
        system_prompt_exists = cursor.fetchone()

        if not system_prompt_exists:
            self._add_message_to_db(user_id, "system", system_prompt_content)

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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def _get_raw_user_history_for_viewing(self, user_id: str, limit: int = 10) -> list:
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

    async def _call_chatgpt_api(self, user_id: str, prompt: str, model: str, remember_context: bool) -> str:
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user or message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_in_listen_channel = False
        if hasattr(message.channel, 'id'):
            is_in_listen_channel = message.channel.id in self.listen_channel_ids

        if not is_dm and not is_in_listen_channel:
            return
        
        if message.content.startswith(self.bot.command_prefix):
            return
            
        prompt = message.content.strip()
        if not prompt:
            return

        user_id_str = str(message.author.id)
        
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
        except Exception as e:
            logger.error(f"Error in on_message handler for user {user_id_str}: {e}")
            await message.reply(f"❌ 處理你的訊息時發生錯誤 ({type(e).__name__})。")

    @app_commands.command(name="settings", description="設定你個人的對話偏好")
    @app_commands.describe(
        model="【可選】設定你偏好的對話模型",
        remember_context="【可選】設定是否要啟用對話歷史紀錄",
        system_prompt="【可選】設定你對AI的個人化指示 (例如：你是一位貓娘)"
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        model: Optional[Literal["gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]] = None,
        remember_context: Optional[bool] = None,
        system_prompt: Optional[str] = None
    ):
        await interaction.response.defer(ephemeral=True)
        user_id_str = str(interaction.user.id)

        if model is not None:
            self._update_user_setting(user_id_str, "model", model)
        if remember_context is not None:
            self._update_user_setting(user_id_str, "remember_context", remember_context)
        if system_prompt is not None:
            self._update_user_setting(user_id_str, "system_prompt", system_prompt)

        current_settings = self._get_user_settings(user_id_str)

        embed = discord.Embed(
            title=f"{interaction.user.display_name} 的個人化設定",
            description="當您在監聽頻道或私訊中與我對話時，將會套用以下設定。",
            color=discord.Color.blue()
        )
        embed.add_field(name="🧠 使用模型 (model)", value=f"`{current_settings['model']}`", inline=False)
        embed.add_field(name="💾 歷史紀錄 (remember_context)", value="✅ 已啟用" if current_settings['remember_context'] else "❌ 已停用", inline=False)
        embed.add_field(name="📜 系統提示 (system_prompt)", value=f"```\n{current_settings['system_prompt']}\n```", inline=False)
        embed.set_footer(text="若要修改，請在指令中直接給予新設定值。")

        await interaction.followup.send(embed=embed)


    @app_commands.command(name="clear_my_chat_history", description="清除你個人所有與 ChatGPT 的對話歷史")
    async def clear_my_chat_history(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        try:
            self._clear_user_history_in_db(user_id_str)
            await interaction.response.send_message("🧹 你個人的 ChatGPT 對話歷史已清除。下次對話將從新的系統提示開始。")
        except Exception as e:
            logger.error(f"清除使用者 {user_id_str} 的歷史紀錄時發生錯誤: {e}")
            await interaction.response.send_message(f"❌ 清除歷史時發生錯誤 ({type(e).__name__})。")
            
    @app_commands.command(name="view_user_history", description="查看特定使用者的 ChatGPT 對話歷史紀錄 (僅限擁有者)")
    @app_commands.describe(user="要查看紀錄的 Discord 使用者", count="要顯示的最近訊息數量 (預設 10，最多 50)")
    @commands.is_owner()
    async def view_user_history(self, interaction: discord.Interaction, user: discord.User, count: app_commands.Range[int, 1, 50] = 10):
        await interaction.response.defer(ephemeral=True) 
        user_id_to_view = str(user.id)
        history_records = self._get_raw_user_history_for_viewing(user_id_to_view, limit=count)

        if not history_records:
            await interaction.followup.send(f"🤷 找不到使用者 {user.mention} (ID: {user_id_to_view}) 的對話紀錄。", ephemeral=True)
            return
        
        formatted_entries = []
        for record in reversed(history_records):
            formatted_ts = str(record["timestamp"])
            role = record["role"].upper()
            content = record["content"]
            model_info = f" (模型: {record['model_used']})" if record["model_used"] else ""
            display_content = content[:300] + ('...' if len(content) > 300 else '')
            entry_text = f"**[{formatted_ts}] {role}**{model_info}:\n```\n{display_content}\n```\n---\n"
            formatted_entries.append(entry_text)

        header = f"📜 使用者 {user.mention} (ID: {user_id_to_view}) 的最近 {len(history_records)} 條對話紀錄 (共查詢 {count} 條，時間由舊到新):\n---\n"
        current_message_batch = header

        for entry_text in formatted_entries:
            if len(current_message_batch) + len(entry_text) > 1950:
                await interaction.followup.send(current_message_batch, ephemeral=True)
                current_message_batch = ""
            current_message_batch += entry_text
        
        if current_message_batch and (current_message_batch != header or not formatted_entries):
             await interaction.followup.send(current_message_batch, ephemeral=True)

    @view_user_history.error
    async def view_user_history_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, commands.NotOwner):
            await interaction.response.send_message("❌ 你沒有權限執行此指令。", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ 指令發生錯誤：{error}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ChatGPTCog(bot))