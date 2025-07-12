import discord
from discord.ext import tasks
import os
import json
from datetime import datetime, timedelta, timezone
import logging

# --- ロギング設定 ---
logging.basicConfig(level=logging.INFO)

# --- 環境変数からIDを取得 ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv('DISCORD_BOT_TOKEN')
BUMP_CHANNEL_ID = int(os.getenv('BUMP_CHANNEL_ID'))
BUMP_LOG_CHANNEL_ID = int(os.getenv('BUMP_LOG_CHANNEL_ID'))
INTRO_CHANNEL_ID = int(os.getenv('INTRO_CHANNEL_ID'))
INTRO_ROLE_ID = int(os.getenv('INTRO_ROLE_ID'))
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID'))

# --- 状態を保存するファイル名 ---
BUMP_COUNT_FILE = 'data/bump_counts.json'
LAST_BUMP_TIME_FILE = 'data/last_bump_time.txt'

# --- Discord Botのクライアント設定 ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)

# --- ここからが新しい機能 ---

@tasks.loop(hours=1) # 1時間に1回実行
async def periodic_role_check():
    """過去の自己紹介を遡ってロールが付与されているかチェックする"""
    logging.info("--- Running periodic role check ---")
    try:
        intro_channel = client.get_channel(INTRO_CHANNEL_ID)
        if not intro_channel:
            logging.warning("Intro channel not found for periodic check.")
            return

        guild = intro_channel.guild
        intro_role = guild.get_role(INTRO_ROLE_ID)
        if not intro_role:
            logging.warning("Intro role not found for periodic check.")
            return

        # 直近24時間分のメッセージをチェック
        since = datetime.now(timezone.utc) - timedelta(days=1)
        async for message in intro_channel.history(limit=200, after=since):
            # Botや、すでにロールを持っている人はスキップ
            if message.author.bot or (isinstance(message.author, discord.Member) and intro_role in message.author.roles):
                continue
            
            author_member = guild.get_member(message.author.id)
            if author_member:
                logging.info(f"Found user without role in history: {author_member.display_name}. Assigning role...")
                await author_member.add_roles(intro_role, reason="自己紹介の履歴をチェックして付与")
                # こちらではウェルカムメッセージは送らない（即時反応と役割を分けるため）

    except Exception as e:
        logging.error(f"Error in periodic_role_check: {e}")
    logging.info("--- Periodic role check finished ---")

# (check_bump_reminder関数は変更なし)
@tasks.loop(minutes=10)
async def check_bump_reminder():
    # ... (以前のコードと同じ)
    pass 

# --- Bot起動時の処理 ---
@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user}')
    if not os.path.exists('data'):
        os.makedirs('data')
    
    # 2つの定期処理タスクを開始する
    check_bump_reminder.start()
    periodic_role_check.start()

# --- メッセージ受信時の処理 ---
@client.event
async def on_message(message):
    # (このon_message関数は変更なし)
    # ... (以前のコードと同じ)
    pass

# --- メイン処理 ---
if __name__ == "__main__":
    if TOKEN:
        client.run(TOKEN)
    else:
        logging.error("DISCORD_BOT_TOKEN not found. Make sure it is set.")
