import discord
from discord.ext import commands
from discord import app_commands
import time
import platform

class Main(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 紀錄機器人啟動時間
        self.start_time = time.time()

    @app_commands.command(name="ping", description="顯示機器人的延遲時間")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)  # 轉換為毫秒
        await interaction.response.send_message(f"Pong! 目前延遲：{latency}ms")

    @app_commands.command(name="status", description="顯示機器人的目前狀態")
    async def status(self, interaction: discord.Interaction):
        # 計算運行時間
        uptime_seconds = time.time() - self.start_time
        uptime_str = time.strftime("%H:%M:%S", time.gmtime(uptime_seconds))
        
        # 獲取資訊
        latency = round(self.bot.latency * 1000)
        guild_count = len(self.bot.guilds)
        python_version = platform.python_version()
        discord_py_version = discord.__version__

        embed = discord.Embed(
            title=f"{self.bot.user.name} 狀態報告",
            description="以下是機器目前的即時資訊。",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        embed.add_field(name="🏓 延遲 (Latency)", value=f"{latency}ms", inline=True)
        embed.add_field(name="⏳ 運行時間 (Uptime)", value=uptime_str, inline=True)
        embed.add_field(name="📡 所在伺服器 (Guilds)", value=f"{guild_count} 個", inline=True)
        embed.add_field(name="🐍 Python 版本", value=python_version, inline=False)
        embed.add_field(name="🤖 Discord.py 版本", value=discord_py_version, inline=False)
        embed.set_footer(text=f"報告生成時間：{discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

        await interaction.response.send_message(embed=embed)

    @commands.is_owner()
    @app_commands.command(name="sync_commands", description="同步斜線指令 (僅限擁有者)")
    async def sync_commands(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f"✅ 成功同步 {len(synced)} 個斜線指令。")
        except Exception as e:
            await interaction.followup.send(f"❌ 同步失敗：{e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Main(bot))