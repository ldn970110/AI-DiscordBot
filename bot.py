import os
import sys
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import signal
import json
import logging  # 引入 logging 模組

# --- 優化 1：設定日誌系統 ---
# 設定日誌記錄器
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger = logging.getLogger("discord_bot")
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# --- 優化 2：檢查必要的環境變數 ---
# 載入環境變數
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TOKEN:
    logger.critical("❌ 致命錯誤：環境變數中未找到 DISCORD_BOT_TOKEN！")
    sys.exit(1)

# 設定 Intents
intents = discord.Intents.all()
# 您也可以只啟用您需要的 Intents，這在未來是更好的做法
# intents = discord.Intents.default()
# intents.message_content = True
# intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- 事件：機器人準備就緒 ---
@bot.event
async def on_ready():
    logger.info(f"目前登入身份 --> {bot.user} (ID: {bot.user.id})")
    try:
        slash = await bot.tree.sync()
        logger.info(f"載入 {len(slash)} 個斜線指令")
    except Exception as e:
        logger.error(f"同步斜線指令時發生錯誤：{e}")


# --- 指令：載入/卸載/重載 Cog ---
@bot.command()
@commands.is_owner()
async def load(ctx, extension):
    try:
        await bot.load_extension(f"cogs.{extension}")
        await ctx.send(f"✅ 已載入 `{extension}` cog。")
        logger.info(f"Cog '{extension}' loaded by {ctx.author}.")
    except Exception as e:
        await ctx.send(f"❌ 載入 `{extension}` cog 失敗：{e}")
        logger.error(f"Failed to load cog '{extension}': {e}")

@bot.command()
@commands.is_owner()
async def unload(ctx, extension):
    try:
        await bot.unload_extension(f"cogs.{extension}")
        await ctx.send(f"✅ 已卸載 `{extension}` cog。")
        logger.info(f"Cog '{extension}' unloaded by {ctx.author}.")
    except Exception as e:
        await ctx.send(f"❌ 卸載 `{extension}` cog 失敗：{e}")
        logger.error(f"Failed to unload cog '{extension}': {e}")


@bot.command()
@commands.is_owner()
async def reload(ctx, extension):
    try:
        await bot.reload_extension(f"cogs.{extension}")
        await ctx.send(f"✅ 已重新載入 `{extension}` cog。")
        logger.info(f"Cog '{extension}' reloaded by {ctx.author}.")
    except Exception as e:
        await ctx.send(f"❌ 重新載入 `{extension}` cog 失敗：{e}")
        logger.error(f"Failed to reload cog '{extension}': {e}")


# --- 優化 3：更穩健的 Cog 載入函式 ---
async def load_extensions():
    logger.info("開始載入所有 cogs...")
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            cog_name = f"cogs.{filename[:-3]}"
            try:
                await bot.load_extension(cog_name)
                logger.info(f"成功載入 {cog_name}")
            except Exception as e:
                logger.error(f"載入 {cog_name} 失敗. 錯誤: {e}", exc_info=True)
    logger.info("所有 cogs 載入完畢。")

# --- 擁有者指令 (維持原樣) ---
@bot.command()
@commands.is_owner()
async def restart(ctx):
    """重啟機器人"""
    await ctx.send("🔄 機器人正在重新啟動...")
    logger.warning(f"Bot restart initiated by {ctx.author}.")
    await bot.close()
    os.execv(sys.executable, ['python'] + sys.argv)

@bot.command()
@commands.is_owner()
async def stop(ctx):
    """關閉機器人"""
    await ctx.send("⚠️ 機器人即將關閉...")
    logger.warning(f"Bot stop initiated by {ctx.author}.")
    await bot.close()

# --- 優雅關機的訊號處理 (維持原樣，這已是很好的實踐) ---
async def graceful_shutdown(signal_type):
    logger.warning(f"收到關機訊號 {signal_type}，正在關閉機器人...")
    await bot.close()
    logger.info("機器人已成功關閉。")

def signal_handler(sig, frame):
    asyncio.create_task(graceful_shutdown(signal.Signals(sig).name))

# --- 主程式進入點 ---
async def main():
    # 載入設定檔
    try:
        with open('config.json', 'r', encoding='utf8') as jfile:
            bot.config = json.load(jfile)
            logger.info("✅ config.json 載入成功。")
    except FileNotFoundError:
        logger.warning("⚠️ config.json 未找到，將使用空設定。")
        bot.config = {}
    except json.JSONDecodeError:
        logger.error("❌ 解讀 config.json 失敗，將使用空設定。")
        bot.config = {}

    # 啟動機器人
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)

if __name__ == "__main__":
    # 設定訊號處理
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, signal_handler)
        except OSError:
            # 在某些環境下(如Windows)可能不支援所有訊號
            pass

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("手動中斷程式 (Ctrl+C)。")