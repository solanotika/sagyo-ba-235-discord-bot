import discord
from discord.ext import tasks
import os
import json
import asyncio
from datetime import datetime, timedelta, timezone

# --- 環境変数からIDを取得 ---
# (この部分は変更なし)
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
BUMP_CHANNEL_ID = int(os.getenv('BUMP_CHANNEL_ID'))
BUMP_LOG_CHANNEL_ID = int(os.getenv('BUMP_LOG_CHANNEL_ID'))
INTRO_CHANNEL_ID = int(os.getenv('INTRO_CHANNEL_ID'))
INTRO_ROLE_ID = int(os.getenv('INTRO_ROLE_ID'))
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID'))

# --- 状態を保存するファイル名 ---
# (この部分は変更なし)
BUMP_COUNT_FILE = 'bump_counts.json'
LAST_BUMP_TIME_FILE = 'last_bump_time.txt'

# --- Discord Botのクライアント設定 ---
# (この部分は変更なし)
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)

# --- Git操作用の関数 ---
# (commit_and_push関数は変更なし)
async def commit_and_push(file_paths, commit_message):
    """変更をリポジトリにコミット&プッシュする"""
    proc_git_config_user = await asyncio.create_subprocess_shell('git config --global user.name "GitHub Actions Bot"')
    await proc_git_config_user.wait()
    proc_git_config_email = await asyncio.create_subprocess_shell('git config --global user.email "action@github.com"')
    await proc_git_config_email.wait()
    
    for file_path in file_paths:
        proc_git_add = await asyncio.create_subprocess_shell(f'git add {file_path}')
        await proc_git_add.wait()
        
    proc_git_diff = await asyncio.create_subprocess_shell('git diff --staged --quiet')
    await proc_git_diff.wait()

    if proc_git_diff.returncode != 0:
        print(f"Committing changes: {commit_message}")
        proc_git_commit = await asyncio.create_subprocess_shell(f'git commit -m "{commit_message}"')
        await proc_git_commit.wait()
        proc_git_push = await asyncio.create_subprocess_shell('git push')
        await proc_git_push.wait()
    else:
        print("No changes to commit.")

# --- Bump関連の機能 (①, ②) ---
# (check_bump_status関数は変更なし)
async def check_bump_status():
    # ... (前回のコードと全く同じ)
    print("Checking for bump status...")
    bump_channel = client.get_channel(BUMP_CHANNEL_ID)
    if not bump_channel: return

    disboard_bot_id = 302050872383242240
    
    async for message in bump_channel.history(limit=50):
        if message.author.id == disboard_bot_id and "表示順をアップしたよ" in message.content:
            bump_time = message.created_at
            
            last_notified_bump_time_str = ""
            if os.path.exists(LAST_BUMP_TIME_FILE):
                with open(LAST_BUMP_TIME_FILE, 'r') as f:
                    last_notified_bump_time_str = f.read().strip()
            
            last_notified_bump_time = datetime.fromisoformat(last_notified_bump_time_str) if last_notified_bump_time_str else None

            if datetime.now(timezone.utc) >= bump_time + timedelta(hours=2):
                if last_notified_bump_time is None or last_notified_bump_time < bump_time:
                    print(f"Bump reminder needed. Last bump at {bump_time}.")
                    await bump_channel.send("みんな、DISBOARDの **/bump** の時間だよ！\nサーバーの表示順を上げて、新しい仲間を増やそう！")
                    
                    with open(LAST_BUMP_TIME_FILE, 'w') as f:
                        f.write(str(bump_time.isoformat()))
                    
                    if message.interaction and message.interaction.user:
                        user = message.interaction.user
                        counts = {}
                        if os.path.exists(BUMP_COUNT_FILE):
                            with open(BUMP_COUNT_FILE, 'r') as f:
                                try:
                                    counts = json.load(f)
                                except json.JSONDecodeError:
                                    counts = {}
                        
                        user_id_str = str(user.id)
                        counts[user_id_str] = counts.get(user_id_str, 0) + 1
                        
                        with open(BUMP_COUNT_FILE, 'w') as f:
                            json.dump(counts, f, indent=2)

                        log_channel = client.get_channel(BUMP_LOG_CHANNEL_ID)
                        if log_channel:
                            guild = bump_channel.guild
                            report_lines = ["📈 **Bump実行回数レポート** 📈"]
                            sorted_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)

                            for uid, count in sorted_counts:
                                member = guild.get_member(int(uid))
                                user_name = member.display_name if member else f"ID: {uid}"
                                report_lines.append(f"・{user_name}: {count}回")
                                
                            await log_channel.send("\n".join(report_lines))

                        await commit_and_push([BUMP_COUNT_FILE, LAST_BUMP_TIME_FILE], "Update bump status")
            break
    print("Bump check finished.")

# --- 自己紹介関連の機能 (③, ④, ⑤) ---
# (check_introductions関数は変更なし)
async def check_introductions():
    # ... (前回のコードと全く同じ)
    print("Checking for new introductions...")
    intro_channel = client.get_channel(INTRO_CHANNEL_ID)
    if not intro_channel: return
    
    welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
    guild = intro_channel.guild
    intro_role = guild.get_role(INTRO_ROLE_ID)

    if not (welcome_channel and guild and intro_role): return

    since = datetime.now(timezone.utc) - timedelta(days=1)
    
    async for message in intro_channel.history(limit=200, after=since):
        author = message.author
        if isinstance(author, discord.Member) and not author.bot and intro_role not in author.roles:
            print(f"Found new introduction from {author.display_name}. Assigning role...")
            try:
                await author.add_roles(intro_role, reason="自己紹介を投稿したため")
                await welcome_channel.send(f"🎉{author.mention}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！")
            except discord.Forbidden:
                print(f"Error: Missing permissions to assign role to {author.display_name}")
            except Exception as e:
                print(f"An error occurred while processing {author.display_name}: {e}")
            
    print("Introduction check finished.")

# --- ここからが変更点 ---

# 15分ごとに実行する定期処理タスクを定義
@tasks.loop(minutes=15)
async def periodic_checks():
    """定期的に実行したい処理をここにまとめる"""
    print(f"\n--- Running periodic checks at {datetime.now()} ---")
    try:
        await check_bump_status()
        await check_introductions()
    except Exception as e:
        print(f"An error occurred during periodic checks: {e}")
    print("--- Periodic checks finished. Waiting for next loop. ---")

@periodic_checks.before_loop
async def before_periodic_checks():
    """ループが始まる前に、Botが完全に準備できるまで待つ"""
    await client.wait_until_ready()

@client.event
async def on_ready():
    """Botが起動したときに一度だけ実行される処理"""
    print(f'Logged in as {client.user}')
    # 定期処理タスクを開始する
    periodic_checks.start()

# --- メイン処理 ---
if __name__ == "__main__":
    client.run(TOKEN)
