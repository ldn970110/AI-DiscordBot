import os
import sys
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import signal
import json # <--- 新增

# 載入環境變數
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 設定 Intents
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- 事件：機器人準備就緒 ---
@bot.event
async def on_ready():
    slash = await bot.tree.sync()
    print(f"目前登入身份 --> {bot.user}")
    print(f"載入 {len(slash)} 個斜線指令")

# --- 指令：載入/卸載/重載 Cog ---
@bot.command()
@commands.is_owner()
async def load(ctx, extension):
    await bot.load_extension(f"cogs.{extension}")
    await ctx.send(f"Loaded {extension} done.")

@bot.command()
@commands.is_owner()
async def unload(ctx, extension):
    await bot.unload_extension(f"cogs.{extension}")
    await ctx.send(f"UnLoaded {extension} done.")

@bot.command()
@commands.is_owner()
async def reload(ctx, extension):
    await bot.reload_extension(f"cogs.{extension}")
    await ctx.send(f"ReLoaded {extension} done.")

# --- 函式：載入所有 Cogs ---
async def load_extensions():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            await bot.load_extension(f"cogs.{filename[:-3]}")

# --- 指令：重啟/關閉機器人 ---
@bot.command()
@commands.is_owner()
async def restart(ctx):
    """重啟機器人"""
    await ctx.send("🔄 機器人正在重新啟動...")
    await bot.close()
    os.execv(sys.executable, ['python'] + sys.argv)

@bot.command()
@commands.is_owner()
async def stop(ctx):
    """關閉機器人"""
    await ctx.send("⚠️ 機器人即將關閉...")
    await bot.close()

# --- 優雅關機的訊號處理 ---
async def graceful_shutdown():
    print("⚠️ 收到關機訊號，正在關閉機器人...")
    await bot.close()

def signal_handler(sig, frame):
    # 這確保了在非同步環境中安全地執行關機程序
    bot.loop.create_task(graceful_shutdown())

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- 主程式進入點 ---
async def main():
    # 在啟動機器人之前，先讀取設定檔
    try:
        with open('config.json', 'r', encoding='utf8') as jfile:
            # 將設定內容附加到 bot 物件上，方便 cogs 取用
            bot.config = json.load(jfile)
            print("✅ config.json 載入成功。")
    except FileNotFoundError:
        print("⚠️ config.json 未找到，將使用空設定。")
        bot.config = {}
    except json.JSONDecodeError:
        print("❌ 解讀 config.json 失敗，將使用空設定。")
        bot.config = {}

    # 啟動機器人
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, RuntimeError):
        # 捕捉到 Ctrl+C 或其他執行時錯誤時，確保事件循環能正確關閉
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(graceful_shutdown())
        else:
            loop.run_until_complete(graceful_shutdown())