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
WORK_TIMES_FILE = 'data/work_times.json'

# --- グローバル変数 ---
active_sessions = {}

# --- 時間をフォーマットするヘルパー関数 ---
def format_duration(total_seconds):
    if total_seconds is None or total_seconds < 0:
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours)}時間 {int(minutes)}分 {int(seconds)}秒"

# --- 時間記録データをロード/セーブする関数 ---
def load_work_times():
    if not os.path.exists(WORK_TIMES_FILE):
        return {}
    with open(WORK_TIMES_FILE, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_work_times(data):
    with open(WORK_TIMES_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# --- Discord Botのクライアント設定 ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True
intents.voice_states = True

class MyClient(discord.Client):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        try:
            self.db_pool = await asyncpg.create_pool(dsn=os.getenv('DATABASE_URL'))
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

# --- 定期パトロール機能 ---
@tasks.loop(hours=2)
async def periodic_role_check():
    pass

# --- Bumpリマインダー機能 ---
@tasks.loop(minutes=15)
async def check_bump_reminder():
    pass

# --- Bot起動時の処理（修正箇所） ---
@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user}')
    if not os.path.exists('data'):
        os.makedirs('data')
    
    # 起動時の自動チェックを一時的に無効化して、レートリミットを回避する
    logging.info("Cooldown mode activated. Background tasks will NOT be started automatically.")
    
    # if not periodic_role_check.is_running():
    #     logging.info("Waiting 30 seconds before starting periodic_role_check...")
    #     await asyncio.sleep(30)
    #     periodic_role_check.start()
    #     logging.info("-> periodic_role_check task started.")

    # if not check_bump_reminder.is_running():
    #     logging.info("Waiting another 30 seconds before starting check_bump_reminder...")
    #     await asyncio.sleep(30)
    #     check_bump_reminder.start()
    #     logging.info("-> check_bump_reminder task started.")
    
    logging.info("Bot is online in cooldown mode.")

# --- メッセージ受信時の処理 ---
@client.event
async def on_message(message):
    pass # 省略

# --- VC監視機能 ---
@client.event
async def on_voice_state_update(member, before, after):
    pass # 省略

# --- 新しいスラッシュコマンド ---
@client.tree.command(name="worktime", description="指定したメンバーの累計作業時間を表示します。")
async def worktime(interaction: discord.Interaction, member: discord.Member):
    pass # 省略

# --- メイン処理 ---
if __name__ == "__main__":
    RECONNECT_DELAY = 300 # 5分

    while True:
        try:
            if TOKEN:
                client.run(TOKEN)
            else:
                logging.error("DISCORD_BOT_TOKEN not found. Exiting.")
                break 
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logging.warning(f"We are being rate-limited. Waiting for {RECONNECT_DELAY} seconds before reconnecting.")
                time.sleep(RECONNECT_DELAY)
            else:
                raise
        except Exception as e:
            logging.error(f"An unexpected error occurred in the main run loop: {e}", exc_info=True)
            time.sleep(60)
