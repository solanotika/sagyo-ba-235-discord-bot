import discord
from discord import app_commands
from discord.ext import tasks
import os
import json
from datetime import datetime, timedelta, timezone
import logging
import re
import asyncio
import time

# --- ロギング設定 ---
logging.basicConfig(level=logging.INFO)

# --- 環境変数からIDを取得 ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv('DISCORD_BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
TARGET_VC_IDS_STR = os.getenv('TARGET_VC_IDS', '')
TARGET_VC_IDS = {int(id_str.strip()) for id_str in TARGET_VC_IDS_STR.split(',') if id_str.strip().isdigit()}
BUMP_CHANNEL_ID = int(os.getenv('BUMP_CHANNEL_ID'))
BUMP_LOG_CHANNEL_ID = int(os.getenv('BUMP_LOG_CHANNEL_ID'))
INTRO_CHANNEL_ID = int(os.getenv('INTRO_CHANNEL_ID'))
INTRO_ROLE_ID = int(os.getenv('INTRO_ROLE_ID'))
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID'))
WORK_LOG_CHANNEL_ID = int(os.getenv('WORK_LOG_CHANNEL_ID'))

# --- 状態を保存するファイル名 ---
BUMP_COUNT_FILE = 'data/bump_counts.json'
LAST_REMINDED_BUMP_ID_FILE = 'data/last_reminded_id.txt'

# --- グローバル変数 ---
active_sessions = {}

# --- ヘルパー関数群 ---
def format_duration(total_seconds):
    if total_seconds is None or total_seconds < 0:
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours)}時間 {int(minutes)}分 {int(seconds)}秒"

# --- Discord Botのクライアント設定 ---
intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db_pool = None
        self.loop_counter = 0 # 統合ループ用のカウンター

    async def setup_hook(self):
        try:
            self.db_pool = await asyncpg.create_pool(dsn=DATABASE_URL, max_size=5)
            logging.info("Successfully connected to the database.")
            async with self.db_pool.acquire() as connection:
                await connection.execute('''
                    CREATE TABLE IF NOT EXISTS work_logs (
                        user_id BIGINT PRIMARY KEY,
                        total_seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0
                    )
                ''')
                logging.info("Database table 'work_logs' initialized.")
        except Exception as e:
            self.db_pool = None
            logging.error(f"Failed to connect to the database: {e}")
            
        await self.tree.sync()

    async def close(self):
        if self.db_pool:
            await self.db_pool.close()
        await super().close()

client = MyClient(intents=intents)

# --- バックグラウンド処理のヘルパー関数 ---
async def do_periodic_role_check():
    # (旧 periodic_role_check の中身)
    pass

async def do_bump_reminder_check():
    # (旧 check_bump_reminder の中身)
    pass

# --- 統合された単一バックグラウンドループ ---
@tasks.loop(minutes=15)
async def unified_background_loop():
    if not client.is_ready() or not client.db_pool:
        logging.warning("Bot is not ready or DB is not connected. Skipping loop.")
        return

    # カウンターを増やす
    client.loop_counter += 1
    logging.info(f"--- Running unified background loop (Cycle: {client.loop_counter}) ---")

    # 毎回実行する軽い処理 (Bumpリマインダー)
    await do_bump_reminder_check()

    # 8回に1回 (15分 * 8 = 2時間) だけ実行する重い処理 (ロールチェック)
    if client.loop_counter % 8 == 0:
        await do_periodic_role_check()

# --- Bot起動時の処理 ---
@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user}')
    if not os.path.exists('data'):
        os.makedirs('data')
    
    if client.db_pool and not unified_background_loop.is_running():
        # ループを開始する (初回実行は15分後)
        unified_background_loop.start()
        logging.info("Unified background loop has been started.")

# --- イベントハンドラとコマンド (内容は変更なし、必要に応じて省略を解除) ---
@client.event
async def on_message(message):
    # (変更なし)
    pass

@client.event
async def on_voice_state_update(member, before, after):
    # (変更なし)
    pass

@client.tree.command(name="worktime", description="指定したメンバーの累計作業時間を表示します。")
async def worktime(interaction: discord.Interaction, member: discord.Member):
    # (変更なし)
    pass

@client.tree.command(name="announce", description="指定したチャンネルにBotからお知らせを投稿します。(管理者限定)")
@app_commands.describe(channel="投稿先のチャンネル")
@app_commands.checks.has_permissions(administrator=True)
async def announce(interaction: discord.Interaction, channel: discord.TextChannel):
    # (変更なし)
    pass
@announce.error
async def announce_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # (変更なし)
    pass

# --- メイン処理 ---
if __name__ == "__main__":
    RECONNECT_DELAY = 300
    while True:
        try:
            if TOKEN and DATABASE_URL:
                client.run(TOKEN)
            else:
                logging.error("Required environment variables not found. Exiting.")
                break 
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logging.warning(f"Rate-limited. Waiting {RECONNECT_DELAY}s.")
                time.sleep(RECONNECT_DELAY)
            else:
                raise
        except Exception as e:
            logging.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(60)
