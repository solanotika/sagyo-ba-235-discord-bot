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
import google.generativeai as genai

# --- ロギング設定 ---
logging.basicConfig(level=logging.INFO)

# --- main関数を定義 ---
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

    gemini_model = None
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            gemini_model = genai.GenerativeModel('gemini-1.5-flash')
        except Exception as e:
            logging.error(f"Failed to configure Gemini model: {e}")

    LAST_REMINDED_BUMP_ID_FILE = 'data/last_reminded_id.txt'
    active_sessions = {}

    def format_duration(total_seconds):
        if total_seconds is None or total_seconds < 0: total_seconds = 0
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}時間 {int(minutes)}分 {int(seconds)}秒"

    class RecruitmentView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="作業仲間を募集！", style=discord.ButtonStyle.green, emoji="📢", custom_id="recruit_button")
        async def recruit_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
            user = interaction.user
            if not isinstance(user, discord.Member): return
            if not user.voice or not user.voice.channel:
                return await interaction.response.send_message("ボイスチャンネルに参加してからボタンを押してね。", ephemeral=True, delete_after=10)
            try:
                invite = await user.voice.channel.create_invite(max_age=7200, max_uses=0, reason=f"{user.display_name}による募集")
                recruit_channel = client.get_channel(RECRUIT_CHANNEL_ID)
                if not (recruit_channel and interaction.guild): return
                notice_role = interaction.guild.get_role(NOTICE_ROLE_ID)
                if notice_role:
                    await recruit_channel.send(f"{notice_role.mention}\n{user.display_name} さんが作業通話を募集しているよ！みんなで作業しよう！\n{invite.url}")
                    await interaction.response.send_message("募集を投稿したよ！", ephemeral=True, delete_after=5)
            except Exception as e:
                logging.error(f"Failed to process recruitment button click: {e}")

    intents = discord.Intents.default()
    intents.voice_states = True; intents.guilds = True; intents.members = True
    intents.messages = True; intents.message_content = True; intents.reactions = True

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
                    async with self.db_pool.acquire() as connection:
                        await connection.execute('''
                            CREATE TABLE IF NOT EXISTS work_sessions (
                                session_id SERIAL PRIMARY KEY,
                                user_id BIGINT NOT NULL,
                                start_time TIMESTAMPTZ NOT NULL,
                                end_time TIMESTAMPTZ NOT NULL
                            )
                        ''')
                else:
                    logging.warning("DATABASE_URL not found.")
            except Exception as e:
                logging.error(f"DB Error: {e}")
            
            if GUILD_ID:
                guild_obj = discord.Object(id=int(GUILD_ID))
                self.tree.clear_commands(guild=guild_obj)
                self.tree.copy_global_to(guild=guild_obj)
                await self.tree.sync(guild=guild_obj)
            else:
                await self.tree.sync()
            
            self.unified_background_loop.start()

        async def close(self):
            if self.unified_background_loop.is_running(): self.unified_background_loop.cancel()
            if self.db_pool: await self.db_pool.close()
            await super().close()

        async def _do_periodic_role_check(self):
            pass

        async def _do_bump_reminder_check(self):
            try:
                bump_channel = self.get_channel(BUMP_CHANNEL_ID)
                if not bump_channel: return
                disboard_bot_id = 302050872383242240
                last_disboard_message = None
                async for message in bump_channel.history(limit=100):
                    if message.author.id == disboard_bot_id:
                        last_disboard_message = message
                        break
                
                if not last_disboard_message: return

                last_reminded_id = 0
                if os.path.exists(LAST_REMINDED_BUMP_ID_FILE):
                    with open(LAST_REMINDED_BUMP_ID_FILE, 'r') as f:
                        content = f.read().strip()
                        if content.isdigit(): last_reminded_id = int(content)
                
                if last_disboard_message.id == last_reminded_id: return

                if datetime.now(timezone.utc) >= last_disboard_message.created_at + timedelta(hours=2):
                    await bump_channel.send("みんな、DISBOARDの **/bump** の時間だよ！\nサーバーの表示順を上げて、新しい仲間を増やそう！")
                    with open(LAST_REMINDED_BUMP_ID_FILE, 'w') as f: f.write(str(last_disboard_message.id))
            except Exception as e:
                logging.error(f"Error in _do_bump_reminder_check: {e}", exc_info=True)

        @tasks.loop(minutes=15)
        async def unified_background_loop(self):
            if not self.is_ready(): return
            self.loop_counter += 1
            await self._do_bump_reminder_check()
            if self.loop_counter % 8 == 0: await self._do_periodic_role_check()

        @unified_background_loop.before_loop
        async def before_unified_background_loop(self):
            await self.wait_until_ready()

    client = MyClient(intents=intents)

    @client.event
    async def on_ready():
        logging.info(f'Logged in as {client.user}')
        if not os.path.exists('data'): os.makedirs('data')

    @client.event
    async def on_message(message):
        if message.author == client.user: return
        disboard_bot_id = 302050872383242240
        if message.author.bot and message.author.id != disboard_bot_id: return
        
        if client.user.mentioned_in(message) and gemini_model:
            if message.reference and message.reference.cached_message and message.reference.cached_message.author == client.user: return
            async with message.channel.typing():
                prompt = re.sub(r'<@!?\d+>', '', message.content).strip()
                if not prompt: return
                try:
                    response = await gemini_model.generate_content_async(prompt)
                    await message.reply(response.text)
                except Exception as e:
                    logging.error(f"Gemini API Error: {e}")
                    await message.reply("ごめん、AIモデルとの通信でエラーが起きちゃった。")
            return

        if message.channel.id == BUMP_CHANNEL_ID and message.author.id == disboard_bot_id:
            if "表示順をアップしたよ" in message.content: logging.info(f"Bump success message detected.")

    @client.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.channel_id != INTRO_CHANNEL_ID or str(payload.emoji) != '👌' or not payload.member or payload.member.bot: return
        reactor = payload.member
        admin_role = reactor.guild.get_role(ADMIN_ROLE_ID)
        if not (reactor.id == ADMIN_USER_ID or (admin_role and admin_role in reactor.roles)): return
        try:
            channel = client.get_channel(payload.channel_id)
            if not channel: return
            message = await channel.fetch_message(payload.message_id)
            author = message.author
            if not isinstance(author, discord.Member): author = await message.guild.fetch_member(author.id)
            intro_role = message.guild.get_role(INTRO_ROLE_ID)
            if intro_role and intro_role not in author.roles:
                await author.add_roles(intro_role, reason=f"Admin approved.")
                welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
                if welcome_channel: await welcome_channel.send(f"{author.mention}\n🎉{author.display_name}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！")
        except Exception as e:
            logging.error(f"Error in on_raw_reaction_add: {e}", exc_info=True)

    @client.event
    async def on_voice_state_update(member, before, after):
        if member.bot: return
        now = datetime.now(timezone.utc)
        is_before_work_vc = before.channel and before.channel.id not in EXCLUDE_VC_IDS
        is_after_work_vc = after.channel and after.channel.id not in EXCLUDE_VC_IDS
        
        if not is_before_work_vc and is_after_work_vc:
            active_sessions[member.id] = now
        elif is_before_work_vc and not is_after_work_vc:
            if member.id in active_sessions:
                join_time = active_sessions.pop(member.id)
                duration = (now - join_time).total_seconds()
                
                if client.db_pool:
                    async with client.db_pool.acquire() as connection:
                        await connection.execute(
                            'INSERT INTO work_sessions (user_id, start_time, end_time) VALUES ($1, $2, $3)',
                            member.id, join_time, now
                        )
                
                log_channel = client.get_channel(WORK_LOG_CHANNEL_ID)
                if log_channel:
                    await log_channel.send(f"お疲れ様、{member.display_name}！今回の作業時間は **{format_duration(duration)}** だったよ。")

    @client.tree.command(name="worktime", description="指定したメンバーの作業時間を表示します。")
    async def worktime(interaction: discord.Interaction, member: discord.Member):
        if not client.db_pool: return await interaction.response.send_message("DB未接続です。", ephemeral=True)
        await interaction.response.defer()
        
        now = datetime.now(timezone.utc)
        start_of_week = now - timedelta(days=now.weekday())
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        try:
            async with client.db_pool.acquire() as connection:
                query = """
                    SELECT
                        (SELECT SUM(EXTRACT(EPOCH FROM (end_time - start_time))) FROM work_sessions WHERE user_id = $1) as total,
                        (SELECT SUM(EXTRACT(EPOCH FROM (end_time - start_time))) FROM work_sessions WHERE user_id = $1 AND start_time >= $2) as weekly,
                        (SELECT SUM(EXTRACT(EPOCH FROM (end_time - start_time))) FROM work_sessions WHERE user_id = $1 AND start_time >= $3) as monthly
                """
                record = await connection.fetchrow(query, member.id, start_of_week, start_of_month)
            
            total_sec = record['total'] or 0
            weekly_sec = record['weekly'] or 0
            monthly_sec = record['monthly'] or 0
            
            if member.id in active_sessions:
                current_session_duration = (now - active_sessions[member.id]).total_seconds()
                total_sec += current_session_duration
                weekly_sec += current_session_duration
                monthly_sec += current_session_duration

            embed = discord.Embed(title=f"{member.display_name}さんの作業時間", color=discord.Color.blue())
            embed.add_field(name="今週の作業時間", value=format_duration(weekly_sec), inline=False)
            embed.add_field(name="今月の作業時間", value=format_duration(monthly_sec), inline=False)
            embed.add_field(name="累計作業時間", value=format_duration(total_sec), inline=False)
            if member.id in active_sessions:
                embed.set_footer(text="現在、作業セッション中です。")

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logging.error(f"Error in worktime command: {e}", exc_info=True)
            await interaction.followup.send("情報の取得中にエラーが発生しました。")

    @client.tree.command(name="worktime_ranking", description="作業時間ランキングを表示します。")
    @app_commands.describe(period="集計期間を選択してください")
    @app_commands.choices(period=[
        app_commands.Choice(name="累計 (All-time)", value="all"),
        app_commands.Choice(name="今月 (Monthly)", value="monthly"),
        app_commands.Choice(name="今週 (Weekly)", value="weekly"),
    ])
    async def worktime_ranking(interaction: discord.Interaction, period: str):
        if not client.db_pool: return await interaction.response.send_message("DB未接続です。", ephemeral=True)
        await interaction.response.defer()
        try:
            now = datetime.now(timezone.utc)
            title, query_filter = "", ""
            if period == "all":
                title = "🏆 累計作業時間ランキング TOP10"
                query_filter = ""
            elif period == "monthly":
                title = f"🗓️ {now.year}年{now.month}月 作業時間ランキング"
                query_filter = f"WHERE start_time >= DATE_TRUNC('month', NOW())"
            elif period == "weekly":
                title = f"📅 今週の作業時間ランキング"
                query_filter = f"WHERE start_time >= DATE_TRUNC('week', NOW())"
            
            async with client.db_pool.acquire() as connection:
                query = f"""
                    SELECT user_id, SUM(EXTRACT(EPOCH FROM (end_time - start_time))) as total 
                    FROM work_sessions {query_filter} 
                    GROUP BY user_id 
                    HAVING SUM(EXTRACT(EPOCH FROM (end_time - start_time))) > 0 
                    ORDER BY total DESC 
                    LIMIT 10;
                """
                records = await connection.fetch(query)
            
            if not records: return await interaction.followup.send(f"この期間の作業記録はまだありません。")

            embed = discord.Embed(title=title, color=discord.Color.gold())
            rank_emojis = ["🥇", "🥈", "🥉"]
            for i, record in enumerate(records):
                if interaction.guild:
                    member = interaction.guild.get_member(record['user_id'])
                    user_name = member.display_name if member else f"ID: {record['user_id']} (元メンバー)"
                else:
                    user_name = f"ID: {record['user_id']}"
                rank = rank_emojis[i] if i < 3 else f"**{i+1}位**"
                embed.add_field(name=f"{rank}：{user_name}", value=f"```{format_duration(record['total'])}```", inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logging.error(f"Error in worktime_ranking: {e}", exc_info=True)

    @client.tree.command(name="announce", description="指定したチャンネルにBotからお知らせを投稿します。(管理者限定)")
    @app_commands.checks.has_permissions(administrator=True)
    async def announce(interaction: discord.Interaction, channel: discord.TextChannel):
        await channel.send("★お知らせ用メッセージ入力欄★")
        await interaction.response.send_message(f"{channel.mention} にお知らせを投稿しました。", ephemeral=True)

    @client.tree.command(name="setup_recruit", description="作業募集用のパネルを設置します。(管理者限定)")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_recruit(interaction: discord.Interaction):
        embed = discord.Embed(title="📢 作業仲間募集パネル", description="下のボタンを押すと、今いるボイスチャンネルへの招待リンク付きで募集が投稿されるよ！", color=discord.Color.green())
        await interaction.channel.send(embed=embed, view=RecruitmentView())
        await interaction.response.send_message("募集パネルを設置しました。", ephemeral=True)

    if TOKEN:
        client.run(TOKEN, reconnect=True)
    else:
        logging.error("TOKEN not found.")

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
