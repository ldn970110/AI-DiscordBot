import discord
import os
from discord.ext import commands
from discord import app_commands
from openai import OpenAI
from typing import Optional, Literal
import logging

# --- 新增：從我們的新模組中匯入所有函式 ---
from .utils import db_manager

# 獲取日誌記錄器
logger = logging.getLogger("discord_bot")

# 預設設定值
DEFAULT_SETTINGS = {
    "model": "gpt-4-turbo",
    "remember_context": True,
    "system_prompt": "請你之後的回應一律使用繁體中文。"
}

class ChatGPTCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        # --- 修改：啟動時直接呼叫 db_manager 來初始化和載入快取 ---
        db_manager.init_db()
        self.listened_channel_ids_cache = db_manager.load_listened_channels_to_cache()

    # --- 核心對話邏輯 ---
    async def _call_chatgpt_api(self, user_id: str, prompt: str, model: str, remember_context: bool) -> str:
        # 組合給 API 的訊息列表
        messages_for_api = []
        if remember_context:
            user_settings = db_manager.get_user_settings(user_id, {**DEFAULT_SETTINGS, "system_prompt": self.bot.config.get("default_system_prompt", DEFAULT_SETTINGS["system_prompt"])})
            system_prompt = user_settings["system_prompt"]
            messages_for_api = db_manager.get_user_history_from_db(user_id, system_prompt)
            messages_for_api.append({"role": "user", "content": prompt})
        else:
            # 如果不使用歷史紀錄，也從db獲取個人設定，若無則使用預設
            user_settings = db_manager.get_user_settings(user_id, DEFAULT_SETTINGS)
            messages_for_api = [
                {"role": "system", "content": user_settings["system_prompt"]},
                {"role": "user", "content": prompt}
            ]

        # 呼叫 OpenAI API
        response = self.client.chat.completions.create(model=model, messages=messages_for_api)
        reply_content = response.choices[0].message.content.strip()

        # 如果啟用歷史紀錄，則儲存對話
        if remember_context:
            db_manager.add_message_to_db(user_id, "user", prompt)
            db_manager.add_message_to_db(user_id, "assistant", reply_content, model_used=model)

        return reply_content

    # --- 事件監聽器 ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user or message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_in_listen_channel = hasattr(message.channel, 'id') and message.channel.id in self.listened_channel_ids_cache

        if not is_dm and not is_in_listen_channel:
            return
        
        if message.content.startswith(self.bot.command_prefix):
            return
            
        prompt = message.content.strip()
        if not prompt:
            return

        user_id_str = str(message.author.id)
        
        # 從資料庫獲取使用者設定
        default_prompt = self.bot.config.get("default_system_prompt", DEFAULT_SETTINGS['system_prompt'])
        user_settings = db_manager.get_user_settings(user_id_str, {**DEFAULT_SETTINGS, "system_prompt": default_prompt})
        
        try:
            async with message.channel.typing():
                reply_content = await self._call_chatgpt_api(
                    user_id=user_id_str,
                    prompt=prompt,
                    model=user_settings["model"],
                    remember_context=user_settings["remember_context"]
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
    @app_commands.describe(model="【可選】設定你偏好的對話模型", remember_context="【可選】設定是否要啟用對話歷史紀錄", system_prompt="【可選】設定你對AI的個人化指示")
    async def settings(self, interaction: discord.Interaction, model: Optional[Literal["gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]] = None, remember_context: Optional[bool] = None, system_prompt: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        user_id_str = str(interaction.user.id)

        if model is not None:
            db_manager.update_user_setting(user_id_str, "model", model)
        if remember_context is not None:
            db_manager.update_user_setting(user_id_str, "remember_context", remember_context)
        if system_prompt is not None:
            db_manager.update_user_setting(user_id_str, "system_prompt", system_prompt)

        default_prompt = self.bot.config.get("default_system_prompt", DEFAULT_SETTINGS['system_prompt'])
        current_settings = db_manager.get_user_settings(user_id_str, {**DEFAULT_SETTINGS, "system_prompt": default_prompt})
        
        embed = discord.Embed(title=f"{interaction.user.display_name} 的個人化設定", description="當您在監聽頻道或私訊中與我對話時，將會套用以下設定。", color=discord.Color.blue())
        embed.add_field(name="🧠 使用模型 (model)", value=f"`{current_settings['model']}`", inline=False)
        embed.add_field(name="💾 歷史紀錄 (remember_context)", value="✅ 已啟用" if current_settings['remember_context'] else "❌ 已停用", inline=False)
        embed.add_field(name="📜 系統提示 (system_prompt)", value=f"```\n{current_settings['system_prompt']}\n```", inline=False)
        embed.set_footer(text="若要修改，請在指令中直接給予新設定值。")
        await interaction.followup.send(embed=embed)

    # --- 修正點 1：@app_backs.command -> @app_commands.command ---
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
        
        # --- 修正點 2：self._get_raw... -> db_manager.get_raw... ---
        history_records = db_manager.get_raw_user_history_for_viewing(user_id_to_view, limit=count)

        if not history_records:
            await interaction.followup.send(f"🤷 找不到使用者 {user.mention} (ID: {user_id_to_view}) 的對話紀錄。", ephemeral=True)
            return
        
        formatted_entries = []
        for record in reversed(history_records):
            # 確保 timestamp 是字串才進行分割
            timestamp_str = str(record["timestamp"])
            formatted_ts = timestamp_str.split('.')[0] # 簡化時間顯示
            
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
