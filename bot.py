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

# --- Discord Botのクライアント設定 ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)


# --- 定期パトロール機能 ---
@tasks.loop(hours=1)
async def periodic_role_check():
    # (この関数は変更なし)
    logging.info("--- Running periodic role check ---")
    # ... (内容は変更なし)


# --- ここからが変更点 ---

@tasks.loop(minutes=10) # 10分ごとにチェック
async def check_bump_reminder():
    """DISBOARD Botの最後の発言から2時間経過していたら通知する"""
    try:
        logging.info("--- Running bump reminder check (DISBOARD author specific) ---")
        bump_channel = client.get_channel(BUMP_CHANNEL_ID)
        if not bump_channel:
            logging.warning("Bump channel not found for reminder check.")
            return

        # 最初に、チャンネルの一番最後のメッセージが自分自身の通知かチェック（連投防止）
        last_message_in_channel = await bump_channel.fetch_message(bump_channel.last_message_id) if bump_channel.last_message_id else None
        if last_message_in_channel and last_message_in_channel.author == client.user:
            logging.info("Last message was our own reminder. Skipping.")
            return

        # チャンネルの履歴を遡って、DISBOARD Botの最後の発言を探す
        last_disboard_message = None
        disboard_bot_id = 302050872383242240
        async for message in bump_channel.history(limit=100): # 直近100件のメッセージをチェック
            if message.author.id == disboard_bot_id:
                last_disboard_message = message
                break # 一番新しいものを見つけたらループを抜ける

        if not last_disboard_message:
            logging.info("No DISBOARD message found in recent history. Skipping reminder.")
            return

        # DISBOARD Botの最後の発言から2時間経過したかチェック
        two_hours_after_disboard_message = last_disboard_message.created_at + timedelta(hours=2)
        if datetime.now(timezone.utc) >= two_hours_after_disboard_message:
            logging.info("2 hours have passed since the last DISBOARD message. Sending reminder.")
            await bump_channel.send("みんな、DISBOARDの **/bump** の時間だよ！\nサーバーの表示順を上げて、新しい仲間を増やそう！")

    except Exception as e:
        logging.error(f"Error in check_bump_reminder: {e}")
    finally:
        logging.info("--- Bump reminder check finished ---")

# --- Bot起動時の処理 ---
@client.event
async def on_ready():
    # (この関数は変更なし)
    logging.info(f'Logged in as {client.user}')
    # ... (内容は変更なし)

# --- メッセージ受信時の処理 ---
@client.event
async def on_message(message):
    # (この関数は変更なし)
    # ... (内容は変更なし)

# --- メイン処理 ---
if __name__ == "__main__":
    if TOKEN:
        client.run(TOKEN)
    else:
        logging.error("DISCORD_BOT_TOKEN not found. Make sure it is set.")
