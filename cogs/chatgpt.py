import discord
import os
from discord.ext import commands
from discord import app_commands
from openai import OpenAI
from typing import Optional, Literal
import logging
import json

# --- 新增：匯入搜尋工具 ---
from duckduckgo_search import DDGS

# --- 新增：從我們的新模組中匯入所有函式 ---
from .utils import db_manager

logger = logging.getLogger("discord_bot")

DEFAULT_SETTINGS = {
    "model": "gpt-4o", # 建議使用 gpt-4o，工具使用效果更好
    "remember_context": True,
    "system_prompt": "請你之後的回應一律使用繁體中文。",
    "enable_search": False, # 預設關閉搜尋
}

# --- 新增：定義我們的搜尋工具函式 ---
def web_search(query: str, max_results: int = 5) -> str:
    """使用 DuckDuckGo 進行網路搜尋，並返回格式化的結果。"""
    logger.info(f"執行網路搜尋，關鍵字：{query}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "沒有找到相關的搜尋結果。"
        # 將結果格式化為簡單的字串
        return "\n\n".join([f"標題: {r['title']}\n連結: {r['href']}\n摘要: {r['body']}" for r in results])
    except Exception as e:
        logger.error(f"網路搜尋時發生錯誤: {e}")
        return f"搜尋時發生錯誤: {e}"

class ChatGPTCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        db_manager.init_db()
        self.listened_channel_ids_cache = db_manager.load_listened_channels_to_cache()
        
        # --- 新增：定義工具的規格，讓 OpenAI 知道有這個工具可用 ---
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "當你需要回答關於即時資訊、近期事件或任何你知識庫中沒有的特定主題時，使用這個工具進行網路搜尋。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "要搜尋的關鍵字或問題，例如：'2024年奧運主辦城市' 或 'Nvidia最新股價'。",
                            },
                        },
                        "required": ["query"],
                    },
                },
            }
        ]
        # 將函式名稱對應到真正的函式
        self.available_functions = {"web_search": web_search}

    # --- 修改：核心對話邏輯，加入工具使用迴圈 ---
    async def _call_chatgpt_api(self, user_id: str, prompt: str, user_settings: dict) -> str:
        model = user_settings["model"]
        remember_context = user_settings["remember_context"]
        enable_search = user_settings["enable_search"]
        system_prompt = user_settings["system_prompt"]
        
        messages = []
        if remember_context:
            messages = db_manager.get_user_history_from_db(user_id, system_prompt)
        else:
            messages = [{"role": "system", "content": system_prompt}]
        
        messages.append({"role": "user", "content": prompt})
        
        # 最多允許2次工具呼叫，防止無限迴圈
        for _ in range(3): 
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                tools=self.tools if enable_search else None, # 如果使用者啟用搜尋，才提供工具
                tool_choice="auto" if enable_search else None,
            )
            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            if not tool_calls:
                # 沒有工具呼叫，直接返回結果
                reply_content = response_message.content
                if remember_context:
                    db_manager.add_message_to_db(user_id, "user", prompt)
                    db_manager.add_message_to_db(user_id, "assistant", reply_content, model_used=model)
                return reply_content

            # 有工具呼叫，執行工具
            messages.append(response_message)  # 將助理的工具呼叫請求也加入歷史
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_to_call = self.available_functions[function_name]
                function_args = json.loads(tool_call.function.arguments)
                function_response = function_to_call(**function_args)
                
                # 將工具的執行結果加入歷史
                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": function_response,
                })
        
        # 如果迴圈結束仍未獲得最終答案，返回一個提示訊息
        return "模型在多次嘗試使用工具後仍無法給出最終回覆，請嘗試簡化您的問題。"

    # --- 事件監聽器 ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_in_listen_channel = hasattr(message.channel, 'id') and message.channel.id in self.listened_channel_ids_cache

        if not is_dm and not is_in_listen_channel: return
        if message.content.startswith(self.bot.command_prefix): return
            
        prompt = message.content.strip()
        if not prompt: return

        user_id_str = str(message.author.id)
        
        default_prompt = self.bot.config.get("default_system_prompt", DEFAULT_SETTINGS['system_prompt'])
        user_settings = db_manager.get_user_settings(user_id_str, {**DEFAULT_SETTINGS, "system_prompt": default_prompt})
        
        try:
            async with message.channel.typing():
                reply_content = await self._call_chatgpt_api(
                    user_id=user_id_str,
                    prompt=prompt,
                    user_settings=user_settings
                )
            await message.reply(reply_content)
        except Exception as e:
            logger.error(f"Error in on_message handler for user {user_id_str}: {e}", exc_info=True)
            await message.reply(f"❌ 處理你的訊息時發生錯誤 ({type(e).__name__})。")

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ 您沒有執行此指令所需的權限（需要「管理頻道」權限）。", ephemeral=True)
        else:
            logger.error(f"App command error: {error}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 指令發生未知的錯誤。", ephemeral=True)

    # --- 指令群組 ---
    channel_group = app_commands.Group(name="channel", description="管理機器人監聽的頻道")

    @channel_group.command(name="register", description="將目前頻道註冊為AI對話頻道")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def register(self, interaction: discord.Interaction):
        success = db_manager.add_listened_channel(str(interaction.channel_id), str(interaction.guild_id), str(interaction.user.id))
        if success:
            self.listened_channel_ids_cache.add(interaction.channel_id)
            await interaction.response.send_message(f"✅ 頻道 <#{interaction.channel_id}> 已成功註冊為AI對話頻道。")
            logger.info(f"頻道 {interaction.channel_id} 已由 {interaction.user.id} 註冊。")
        else:
            await interaction.response.send_message(f"ℹ️ 頻道 <#{interaction.channel_id}> 已經在監聽列表中了。", ephemeral=True)

    @channel_group.command(name="unregister", description="將目前頻道從AI對話頻道中移除")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unregister(self, interaction: discord.Interaction):
        success = db_manager.remove_listened_channel(str(interaction.channel_id))
        if success:
            if interaction.channel_id in self.listened_channel_ids_cache:
                self.listened_channel_ids_cache.remove(interaction.channel_id)
            await interaction.response.send_message(f"✅ 頻道 <#{interaction.channel_id}> 已成功從監聽列表中移除。")
            logger.info(f"頻道 {interaction.channel_id} 已由 {interaction.user.id} 移除。")
        else:
            await interaction.response.send_message(f"ℹ️ 頻道 <#{interaction.channel_id}> 並不在監聽列表中。", ephemeral=True)

    @channel_group.command(name="list", description="列出此伺服器中所有被監聽的頻道")
    async def list_channels(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用。", ephemeral=True)
            return
            
        channels = db_manager.get_listened_channels_for_guild(str(interaction.guild_id))
        
        if not channels:
            description = "目前沒有任何頻道被設定為AI對話頻道。"
        else:
            description = "以下是本伺服器中，我會進行對話的頻道：\n" + "\n".join([f"- <#{channel[0]}>" for channel in channels])
            
        embed = discord.Embed(title=f"“{interaction.guild.name}” 的AI監聽頻道列表", description=description, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- 其他指令 ---
    @app_commands.command(name="settings", description="設定你個人的對話偏好")
    @app_commands.describe(
        model="【可選】設定你偏好的對話模型 (推薦gpt-4o)",
        remember_context="【可選】設定是否要啟用對話歷史紀錄",
        system_prompt="【可選】設定你對AI的個人化指示",
        enable_search="【可選】設定是否允許AI自動上網搜尋"
    )
    async def settings(self, interaction: discord.Interaction, 
                       model: Optional[Literal["gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]] = None, 
                       remember_context: Optional[bool] = None, 
                       system_prompt: Optional[str] = None,
                       enable_search: Optional[bool] = None):
        await interaction.response.defer(ephemeral=True)
        user_id_str = str(interaction.user.id)

        # 更新設定
        if model is not None: db_manager.update_user_setting(user_id_str, "model", model)
        if remember_context is not None: db_manager.update_user_setting(user_id_str, "remember_context", remember_context)
        if system_prompt is not None: db_manager.update_user_setting(user_id_str, "system_prompt", system_prompt)
        if enable_search is not None: db_manager.update_user_setting(user_id_str, "enable_search", enable_search)

        # 顯示更新後的設定
        default_prompt = self.bot.config.get("default_system_prompt", DEFAULT_SETTINGS['system_prompt'])
        current_settings = db_manager.get_user_settings(user_id_str, {**DEFAULT_SETTINGS, "system_prompt": default_prompt})
        
        embed = discord.Embed(title=f"{interaction.user.display_name} 的個人化設定", color=discord.Color.blue())
        embed.add_field(name="🧠 使用模型", value=f"`{current_settings['model']}`", inline=False)
        embed.add_field(name="💾 歷史紀錄", value="✅ 已啟用" if current_settings['remember_context'] else "❌ 已停用", inline=False)
        embed.add_field(name="🌐 自動搜尋", value="✅ 已啟用" if current_settings['enable_search'] else "❌ 已停用", inline=False)
        embed.add_field(name="📜 系統提示", value=f"```\n{current_settings['system_prompt']}\n```", inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="clear_my_chat_history", description="清除你個人所有與 ChatGPT 的對話歷史")
    async def clear_my_chat_history(self, interaction: discord.Interaction):
        try:
            db_manager.clear_user_history_in_db(str(interaction.user.id))
            await interaction.response.send_message("🧹 你個人的 ChatGPT 對話歷史已清除。下次對話將從新的系統提示開始。")
        except Exception as e:
            logger.error(f"清除使用者 {interaction.user.id} 的歷史紀錄時發生錯誤: {e}", exc_info=True)
            await interaction.response.send_message(f"❌ 清除歷史時發生錯誤 ({type(e).__name__})。", ephemeral=True)
            
    @app_commands.command(name="view_user_history", description="查看特定使用者的 ChatGPT 對話歷史紀錄 (僅限擁有者)")
    @app_commands.describe(user="要查看紀錄的 Discord 使用者", count="要顯示的最近訊息數量 (預設 10，最多 50)")
    @commands.is_owner()
    async def view_user_history(self, interaction: discord.Interaction, user: discord.User, count: app_commands.Range[int, 1, 50] = 10):
        await interaction.response.defer(ephemeral=True) 
        user_id_to_view = str(user.id)
        
        history_records = db_manager.get_raw_user_history_for_viewing(user_id_to_view, limit=count)

        if not history_records:
            await interaction.followup.send(f"🤷 找不到使用者 {user.mention} (ID: {user_id_to_view}) 的對話紀錄。", ephemeral=True)
            return
        
        formatted_entries = []
        for record in reversed(history_records):
            timestamp_str = str(record["timestamp"])
            formatted_ts = timestamp_str.split('.')[0]
            
            role = record["role"].upper()
            content = record["content"]
            model_info = f" (模型: {record['model_used']})" if record["model_used"] else ""
            display_content = content[:300] + ('...' if len(content) > 300 else '')
            entry_text = f"**[{formatted_ts}] {role}**{model_info}:\n```\n{display_content}\n```\n---\n"
            formatted_entries.append(entry_text)

        header = f"📜 使用者 {user.mention} (ID: {user_id_to_view}) 的最近 {len(history_records)} 條對話紀錄 (由舊到新):\n---\n"
        current_message_batch = header

        for entry_text in formatted_entries:
            if len(current_message_batch) + len(entry_text) > 1950:
                await interaction.followup.send(current_message_batch, ephemeral=True)
                current_message_batch = ""
            current_message_batch += entry_text
        
        if current_message_batch and (current_message_batch != header or not formatted_entries):
             await interaction.followup.send(current_message_batch, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ChatGPTCog(bot))
