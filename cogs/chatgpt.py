import discord
import os
from discord.ext import commands # 確保 commands 被引入
from discord import app_commands
from openai import OpenAI
from typing import Optional, Literal
import sqlite3
import datetime # 確保 datetime 被引入

class ChatGPTCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) #
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
        default_system_content = "請你之後的回應一律使用繁體中文。" #
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

    # --- 新增的方法 ---
    def _get_raw_user_history_for_viewing(self, user_id: str, limit: int = 10) -> list:
        """從資料庫獲取指定使用者的原始對話歷史紀錄以供查看 (最新的在前面)"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row # 讓你可以用欄位名稱存取資料
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
    # --- 新增的方法結束 ---

    @app_commands.command(name="chatgpt", description="與 ChatGPT 對話")
    @app_commands.describe(
        prompt="你想問什麼？",
        model="選擇要使用的模型（預設為 gpt-4-turbo）", #
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
        ]] = "gpt-4-turbo", #
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
                default_system_content = "請你之後的回應一律使用繁體中文。" #
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
            # 根據是否有主題，建立不同的提示
            if topic:
                prompt = f"請你說一則關於「{topic}」的繁體中文笑話，要簡短有趣。"
            else:
                prompt = "請你隨機說一則繁體中文笑話，要簡短有趣。"

            # 呼叫 OpenAI API
            # 對於講笑話這種簡單任務，使用 gpt-3.5-turbo 就足夠了，速度快且成本低
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "你是一個幽默的助理，專門講笑話。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.8, # 讓笑話多一點創意
                max_tokens=150
            )

            joke_content = response.choices[0].message.content.strip()

            # 使用 Embed 來美化訊息
            embed = discord.Embed(
                title="一個笑話來了！",
                description=joke_content,
                color=discord.Color.gold() # 設定一個顏色
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
    @commands.is_owner() # 確保只有機器人擁有者能執行
    async def view_user_history(self, interaction: discord.Interaction, user: discord.User, count: app_commands.Range[int, 1, 50] = 10):
        await interaction.response.defer(ephemeral=True) # 回應僅發送者可見

        user_id_to_view = str(user.id)
        history_records = self._get_raw_user_history_for_viewing(user_id_to_view, limit=count)

        if not history_records:
            await interaction.followup.send(f"🤷 找不到使用者 {user.mention} (ID: {user_id_to_view}) 的對話紀錄。", ephemeral=True)
            return

        # 格式化並發送紀錄 (時間由舊到新排列顯示)
        # 資料庫取出時是 timestamp DESC (新到舊)，所以要反轉列表
        formatted_entries = []
        for record in reversed(history_records): 
            # SQLite 的 CURRENT_TIMESTAMP 預設格式通常是 'YYYY-MM-DD HH:MM:SS'
            # 如果是 datetime 物件，需要格式化:
            # if isinstance(record["timestamp"], datetime.datetime):
            #    formatted_ts = record["timestamp"].strftime('%Y-%m-%d %H:%M:%S')
            # else:
            #    formatted_ts = str(record["timestamp"]) # 直接使用字串
            formatted_ts = str(record["timestamp"]) # SQLite 通常返回字串

            role = record["role"].upper()
            content = record["content"]
            model_info = f" (模型: {record['model_used']})" if record["model_used"] else ""
            
            # 截斷過長的內容以利顯示
            display_content = content[:300] + ('...' if len(content) > 300 else '')
            
            entry_text = f"**[{formatted_ts}] {role}**{model_info}:\n```\n{display_content}\n```\n---\n"
            formatted_entries.append(entry_text)

        header = f"📜 使用者 {user.mention} (ID: {user_id_to_view}) 的最近 {len(history_records)} 條對話紀錄 (共查詢 {count} 條，時間由舊到新):\n---\n"
        
        # 第一條回應使用 followup.send
        current_message_batch = header
        first_followup_sent = False

        for entry_text in formatted_entries:
            if len(current_message_batch) + len(entry_text) > 1950: # Discord 訊息長度限制約 2000
                if not first_followup_sent:
                    await interaction.followup.send(current_message_batch, ephemeral=True)
                    first_followup_sent = True
                else:
                    await interaction.followup.send(current_message_batch, ephemeral=True) # 後續的也用 followup
                current_message_batch = "" # 開始新的訊息批次
            current_message_batch += entry_text
        
        # 發送最後剩餘的訊息批次
        if current_message_batch:
            # 如果 current_message_batch 只有 header，表示沒有任何 entry 被加入（可能都被過濾或 history_records 為空）
            # 但 history_records 為空的情況已在前面處理
            if not first_followup_sent and current_message_batch == header and not formatted_entries: 
                # 此情況理論上不會發生，因為 header 不會被單獨賦值給 current_message_batch 如果 formatted_entries 為空
                pass
            elif not first_followup_sent: # 如果所有內容能放在第一則訊息
                 await interaction.followup.send(current_message_batch, ephemeral=True)
            elif current_message_batch.strip(): # 如果有剩餘內容
                 await interaction.followup.send(current_message_batch, ephemeral=True)


    @view_user_history.error # is_owner() 失敗時的錯誤處理
    async def view_user_history_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, commands.NotOwner):
            await interaction.response.send_message("❌ 你沒有權限執行此指令。", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ 指令發生錯誤：{error}", ephemeral=True)
    # --- 新增的指令結束 ---

async def setup(bot: commands.Bot):
    await bot.add_cog(ChatGPTCog(bot))
