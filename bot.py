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

# --- main関数を定義 ---
def main():
    # --- 環境変数からIDを取得 ---
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    DATABASE_URL = os.getenv('DATABASE_URL')
    GUILD_ID = os.getenv('GUILD_ID')
    EXCLUDE_VC_IDS_STR = os.getenv('EXCLUDE_VC_IDS', '')
    EXCLUDE_VC_IDS = {int(id_str.strip()) for id_str in EXCLUDE_VC_IDS_STR.split(',') if id_str.strip().isdigit()}
    BUMP_CHANNEL_ID = int(os.getenv('BUMP_CHANNEL_ID', 0))
    INTRO_CHANNEL_ID = int(os.getenv('INTRO_CHANNEL_ID', 0))
    INTRO_ROLE_ID = int(os.getenv('INTRO_ROLE_ID', 0))
    WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID', 0))
    WORK_LOG_CHANNEL_ID = int(os.getenv('WORK_LOG_CHANNEL_ID', 0))
    NOTICE_ROLE_ID = int(os.getenv('NOTICE_ROLE_ID', 0))
    ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))
    RECRUIT_CHANNEL_ID = int(os.getenv('RECRUIT_CHANNEL_ID', 0))
    ADMIN_ROLE_ID = int(os.getenv('ADMIN_ROLE_ID', 0))

    # --- グローバル変数 ---
    active_sessions = {}

    # --- ヘルパー関数 ---
    def format_duration(total_seconds):
        if total_seconds is None or total_seconds < 0:
            total_seconds = 0
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}時間 {int(minutes)}分 {int(seconds)}秒"

    # --- UI部品：永続的な募集ボタン ---
    class RecruitmentView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="作業仲間を募集！", style=discord.ButtonStyle.green, emoji="📢", custom_id="recruit_button")
        async def recruit_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            user = interaction.user
            if not isinstance(user, discord.Member): return

            if not user.voice or not user.voice.channel:
                await interaction.response.send_message("ボイスチャンネルに参加してからボタンを押してね。", ephemeral=True, delete_after=10)
                return

            try:
                voice_channel = user.voice.channel
                invite = await voice_channel.create_invite(max_age=7200, max_uses=0, reason=f"{user.display_name}による募集")
                recruit_channel = client.get_channel(RECRUIT_CHANNEL_ID)
                if not (recruit_channel and interaction.guild): return
                notice_role = interaction.guild.get_role(NOTICE_ROLE_ID)

                if notice_role:
                    message_text = f"{notice_role.mention}\n{user.display_name} さんが作業通話を募集しているよ！みんなで作業しよう！\n{invite.url}"
                    await recruit_channel.send(message_text)
                    await interaction.response.send_message("募集を投稿したよ！", ephemeral=True, delete_after=5)
            except Exception as e:
                logging.error(f"Failed to process recruitment button click: {e}")
                await interaction.response.send_message("エラーが発生しました。管理者に連絡してください。", ephemeral=True)

    # --- Botクライアントの定義 ---
    intents = discord.Intents.default()
    intents.voice_states = True
    intents.guilds = True
    intents.members = True
    intents.messages = True
    intents.message_content = True
    intents.reactions = True

    class MyClient(discord.Client):
        def __init__(self, *, intents: discord.Intents):
            super().__init__(intents=intents)
            self.tree = app_commands.CommandTree(self)
            self.db_pool = None
            self.loop_counter = 0

        async def setup_hook(self):
            self.add_view(RecruitmentView())
            
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
                else:
                    logging.warning("DATABASE_URL not found.")
            except Exception as e:
                self.db_pool = None
                logging.error(f"Failed to connect to the database: {e}")
            
            if GUILD_ID:
                guild_obj = discord.Object(id=int(GUILD_ID))
                self.tree.clear_commands(guild=guild_obj)
                self.tree.copy_global_to(guild=guild_obj)
                await self.tree.sync(guild=guild_obj)
                logging.info(f"Commands synced to guild {GUILD_ID}.")
            else:
                await self.tree.sync()
                logging.info("Commands synced globally.")

            if not unified_background_loop.is_running():
                unified_background_loop.start()

        async def close(self):
            if unified_background_loop.is_running():
                unified_background_loop.cancel()
            if self.db_pool:
                await self.db_pool.close()
            await super().close()

    client = MyClient(intents=intents)
    
    # --- バックグラウンド処理 ---
    # (省略)

    # --- イベントハンドラとコマンド ---
    @client.event
    async def on_ready():
        logging.info(f'Logged in as {client.user}')

    @client.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        # (省略)
        pass

    @client.event
    async def on_voice_state_update(member, before, after):
        if member.bot: return
        now = datetime.now(timezone.utc)

        # --- ここからが修正点 ---
        # before.channel が作業チャンネルか (除外リストにないか)
        is_before_work_vc = before.channel and before.channel.id not in EXCLUDE_VC_IDS
        # after.channel が作業チャンネルか (除外リストにないか)
        is_after_work_vc = after.channel and after.channel.id not in EXCLUDE_VC_IDS

        # セッション開始の条件：作業VCではなかった状態から、作業VCに入った
        if not is_before_work_vc and is_after_work_vc:
            active_sessions[member.id] = now
            logging.info(f"SESSION START: {member.display_name} in {after.channel.name}")

        # セッション終了の条件：作業VCにいた状態から、作業VCではない状態になった
        elif is_before_work_vc and not is_after_work_vc:
            if member.id in active_sessions:
                join_time = active_sessions.pop(member.id)
                duration = (now - join_time).total_seconds()
                total_seconds_after_update = 0

                if client.db_pool:
                    async with client.db_pool.acquire() as connection:
                        await connection.execute('''
                            INSERT INTO work_logs (user_id, total_seconds) VALUES ($1, $2)
                            ON CONFLICT (user_id) DO UPDATE
                            SET total_seconds = work_logs.total_seconds + $2
                        ''', member.id, duration)
                        record = await connection.fetchrow('SELECT total_seconds FROM work_logs WHERE user_id = $1', member.id)
                        if record:
                            total_seconds_after_update = record['total_seconds']
                
                log_channel = client.get_channel(WORK_LOG_CHANNEL_ID)
                if log_channel:
                    await log_channel.send(
                        f"{member.mention}\n"
                        f"お疲れ様、{member.display_name}！\n"
                        f"今回の作業時間は **{format_duration(duration)}** だったよ。\n"
                        f"累計作業時間は **{format_duration(total_seconds_after_update)}** だよ。"
                    )
                logging.info(f"SESSION END: {member.display_name}. Duration: {format_duration(duration)}")
        # --- ここまでが修正点 ---

    # (以降のコマンドとメイン実行ブロックは変更なし)
    # ...

    if TOKEN:
        client.run(TOKEN, reconnect=True)
    else:
        logging.error("TOKEN not found.")

if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            logging.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(60)
