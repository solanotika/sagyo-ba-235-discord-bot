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
import asyncpg

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
BUMP_CHANNEL_ID = int(os.getenv('BUMP_CHANNEL_ID', 0))
BUMP_LOG_CHANNEL_ID = int(os.getenv('BUMP_LOG_CHANNEL_ID', 0))
INTRO_CHANNEL_ID = int(os.getenv('INTRO_CHANNEL_ID', 0))
INTRO_ROLE_ID = int(os.getenv('INTRO_ROLE_ID', 0))
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID', 0))
WORK_LOG_CHANNEL_ID = int(os.getenv('WORK_LOG_CHANNEL_ID', 0))
AUTO_NOTICE_VC_ID = int(os.getenv('AUTO_NOTICE_VC_ID', 0))
NOTICE_ROLE_ID = int(os.getenv('NOTICE_ROLE_ID', 0))
RECRUIT_CHANNEL_ID = 1389386628497412138

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
        self.loop_counter = 0

    async def setup_hook(self):
        # データベース接続とテーブル初期化
        try:
            if DATABASE_URL:
                self.db_pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10)
                logging.info("Successfully connected to the database.")
                async with self.db_pool.acquire() as connection:
                    await connection.execute('''
                        CREATE TABLE IF NOT EXISTS work_logs (
                            user_id BIGINT PRIMARY KEY,
                            total_seconds DOUBLE PRECISION NOT NULL DEFAULT 0.0
                        )
                    ''')
                    logging.info("Database table 'work_logs' initialized.")
            else:
                logging.warning("DATABASE_URL not found. Database features will be disabled.")
        except Exception as e:
            self.db_pool = None
            logging.error(f"Failed to connect to the database during setup: {e}")

        # スラッシュコマンドの同期
        await self.tree.sync()
        logging.info("Command tree synced.")

        # バックグラウンドタスクの開始
        if not unified_background_loop.is_running():
            unified_background_loop.start()
            logging.info("Unified background loop has been started.")

    async def close(self):
        if unified_background_loop.is_running():
            unified_background_loop.cancel()
        if self.db_pool:
            await self.db_pool.close()
        await super().close()

client = MyClient(intents=intents)

# --- バックグラウンド処理のヘルパー関数 ---
async def do_periodic_role_check():
    try:
        intro_channel = client.get_channel(INTRO_CHANNEL_ID)
        if not intro_channel: return
        guild = intro_channel.guild
        intro_role = guild.get_role(INTRO_ROLE_ID)
        if not intro_role: return

        since = datetime.now(timezone.utc) - timedelta(days=1)
        async for message in intro_channel.history(limit=200, after=since):
            if message.author.bot or (isinstance(message.author, discord.Member) and intro_role in message.author.roles):
                continue
            
            author_member = guild.get_member(message.author.id)
            if author_member:
                await author_member.add_roles(intro_role, reason="自己紹介の履歴をチェックして付与")
                welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
                if welcome_channel:
                    await welcome_channel.send(f"🎉{author_member.mention}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！ (履歴チェックより)")
    except Exception as e:
        logging.error(f"Error in periodic_role_check: {e}")

async def do_bump_reminder_check():
    try:
        bump_channel = client.get_channel(BUMP_CHANNEL_ID)
        if not bump_channel: return

        last_disboard_message = None
        disboard_bot_id = 302050872383242240
        async for message in bump_channel.history(limit=100):
            if message.author.id == disboard_bot_id:
                last_disboard_message = message
                break

        if not last_disboard_message: return

        last_reminded_id = 0
        if os.path.exists(LAST_REMINDED_BUMP_ID_FILE):
            with open(LAST_REMINDED_BUMP_ID_FILE, 'r') as f:
                content = f.read().strip()
                if content.isdigit():
                    last_reminded_id = int(content)

        if last_disboard_message.id == last_reminded_id: return

        two_hours_after_disboard_message = last_disboard_message.created_at + timedelta(hours=2)
        if datetime.now(timezone.utc) >= two_hours_after_disboard_message:
            await bump_channel.send("みんな、DISBOARDの **/bump** の時間だよ！\nサーバーの表示順を上げて、新しい仲間を増やそう！")
            with open(LAST_REMINDED_BUMP_ID_FILE, 'w') as f:
                f.write(str(last_disboard_message.id))
    except Exception as e:
        logging.error(f"Error in check_bump_reminder: {e}", exc_info=True)

# --- 統合された単一バックグラウンドループ ---
@tasks.loop(minutes=15)
async def unified_background_loop():
    if not client.is_ready() or not client.db_pool:
        return

    client.loop_counter += 1
    logging.info(f"--- Running unified background loop (Cycle: {client.loop_counter}) ---")

    await do_bump_reminder_check()

    if client.loop_counter % 8 == 0:
        await do_periodic_role_check()

@unified_background_loop.before_loop
async def before_unified_background_loop():
    await client.wait_until_ready()
    logging.info("Client is ready, unified background loop is starting.")


# --- Bot起動時の処理 ---
@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user} (ID: {client.user.id})')
    logging.info(f'Connected to {len(client.guilds)} guilds.')
    if not os.path.exists('data'):
        os.makedirs('data')

# --- イベントハンドラとコマンド ---
@client.event
async def on_message(message):
    # ... (内容は変更なし)
    pass

@client.event
async def on_voice_state_update(member, before, after):
    # ... (内容は変更なし)
    pass

@client.tree.command(name="worktime", description="指定したメンバーの累計作業時間を表示します。")
async def worktime(interaction: discord.Interaction, member: discord.Member):
    # ... (内容は変更なし)
    pass

@client.tree.command(name="announce", description="指定したチャンネルにBotからお知らせを投稿します。(管理者限定)")
@app_commands.describe(channel="投稿先のチャンネル")
@app_commands.checks.has_permissions(administrator=True)
async def announce(interaction: discord.Interaction, channel: discord.TextChannel):
    # ... (内容は変更なし)
    pass

@announce.error
async def announce_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # ... (内容は変更なし)
    pass

# --- メイン処理 ---
if __name__ == "__main__":
    RECONNECT_DELAY = 300
    while True:
        try:
            if TOKEN:
                client.run(TOKEN, reconnect=True)
            else:
                logging.error("Required environment variables (TOKEN) not found. Exiting.")
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
