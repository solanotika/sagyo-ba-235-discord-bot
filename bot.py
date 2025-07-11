import discord
from discord.ext import tasks
import os
import json
from datetime import datetime, timedelta, timezone
import logging

# --- ロギング設定 ---
# Renderのログ画面で見やすくするため
logging.basicConfig(level=logging.INFO)

# --- 環境変数からIDを取得 ---
# python-dotenvはローカルテスト用。Renderでは環境変数パネルを使う。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass # Render環境では不要

TOKEN = os.getenv('DISCORD_BOT_TOKEN')
# IDは数値として扱う
BUMP_CHANNEL_ID = int(os.getenv('BUMP_CHANNEL_ID'))
BUMP_LOG_CHANNEL_ID = int(os.getenv('BUMP_LOG_CHANNEL_ID'))
INTRO_CHANNEL_ID = int(os.getenv('INTRO_CHANNEL_ID'))
INTRO_ROLE_ID = int(os.getenv('INTRO_ROLE_ID'))
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID'))

# --- 状態を保存するファイル名 ---
# 注意: Renderの無料プランでは、デプロイのたびにファイルがリセットされる可能性がある
BUMP_COUNT_FILE = 'data/bump_counts.json'
LAST_BUMP_TIME_FILE = 'data/last_bump_time.txt'

# --- Discord Botのクライアント設定 ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)

# --- Bumpリマインダー機能 ---
@tasks.loop(minutes=10) # 10分ごとにチェック
async def check_bump_reminder():
    """2時間経過したことを通知するリマインダー機能"""
    try:
        # ファイルから最後のBump時刻を読み込む
        last_bump_time_str = ""
        if os.path.exists(LAST_BUMP_TIME_FILE):
            with open(LAST_BUMP_TIME_FILE, 'r') as f:
                last_bump_time_str = f.read().strip()
        
        if not last_bump_time_str:
            return # Bump記録がまだない

        last_bump_time = datetime.fromisoformat(last_bump_time_str)
        
        # 2時間経過していて、まだリマインドを送っていなければ通知
        if datetime.now(timezone.utc) >= last_bump_time + timedelta(hours=2):
            bump_channel = client.get_channel(BUMP_CHANNEL_ID)
            # リマインド済みかを簡易的にチェック（ここでは毎回送るのを避けるため、最後のbump時刻を更新しない）
            # より正確にするには、最後にリマインドした時刻も別途保存する必要がある
            # ここではシンプルに、2時間経過後の最初のチェックで通知する想定
            await bump_channel.send("みんな、DISBOARDの **/bump** の時間だよ！\nサーバーの表示順を上げて、新しい仲間を増やそう！")
            # 2時間以上経ったらリマインドし続けないように、一度ファイルをリセット
            os.remove(LAST_BUMP_TIME_FILE) 
            logging.info("Sent a bump reminder.")

    except Exception as e:
        logging.error(f"Error in check_bump_reminder: {e}")

# --- Bot起動時の処理 ---
@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user}')
    # dataディレクトリがなければ作成
    if not os.path.exists('data'):
        os.makedirs('data')
    # 定期リマインダータスクを開始
    check_bump_reminder.start()

# --- メッセージ受信時の処理（イベント駆動の心臓部） ---
@client.event
async def on_message(message):
    # 自分自身やBotのメッセージは無視
    if message.author == client.user or message.author.bot:
        return

    # --- ③,④ 自己紹介チャンネルへの投稿を即時検知 ---
    if message.channel.id == INTRO_CHANNEL_ID:
        author_member = message.guild.get_member(message.author.id)
        intro_role = message.guild.get_role(INTRO_ROLE_ID)

        if intro_role not in author_member.roles:
            try:
                await author_member.add_roles(intro_role, reason="自己紹介の投稿")
                welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
                await welcome_channel.send(f"🎉{author_member.mention}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！")
                logging.info(f"Assigned intro role to {author_member.display_name}.")
            except Exception as e:
                logging.error(f"Failed to assign role or send welcome message: {e}")

    # --- ①,② Bump成功メッセージを即時検知 ---
    if message.channel.id == BUMP_CHANNEL_ID and message.author.id == 302050872383242240:
        if "表示順をアップしたよ" in message.content and message.interaction:
            user = message.interaction.user
            logging.info(f"Bump detected by {user.display_name}.")
            
            # Bump時刻をファイルに記録 (リマインダー用)
            with open(LAST_BUMP_TIME_FILE, 'w') as f:
                f.write(str(message.created_at.isoformat()))
            
            # Bump回数を記録
            counts = {}
            if os.path.exists(BUMP_COUNT_FILE):
                with open(BUMP_COUNT_FILE, 'r') as f:
                    try:
                        counts = json.load(f)
                    except json.JSONDecodeError:
                        pass # ファイルが空なら何もしない
            
            user_id_str = str(user.id)
            counts[user_id_str] = counts.get(user_id_str, 0) + 1
            
            with open(BUMP_COUNT_FILE, 'w') as f:
                json.dump(counts, f, indent=2)

            # 監査ログに出力
            log_channel = client.get_channel(BUMP_LOG_CHANNEL_ID)
            guild = message.guild
            report_lines = ["📈 **Bump実行回数レポート** 📈"]
            sorted_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)

            for uid, count in sorted_counts:
                member = guild.get_member(int(uid))
                user_name = member.display_name if member else f"ID: {uid}"
                report_lines.append(f"・{user_name}: {count}回")
            
            await log_channel.send("\n".join(report_lines))

# --- メイン処理 ---
if __name__ == "__main__":
    if TOKEN:
        client.run(TOKEN)
    else:
        logging.error("DISCORD_BOT_TOKEN not found. Make sure it is set.")
