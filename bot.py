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
@tasks.loop(hours=2)
async def periodic_role_check():
    logging.info("--- Running periodic role check ---")
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
                    await welcome_channel.send(f"🎉{author_member.mention}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！")
    except Exception as e:
        logging.error(f"Error in periodic_role_check: {e}")
    logging.info("--- Periodic role check finished ---")


# --- Bumpリマインダー機能 ---
@tasks.loop(minutes=15)
async def check_bump_reminder():
    logging.info("--- Running bump reminder check (DISBOARD author specific) ---")
    try:
        bump_channel = client.get_channel(BUMP_CHANNEL_ID)
        if not bump_channel: return

        last_message_in_channel = None
        async for message in bump_channel.history(limit=1):
            last_message_in_channel = message
        
        if not last_message_in_channel: return
            
        if last_message_in_channel.author == client.user:
            return

        last_disboard_message = None
        disboard_bot_id = 302050872383242240
        async for message in bump_channel.history(limit=100):
            if message.author.id == disboard_bot_id:
                last_disboard_message = message
                break

        if not last_disboard_message: return

        two_hours_after_disboard_message = last_disboard_message.created_at + timedelta(hours=2)
        if datetime.now(timezone.utc) >= two_hours_after_disboard_message:
            await bump_channel.send("みんな、DISBOARDの **/bump** の時間だよ！\nサーバーの表示順を上げて、新しい仲間を増やそう！")

    except discord.errors.Forbidden:
        logging.error("Missing permissions to read message history in bump channel.")
    except Exception as e:
        logging.error(f"Error in check_bump_reminder: {e}")
    logging.info("--- Bump reminder check finished ---")

# --- Bot起動時の処理 ---
@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user}')
    if not os.path.exists('data'):
        os.makedirs('data')
    if not check_bump_reminder.is_running():
        check_bump_reminder.start()
    if not periodic_role_check.is_running():
        periodic_role_check.start()

# --- メッセージ受信時の処理 ---
@client.event
async def on_message(message):
    if message.author == client.user: return
    if message.author.bot and message.author.id != 302050872383242240: return

    # 自己紹介チャンネルの処理
    if message.channel.id == INTRO_CHANNEL_ID and not message.author.bot:
        author_member = message.guild.get_member(message.author.id)
        intro_role = message.guild.get_role(INTRO_ROLE_ID)
        if intro_role and author_member and intro_role not in author_member.roles:
            await author_member.add_roles(intro_role, reason="自己紹介の投稿")
            welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
            if welcome_channel:
                await welcome_channel.send(f"🎉{author_member.mention}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！")

    # --- Bump成功メッセージの処理（ロジック最終版） ---
    if message.channel.id == BUMP_CHANNEL_ID and message.author.id == 302050872383242240:
        # 返信元のメッセージ（〇〇さんが/bumpを使用しました）が存在するかどうかをチェック
        if message.reference and message.reference.message_id:
            try:
                # 返信元のメッセージを実際に取得しにいく
                referenced_message = await message.channel.fetch_message(message.reference.message_id)
                # そのメッセージの投稿者こそが、Bumpしたユーザーだ！
                user = referenced_message.author
                
                logging.info(f"Bump detected by {user.display_name} via reply reference.")

                # --- 記録・報告処理 ---
                counts = {}
                if os.path.exists(BUMP_COUNT_FILE):
                    with open(BUMP_COUNT_FILE, 'r') as f:
                        try: counts = json.load(f)
                        except json.JSONDecodeError: pass
                
                user_id_str = str(user.id)
                counts[user_id_str] = counts.get(user_id_str, 0) + 1
                
                with open(BUMP_COUNT_FILE, 'w') as f:
                    json.dump(counts, f, indent=2)

                log_channel = client.get_channel(BUMP_LOG_CHANNEL_ID)
                if log_channel:
                    guild = message.guild
                    report_lines = ["📈 **Bump実行回数レポート** 📈"]
                    sorted_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)

                    for uid, count in sorted_counts:
                        member = guild.get_member(int(uid))
                        user_name = member.display_name if member else f"ID: {uid}"
                        report_lines.append(f"・{user_name}: {count}回")
                    
                    await log_channel.send("\n".join(report_lines))

            except discord.NotFound:
                logging.warning("Could not find the referenced message. It might have been deleted.")
            except Exception as e:
                logging.error(f"An error occurred while tracking bump via reply: {e}")

# --- メイン処理 ---
if __name__ == "__main__":
    if TOKEN:
        client.run(TOKEN)
    else:
        logging.error("DISCORD_BOT_TOKEN not found. Make sure it is set.")
