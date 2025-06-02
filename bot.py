import os
import sys
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import signal
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
intents = discord.Intents.all()

bot=commands.Bot(command_prefix = "!", intents = intents)

@bot.event
async def on_ready():
    slash = await bot.tree.sync()
    print(f"目前登入身份 --> {bot.user}")
    print(f"載入 {len(slash)} 個斜線指令")

@bot.command()
async def load(ctx,extension):
    await bot.load_extension(f"cogs.{extension}")
    await ctx.send(f"Loaded {extension} done.")

@bot.command()
async def unload(ctx,extension):
    await bot.unload_extension(f"cogs.{extension}")
    await ctx.send(f"UnLoaded {extension} done.")

@bot.command()
async def reload(ctx,extension):
    await bot.reload_extension(f"cogs.{extension}")
    await ctx.send(f"ReLoaded {extension} done.")

async def load_extensions():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            await bot.load_extension(f"cogs.{filename[:-3]}")

@bot.command()
@commands.is_owner()  # 限制只有擁有者能執行
async def restart(ctx):
    """重啟機器人"""
    await ctx.send("🔄 機器人正在重新啟動...")
    await bot.close()
    
    os.execv(sys.executable, ['python'] + sys.argv)
    
@bot.command()
@commands.is_owner()  # 只有 bot 擁有者可以執行
async def stop(ctx):
    await ctx.send("⚠️ 機器人即將關閉...")
    await bot.close()

async def graceful_shutdown():

    print("⚠️ 收到 Ctrl+C，正在關閉機器人...")
    await bot.close()

def signal_handler(sig, frame):
    loop = asyncio.get_event_loop()
    loop.create_task(graceful_shutdown())

signal.signal(signal.SIGINT, signal_handler)

async def main():
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())  # 在一般環境運行
    except RuntimeError:
        # 如果 asyncio.run() 失敗，改用 get_event_loop() 執行
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())

