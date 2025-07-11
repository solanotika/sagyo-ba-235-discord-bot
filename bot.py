import discord
import os
import json
import asyncio
from datetime import datetime, timedelta, timezone

# --- 環境変数からIDを取得 ---
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
BUMP_CHANNEL_ID = int(os.getenv('BUMP_CHANNEL_ID'))
BUMP_LOG_CHANNEL_ID = int(os.getenv('BUMP_LOG_CHANNEL_ID'))
INTRO_CHANNEL_ID = int(os.getenv('INTRO_CHANNEL_ID'))
INTRO_ROLE_ID = int(os.getenv('INTRO_ROLE_ID'))
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID'))

# --- 状態を保存するファイル名 ---
BUMP_COUNT_FILE = 'bump_counts.json'
LAST_BUMP_TIME_FILE = 'last_bump_time.txt'

# --- Discord Botのクライアント設定 ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)

# --- Git操作用の関数 ---
async def commit_and_push(file_paths, commit_message):
    """変更をリポジトリにコミット&プッシュする"""
    # BotとしてGitユーザーを設定
    await (await asyncio.create_subprocess_shell('git config --global user.name "GitHub Actions Bot"')).wait()
    await (await asyncio.create_subprocess_shell('git config --global user.email "action@github.com"')).wait()
    
    # ファイルをステージング
    for file_path in file_paths:
        await (await asyncio.create_subprocess_shell(f'git add {file_path}')).wait()
        
    # 変更があるか確認
    proc = await asyncio.create_subprocess_shell('git diff --staged --quiet', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.wait()

    # 終了コードが0なら変更なし、1なら変更あり
    if proc.returncode != 0:
        print(f"Committing changes: {commit_message}")
        await (await asyncio.create_subprocess_shell(f'git commit -m "{commit_message}"')).wait()
        await (await asyncio.create_subprocess_shell('git push')).wait()
    else:
        print("No changes to commit.")


# --- Bump関連の機能 (①, ②) ---
async def check_bump_status():
    print("Checking for bump status...")
    bump_channel = client.get_channel(BUMP_CHANNEL_ID)
    
    # DISBOARDのBot ID
    disboard_bot_id = 302050872383242240
    two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)

    # 最後のBump成功メッセージを探す
    async for message in bump_channel.history(limit=50):
        # DISBOARDの成功メッセージかチェック
        if message.author.id == disboard_bot_id and "表示順をアップしたよ" in message.content:
            bump_time = message.created_at
            
            # 状態ファイルを読み込み
            last_notified_bump_time_str = ""
            if os.path.exists(LAST_BUMP_TIME_FILE):
                with open(LAST_BUMP_TIME_FILE, 'r') as f:
                    last_notified_bump_time_str = f.read().strip()
            
            last_notified_bump_time = datetime.fromisoformat(last_notified_bump_time_str) if last_notified_bump_time_str else None

            # 2時間経過 & まだ通知していないか
            if datetime.now(timezone.utc) >= bump_time + timedelta(hours=2):
                if last_notified_bump_time is None or last_notified_bump_time < bump_time:
                    print(f"Bump reminder needed. Last bump at {bump_time}.")
                    await bump_channel.send("みんな、DISBOARDの **/bump** の時間だよ！\nサーバーの表示順を上げて、新しい仲間を増やそう！")
                    
                    # 通知した時間を記録
                    with open(LAST_BUMP_TIME_FILE, 'w') as f:
                        f.write(str(bump_time.isoformat()))
                    
                    # Bump回数の記録と報告
                    if message.interaction and message.interaction.user:
                        user = message.interaction.user
                        counts = {}
                        if os.path.exists(BUMP_COUNT_FILE):
                            with open(BUMP_COUNT_FILE, 'r') as f:
                                counts = json.load(f)
                        
                        user_id_str = str(user.id)
                        counts[user_id_str] = counts.get(user_id_str, 0) + 1
                        
                        with open(BUMP_COUNT_FILE, 'w') as f:
                            json.dump(counts, f, indent=2)

                        # 監査ログに出力
                        log_channel = client.get_channel(BUMP_LOG_CHANNEL_ID)
                        
                        # ユーザー名を取得してリスト作成
                        guild = bump_channel.guild
                        report_lines = ["📈 **Bump実行回数レポート** 📈"]
                        sorted_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)

                        for uid, count in sorted_counts:
                            member = guild.get_member(int(uid))
                            user_name = member.display_name if member else f"ID: {uid}"
                            report_lines.append(f"・{user_name}: {count}回")
                            
                        await log_channel.send("\n".join(report_lines))

                        # ファイルをリポジトリに保存
                        await commit_and_push([BUMP_COUNT_FILE, LAST_BUMP_TIME_FILE], "Update bump status")
            break # 最新のbumpを見つけたらループを抜ける
    print("Bump check finished.")


# --- 自己紹介関連の機能 (③, ④, ⑤) ---
async def check_introductions():
    print("Checking for new introductions...")
    intro_channel = client.get_channel(INTRO_CHANNEL_ID)
    welcome_channel = client.get_channel(WELCOME_CHANNEL_ID)
    guild = intro_channel.guild
    intro_role = guild.get_role(INTRO_ROLE_ID)

    # 24時間前以降のメッセージをチェック
    since = datetime.now(timezone.utc) - timedelta(days=1)
    
    async for message in intro_channel.history(limit=200, after=since):
        author = message.author
        # Botや既にロールを持っている人はスキップ
        if author.bot or intro_role in author.roles:
            continue

        print(f"Found new introduction from {author.display_name}. Assigning role...")
        try:
            # ③ ロールを付与
            await author.add_roles(intro_role, reason="自己紹介を投稿したため")
            
            # ④ ウェルカムメッセージを送信
            await welcome_channel.send(f"🎉{author.mention}さん、ようこそ「作業場235」へ！VCが開放されたよ、自由に使ってね！")
        except discord.Forbidden:
            print(f"Error: Missing permissions to assign role to {author.display_name}")
        except Exception as e:
            print(f"An error occurred while processing {author.display_name}: {e}")
            
    print("Introduction check finished.")


@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    
    # Botが起動したら、各チェック処理を実行
    try:
        await check_bump_status()
        await check_introductions()
    except Exception as e:
        print(f"An error occurred during scheduled checks: {e}")
    finally:
        # 処理が終わったらBotを終了させてワークフローを完了させる
        print("All tasks finished. Closing client.")
        await client.close()


# --- メイン処理 ---
if __name__ == "__main__":
    client.run(TOKEN)
