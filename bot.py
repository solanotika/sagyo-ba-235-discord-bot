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

# --- main関数 ---
def main():
    # --- 環境変数と定数の定義 ---
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    DATABASE_URL = os.getenv('DATABASE_URL')
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    GUILD_ID = os.getenv('GUILD_ID')
    TARGET_VC_IDS_STR = os.getenv('TARGET_VC_IDS', '')
    TARGET_VC_IDS = {int(id_str.strip()) for id_str in TARGET_VC_IDS_STR.split(',') if id_str.strip().isdigit()}
    BUMP_CHANNEL_ID = int(os.getenv('BUMP_CHANNEL_ID', 0))
    INTRO_CHANNEL_ID = int(os.getenv('INTRO_CHANNEL_ID', 0))
    INTRO_ROLE_ID = int(os.getenv('INTRO_ROLE_ID', 0))
    WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID', 0))
    WORK_LOG_CHANNEL_ID = int(os.getenv('WORK_LOG_CHANNEL_ID', 0))
    NOTICE_ROLE_ID = int(os.getenv('NOTICE_ROLE_ID', 0))
    ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))
    RECRUIT_CHANNEL_ID = int(os.getenv('RECRUIT_CHANNEL_ID', 0))
    ADMIN_ROLE_ID = int(os.getenv('ADMIN_ROLE_ID', 0))

    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)

    LAST_REMINDED_BUMP_ID_FILE = 'data/last_reminded_id.txt'
    active_sessions = {}

    # --- ヘルパー関数：時間フォーマット ---
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
    async def do_periodic_role_check():
        pass

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
        if not client.is_ready():
            return

        client.loop_counter += 1
        logging.info(f"--- Running unified background loop (Cycle: {client.loop_counter}) ---")
        await do_bump_reminder_check()
        if client.loop_counter % 8 == 0:
            await do_periodic_role_check()

    @unified_background_loop.before_loop
    async def before_unified_background_loop():
        await client.wait_until_ready()
        logging.info("Client is ready, unified background loop will start.")

    # --- 機能：Bot起動時の処理 ---
    @client.event
    async def on_ready():
        logging.info(f'Logged in as {client.user} (ID: {client.user.id})')
        logging.info(f'Connected to {len(client.guilds)} guilds.')
        if not os.path.exists('data'):
            os.makedirs('data')

    # --- 機能：メッセージ受信時の処理 ---
    @client.event
    async def on_message(message):
        if message.author == client.user: return
        if message.author.bot and message.author.id != 302050872383242240: return
        
        if client.user.mentioned_in(message) and GEMINI_API_KEY:
            logging.info("-> AI Handler: Mention detected.")
            if message.reference and message.reference.cached_message and message.reference.cached_message.author == client.user:
                logging.info("-> AI Handler: Ignored mention because it's a reply to myself.")
                return

            prompt = re.sub(r'<@!?(\d+)>', '', message.content).strip()
            logging.info(f"-> AI Handler: Cleaned prompt is: '{prompt}'")
            
            if not prompt:
                logging.info("-> AI Handler: Prompt is empty after cleaning. Exiting.")
                return

            async with message.channel.typing():
                try:
                    logging.info("-> AI Handler: Generating content with Gemini API...")
                    model = genai.GenerativeModel('gemini-1.5-flash')
                    response = await model.generate_content_async(prompt)
                    
                    if len(response.text) > 2000:
                        for i in range(0, len(response.text), 2000):
                            await message.reply(response.text[i:i+2000])
                    else:
                        await message.reply(response.text)
                    logging.info("-> AI Handler: Successfully sent reply.")

                except Exception as e:
                    logging.error(f"-> AI Handler: Gemini API Error: {e}")
                    await message.reply("ごめん、AIモデルとの通信でエラーが起きちゃった。")
            return

        if message.channel.id == BUMP_CHANNEL_ID and message.author.id == 302050872383242240:
            if "表示順をアップしたよ" in message.content:
                logging.info(f"Bump success message detected.")

    # --- 機能：リアクション追加によるロール付与 ---
    @client.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.channel_id != INTRO_CHANNEL_ID: return
        if str(payload.emoji) != '👌': return
        if not payload.member or payload.member.bot: return

        reactor = payload.member
        admin_role = reactor.guild.get_role(ADMIN_ROLE_ID)
        is_admin_user = (reactor.id == ADMIN_USER_ID)
        has_admin_role = (admin_role is not None and admin_role in reactor.roles)
        if not (is_admin_user or has_admin_role): return
        
        try:
            channel = client.get_channel(payload.channel_id)
            if not channel: return
            message = await channel.fetch_message(payload.message_id)
            author = message.author
            
            if not isinstance(author, discord.Member):
                 author = await message.guild.fetch_member(author.id)

            intro_role = message.guild.get_role(INTRO_ROLE_ID)

            if intro_role and intro_role not in author.roles:
                await author.add_roles(intro_role, reason=f"Admin approved.")
                welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
                if welcome_channel:
                    await welcome_channel.send(f"{author.mention}\n🎉{author.display_name}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！")
        except Exception as e:
            logging.error(f"Error in on_raw_reaction_add: {e}", exc_info=True)

    # --- 機能：VC状態更新時の処理 ---
    @client.event
    async def on_voice_state_update(member, before, after):
        if member.bot: return
        now = datetime.now(timezone.utc)
        
        if after.channel and after.channel.id in TARGET_VC_IDS and (not before.channel or before.channel.id not in TARGET_VC_IDS):
            active_sessions[member.id] = now
        elif before.channel and before.channel.id in TARGET_VC_IDS and (not after.channel or after.channel.id not in TARGET_VC_IDS):
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

    # --- スラッシュコマンド群 ---
    @client.tree.command(name="worktime", description="指定したメンバーの累計作業時間を表示します。")
    async def worktime(interaction: discord.Interaction, member: discord.Member):
        if not client.db_pool: return await interaction.response.send_message("DB未接続です。", ephemeral=True)
        await interaction.response.defer()
        total_seconds = 0
        if client.db_pool:
            async with client.db_pool.acquire() as connection:
                record = await connection.fetchrow('SELECT total_seconds FROM work_logs WHERE user_id = $1', member.id)
                if record:
                    total_seconds = record['total_seconds']
        if member.id in active_sessions:
            join_time = active_sessions[member.id]
            total_seconds += (datetime.now(timezone.utc) - join_time).total_seconds()
        await interaction.followup.send(f"{member.display_name} さんの累計作業時間は **{format_duration(total_seconds)}** です。")

    @client.tree.command(name="worktime_ranking", description="累計作業時間のトップ10ランキングを表示します。")
    async def worktime_ranking(interaction: discord.Interaction):
        if not client.db_pool: return await interaction.response.send_message("DB未接続です。", ephemeral=True)
        await interaction.response.defer()
        try:
            async with client.db_pool.acquire() as connection:
                query = "SELECT user_id, total_seconds FROM work_logs WHERE total_seconds > 0 ORDER BY total_seconds DESC LIMIT 10;"
                records = await connection.fetch(query)
            if not records: return await interaction.followup.send("まだ誰も作業記録がありません。")

            embed = discord.Embed(title="🏆 作業時間ランキング TOP10", color=discord.Color.gold())
            rank_emojis = ["🥇", "🥈", "🥉"]
            
            for i, record in enumerate(records):
                if interaction.guild:
                    member = interaction.guild.get_member(record['user_id'])
                    user_name = member.display_name if member else f"ID: {record['user_id']} (元メンバー)"
                else:
                    user_name = f"ID: {record['user_id']}"
                rank = rank_emojis[i] if i < 3 else f"**{i+1}位**"
                embed.add_field(name=f"{rank}：{user_name}", value=f"```{format_duration(record['total_seconds'])}```", inline=False)
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logging.error(f"Error in worktime_ranking: {e}", exc_info=True)
            await interaction.followup.send("ランキングの取得中にエラーが発生しました。")

    @client.tree.command(name="announce", description="指定したチャンネルにBotからお知らせを投稿します。(管理者限定)")
    @app_commands.checks.has_permissions(administrator=True)
    async def announce(interaction: discord.Interaction, channel: discord.TextChannel):
        await channel.send("★お知らせ用メッセージ入力欄★")
        await interaction.response.send_message(f"{channel.mention} にお知らせを投稿しました。", ephemeral=True)

    @announce.error
    async def announce_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("このコマンドは管理者しか使えないよ。", ephemeral=True)

    @client.tree.command(name="setup_recruit", description="作業募集用のパネルを設置します。(管理者限定)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_recruit(interaction: discord.Interaction):
        embed = discord.Embed(title="📢 作業仲間募集パネル", description="下のボタンを押すと、今いるボイスチャンネルへの招待リンク付きで募集が投稿されるよ！", color=discord.Color.green())
        await interaction.channel.send(embed=embed, view=RecruitmentView())
        await interaction.response.send_message("募集パネルを設置しました。", ephemeral=True)

    @setup_recruit.error
    async def setup_recruit_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message("このコマンドは管理者しか使えないよ。", ephemeral=True)

    # Botの実行
    if TOKEN:
        client.run(TOKEN, reconnect=True)
    else:
        logging.error("TOKEN not found.")

# --- メイン実行ブロック ---
if __name__ == "__main__":
    while True:
        try:
            main()
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logging.warning(f"Rate-limited. Waiting 300s.")
                time.sleep(300)
            else:
                logging.error(f"Unhandled HTTP exception: {e}", exc_info=True)
                time.sleep(60)
        except Exception as e:
            logging.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(60)
