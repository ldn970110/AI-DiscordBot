import discord
from discord.ext import commands
from discord import app_commands # 確保引入 app_commands

class Main(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="顯示機器人的延遲時間")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)  # 轉換為毫秒
        await interaction.response.send_message(f"Pong! 目前延遲：{latency}ms")

async def setup(bot: commands.Bot):
    await bot.add_cog(Main(bot))