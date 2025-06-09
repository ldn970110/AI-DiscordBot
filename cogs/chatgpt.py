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
        self.config = bot.config
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.db_path = "/data/user_chat_history.db" 

        # --- 新增：從 config 讀取要監聽的頻道 ID 列表 ---
        # 我們將 ID 轉換為整數以利比對
        self.listen_channel_ids = [int(channel_id) for channel_id in self.config.get("listen_channel_ids", [])]
        
        self._init_db()

    # --- 資料庫相關函式 (維持原樣) ---
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
        
        default_system_content = self.config.get(
            "default_system_prompt", 
            "請用繁體中文回答。"
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
        # ... 此函式維持原樣，此處省略以節省篇幅 ...
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
        # ... 此函式維持原樣，此處省略以節省篇幅 ...
        messages_for_api = []
        if remember_context:
            messages_for_api = self._get_user_history_from_db(user_id, limit=11)
            messages_for_api.append({"role": "user", "content": prompt})
        else:
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
            self._add_message_to_db(user_id, "user", prompt)
            self._add_message_to_db(user_id, "assistant", reply_content, model_used=model)

        return reply_content
        
    # --- 修改：訊息監聽事件 ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 1. 忽略機器人自己或其他機器人的訊息
        if message.author == self.bot.user or message.author.bot:
            return

        # 2. 判斷訊息是否來自應監聽的範圍 (私訊 或 指定頻道)
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_in_listen_channel = message.channel.id in self.listen_channel_ids

        # 如果訊息不是來自私訊，也不在指定的監聽頻道中，就直接忽略
        if not is_dm and not is_in_listen_channel:
            return
        
        # 3. 忽略指令，避免與斜線指令或!指令衝突
        if message.content.startswith(self.bot.command_prefix):
            return
            
        # 4. 取得訊息內容作為 prompt
        prompt = message.content.strip()
        if not prompt: # 如果是空訊息或只有附件，也忽略
            return

        # 5. 呼叫核心 API 函式進行對話
        user_id_str = str(message.author.id)
        model = "gpt-4-turbo" # 此處使用預設模型
        remember_context = True # 在監聽模式下，預設開啟歷史紀錄

        try:
            # 顯示"正在輸入..."的狀態
            async with message.channel.typing():
                reply_content = await self._call_chatgpt_api(
                    user_id=user_id_str,
                    prompt=prompt,
                    model=model,
                    remember_context=remember_context
                )
            # 以回覆的方式傳送訊息
            await message.reply(reply_content)

        except Exception as e:
            print(f"Error in on_message handler for user {user_id_str}: {e}")
            await message.reply(f"❌ 處理你的訊息時發生錯誤 ({type(e).__name__})。")


    # --- 其他指令 (維持原樣) ---
    # ... /chatgpt, /clear_my_chat_history, /joke, /view_user_history 等指令維持原樣 ...
    # ... 此處省略以節省篇幅 ...
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
            # 呼叫重構後的核心函式
            reply_content = await self._call_chatgpt_api(
                user_id=user_id_str,
                prompt=prompt,
                model=model,
                remember_context=remember_context
            )
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