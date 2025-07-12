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

# --- 時間をフォーマットするヘルパー関数 ---
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
        try:
            self.db_pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=5)
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

# --- バックグラウンド処理 ---
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

@tasks.loop(minutes=15)
async def unified_background_loop():
    if not client.is_ready() or not client.db_pool:
        logging.warning("Bot is not ready or DB is not connected. Skipping loop.")
        return

    client.loop_counter += 1
    logging.info(f"--- Running unified background loop (Cycle: {client.loop_counter}) ---")

    await do_bump_reminder_check()

    if client.loop_counter % 8 == 0:
        await do_periodic_role_check()

# --- Bot起動時の処理 ---
@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user}')
    if not os.path.exists('data'):
        os.makedirs('data')
    
    if client.db_pool and not unified_background_loop.is_running():
        unified_background_loop.start()
        logging.info("Unified background loop has been started.")

# --- イベントハンドラとコマンド ---
@client.event
async def on_message(message):
    if message.author == client.user: return
    if message.author.bot and message.author.id != 302050872383242240: return
    if message.channel.id == INTRO_CHANNEL_ID and not message.author.bot:
        author_member = message.guild.get_member(message.author.id)
        intro_role = message.guild.get_role(INTRO_ROLE_ID)
        if intro_role and author_member and intro_role not in author_member.roles:
            await author_member.add_roles(intro_role, reason="自己紹介の投稿")
            welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
            if welcome_channel:
                await welcome_channel.send(f"🎉{author_member.mention}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！")
    if message.channel.id == BUMP_CHANNEL_ID and message.author.id == 302050872383242240:
        if "表示順をアップしたよ" in message.content:
            user = None
            if message.reference and message.reference.message_id:
                try:
                    referenced_message = await message.channel.fetch_message(message.reference.message_id)
                    user = referenced_message.author
                except (discord.NotFound, discord.HTTPException): pass
            if not user and message.interaction:
                user = message.interaction.user
            if not user and message.embeds:
                for embed in message.embeds:
                    if embed.description:
                        match = re.search(r'<@!?(\d+)>', embed.description)
                        if match:
                            user_id = int(match.group(1))
                            try:
                                user = await client.fetch_user(user_id)
                                break
                            except discord.NotFound: pass
            if user:
                logging.info(f"Bump detected by {user.display_name}.")
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

@client.event
async def on_voice_state_update(member, before, after):
    if member.bot or not client.db_pool:
        return
    now = datetime.now(timezone.utc)
    if after.channel and after.channel.id in TARGET_VC_IDS and (not before.channel or before.channel.id not in TARGET_VC_IDS):
        active_sessions[member.id] = now
        logging.info(f"{member.display_name} joined target VC {after.channel.name}. Session started.")
    elif before.channel and before.channel.id in TARGET_VC_IDS and (not after.channel or after.channel.id not in TARGET_VC_IDS):
        if member.id in active_sessions:
            join_time = active_sessions.pop(member.id)
            duration = (now - join_time).total_seconds()
            
            total_seconds = 0 # 累計時間を初期化
            async with client.db_pool.acquire() as connection:
                # まず、今回の滞在時間をデータベースに記録・加算する
                await connection.execute('''
                    INSERT INTO work_logs (user_id, total_seconds) VALUES ($1, $2)
                    ON CONFLICT (user_id) DO UPDATE
                    SET total_seconds = work_logs.total_seconds + $2
                ''', member.id, duration)
                
                # 次に、更新された後の累計時間をデータベースから取得する
                record = await connection.fetchrow('SELECT total_seconds FROM work_logs WHERE user_id = $1', member.id)
                if record:
                    total_seconds = record['total_seconds']

            # 今回の時間と、累計時間の両方をフォーマットする
            formatted_duration = format_duration(duration)
            formatted_total_duration = format_duration(total_seconds)
            logging.info(f"{member.display_name} left target VC {before.channel.name}. Session duration: {formatted_duration}")
            
            log_channel = client.get_channel(WORK_LOG_CHANNEL_ID)
            if log_channel:
                # 通知メッセージに「累計作業時間」の行を追加する
                await log_channel.send(f"お疲れ様、{member.mention}！\n今回の作業時間: **{formatted_duration}**\n累計作業時間: **{formatted_total_duration}**")

@client.tree.command(name="worktime", description="指定したメンバーの累計作業時間を表示します。")
async def worktime(interaction: discord.Interaction, member: discord.Member):
    if not client.db_pool:
        await interaction.response.send_message("データベースに接続できていません。管理者に連絡してください。", ephemeral=True)
        return
    await interaction.response.defer()
    total_seconds = 0
    async with client.db_pool.acquire() as connection:
        record = await connection.fetchrow('SELECT total_seconds FROM work_logs WHERE user_id = $1', member.id)
        if record:
            total_seconds = record['total_seconds']
    if member.id in active_sessions:
        join_time = active_sessions[member.id]
        current_session_duration = (datetime.now(timezone.utc) - join_time).total_seconds()
        total_seconds += current_session_duration
    formatted_time = format_duration(total_seconds)
    await interaction.followup.send(f"{member.mention} さんの累計作業時間は **{formatted_time}** です。")

@client.tree.command(name="announce", description="指定したチャンネルにBotからお知らせを投稿します。(管理者限定)")
@app_commands.describe(channel="投稿先のチャンネル")
@app_commands.checks.has_permissions(administrator=True)
async def announce(interaction: discord.Interaction, channel: discord.TextChannel):
    announcement_text = """
お知らせ用メッセージ入力欄
"""
    try:
        await channel.send(announcement_text)
        await interaction.response.send_message(f"{channel.mention} にお知らせを投稿したよ。", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"エラー: {channel.mention} にメッセージを投稿する権限がないみたい。", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"エラーが発生しました: {e}", ephemeral=True)

@announce.error
async def announce_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("このコマンドは管理者しか使えないよ。", ephemeral=True)
    else:
        await interaction.response.send_message(f"コマンドの実行中にエラーが発生しました: {error}", ephemeral=True)

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