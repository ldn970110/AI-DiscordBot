import discord
import os
from discord.ext import commands
from discord import app_commands
from openai import OpenAI
from typing import Optional, Literal
import sqlite3
import datetime

class ChatGPTCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 從 bot 物件取得 config，這是我們在 bot.py 中載入的設定檔 <--- 新增
        self.config = bot.config
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # 在 Railway部署時，路徑要指向 volume 掛載的路徑
        self.db_path = "/data/user_chat_history.db" 
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
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
        conn.commit()
        conn.close()

    def _add_message_to_db(self, user_id: str, role: str, content: str, model_used: Optional[str] = None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO chat_history (user_id, role, content, model_used, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, role, content, model_used, datetime.datetime.now()))
        conn.commit()
        conn.close()

    def _get_user_history_from_db(self, user_id: str, limit: int = 11) -> list:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        messages = []
        cursor.execute("""
            SELECT role, content FROM chat_history
            WHERE user_id = ? AND role = 'system'
            ORDER BY timestamp ASC LIMIT 1 
        """, (user_id,))
        system_prompt_row = cursor.fetchone()
        
        # --- 修改這裡 ---
        # 從 self.config 讀取預設提示，如果找不到就用一個備用的
        default_system_content = self.config.get(
            "default_system_prompt", 
            "請用繁體中文回答。" # 這是備用提示
        )
        
        if system_prompt_row:
            messages.append({"role": system_prompt_row["role"], "content": system_prompt_row["content"]})
        else:
            messages.append({"role": "system", "content": default_system_content})
            self._add_message_to_db(user_id, "system", default_system_content)
            
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

    @app_commands.command(name="chatgpt", description="與 ChatGPT 對話")
    @app_commands.describe(
        prompt="你想問什麼？",
        model="選擇要使用的模型（預設為 gpt-4-turbo）",
        remember_context="是否要在此次對話中使用並記錄歷史訊息（預設為是）"
    )
    async def chatgpt(
        self,
        interaction: discord.Interaction,
        prompt: str,
        model: Optional[Literal[
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo"
        ]] = "gpt-4-turbo",
        remember_context: bool = True 
    ):
        await interaction.response.defer()
        user_id_str = str(interaction.user.id) 
        try:
            messages_for_api = []
            if remember_context:
                messages_for_api = self._get_user_history_from_db(user_id_str, limit=11) 
                messages_for_api.append({"role": "user", "content": prompt})
            else:
                # --- 修改這裡 ---
                default_system_content = self.config.get(
                    "default_system_prompt",
                    "請用繁體中文回答。"
                )
                messages_for_api = [
                    {"role": "system", "content": default_system_content},
                    {"role": "user", "content": prompt}
                ]
            response = self.client.chat.completions.create(
                model=model,
                messages=messages_for_api
            )
            reply_content = response.choices[0].message.content.strip()
            if remember_context:
                self._add_message_to_db(user_id_str, "user", prompt)
                self._add_message_to_db(user_id_str, "assistant", reply_content, model_used=model)
            history_status = "已啟用" if remember_context else "未啟用"
            await interaction.followup.send(f"**使用模型：{model}** (歷史紀錄：{history_status})\n\n{reply_content}")
        except Exception as e:
            print(f"Error in chatgpt command for user {user_id_str}: {e}")
            await interaction.followup.send(f"❌ 發生錯誤，無法與 ChatGPT 通訊 ({type(e).__name__})。請稍後再試或聯繫管理員。")

    @app_commands.command(name="clear_my_chat_history", description="清除你個人所有與 ChatGPT 的對話歷史")
    async def clear_my_chat_history(self, interaction: discord.Interaction):
        user_id_str = str(interaction.user.id)
        try:
            self._clear_user_history_in_db(user_id_str)
            await interaction.response.send_message("🧹 你個人的 ChatGPT 對話歷史已清除。下次對話將從新的系統提示開始。")
        except Exception as e:
            print(f"Error clearing chat history for user {user_id_str}: {e}")
            await interaction.response.send_message(f"❌ 清除歷史時發生錯誤 ({type(e).__name__})。")

    @app_commands.command(name="joke", description="讓 ChatGPT 說個笑話")
    @app_commands.describe(topic="你希望笑話關於什麼主題？（可選）")
    async def joke(self, interaction: discord.Interaction, topic: Optional[str] = None):
        """讓 ChatGPT 說一個指定主題或隨機的笑話"""
        await interaction.response.defer()
        try:
            if topic:
                prompt = f"請你說一則關於「{topic}」的繁體中文笑話，要簡短有趣。"
            else:
                prompt = "請你隨機說一則繁體中文笑話，要簡短有趣。"

            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "你是一個幽默的助理，專門講笑話。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.8,
                max_tokens=150
            )
            joke_content = response.choices[0].message.content.strip()
            embed = discord.Embed(
                title="一個笑話來了！",
                description=joke_content,
                color=discord.Color.gold()
            )
            if topic:
                embed.set_footer(text=f"主題：{topic}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            print(f"Error in joke command: {e}")
            await interaction.followup.send(f"❌ 哎呀，我的腦袋短路了，想不出笑話... ({type(e).__name__})")
            
    # --- 擁有者專用指令 ---
    @app_commands.command(name="view_user_history", description="查看特定使用者的 ChatGPT 對話歷史紀錄 (僅限擁有者)")
    @app_commands.describe(
        user="要查看紀錄的 Discord 使用者",
        count="要顯示的最近訊息數量 (預設 10，最多 50)"
    )
    @commands.is_owner()
    async def view_user_history(self, interaction: discord.Interaction, user: discord.User, count: app_commands.Range[int, 1, 50] = 10):
        await interaction.response.defer(ephemeral=True) 

        user_id_to_view = str(user.id)
        history_records = self._get_raw_user_history_for_viewing(user_id_to_view, limit=count)

        if not history_records:
            await interaction.followup.send(f"🤷 找不到使用者 {user.mention} (ID: {user_id_to_view}) 的對話紀錄。", ephemeral=True)
            return
        
        # ... 此處省略了 view_user_history 的訊息格式化與發送邏輯，維持原樣即可 ...
        # (此處的程式碼與你原本的檔案相同，為了簡潔省略)
        # ...
        # 為了確保完整性，我將完整的程式碼貼上
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
        first_followup_sent = False

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