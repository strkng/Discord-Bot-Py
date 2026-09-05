import fcntl
import logging
import os
import secrets
import threading
import time
import urllib.parse

import asyncpg
import discord
from discord.ext import commands
from flask import Flask, redirect, request, session
import requests


# ========================================
# 設定
# ========================================

API_BASE = "https://discord.com/api/v10"

LOCK_FILE_PATH = "bot_instance.lock"

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")

REDIRECT_URI = os.environ.get(
    "REDIRECT_URI",
    "https://discord-bot-py-4mzn.onrender.com/auth/callback",
)

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")


# ========================================
# 禁止サーバー
#
# このサーバーにBotがいなくても、
# OAuth認証したユーザーが参加していれば
# 認証拒否する。
# ========================================

BANNED_GUILD_IDS = {
    "1392780216241491968",
    "1541042102152986664",
}


# ========================================
# 多重起動防止・排他ロック制御
# ========================================

lock_file = open(
    LOCK_FILE_PATH,
    "w",
)

try:

    fcntl.flock(
        lock_file,
        fcntl.LOCK_EX | fcntl.LOCK_NB,
    )

    print(
        "🔒 【排他制御】ロック取得成功: "
        "このプロセスをメインインスタンスとして起動します。",
        flush=True,
    )

    IS_PRIMARY_INSTANCE = True

except (IOError, BlockingIOError):

    print(
        "🚨 【多重起動検知】"
        "すでに別のプロセスでボットが稼働中です。"
        "このインスタンスではDiscord Botを起動しません。",
        flush=True,
    )

    IS_PRIMARY_INSTANCE = False


# ========================================
# Flask
# ========================================

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    os.urandom(32),
)


@app.route("/")
def home():

    return "Bot is running!"


# ========================================
# OAuth2 ログイン開始
# ========================================

@app.route("/auth/login")
def auth_login():

    if not CLIENT_ID:

        return (
            "CLIENT_IDが設定されていません。",
            500,
        )

    if not CLIENT_SECRET:

        return (
            "CLIENT_SECRETが設定されていません。",
            500,
        )

    if not REDIRECT_URI:

        return (
            "REDIRECT_URIが設定されていません。",
            500,
        )


    # ------------------------------------
    # 元のURLからGuild ID / Role IDを取得
    #
    # 既存仕様:
    #
    # /auth/login?state=GUILD_ID_ROLE_ID
    #
    # ------------------------------------

    requested_state = request.args.get(
        "state",
        "",
    )

    grant_guild_id = None
    grant_role_id = None


    if requested_state:

        if "_" not in requested_state:

            return (
                "認証情報の形式が正しくありません。",
                400,
            )


        parts = requested_state.split(
            "_",
            1,
        )


        if len(parts) != 2:

            return (
                "認証情報の形式が正しくありません。",
                400,
            )


        if not parts[0].isdigit():

            return (
                "Guild ID / Role IDが正しくありません。",
                400,
            )


        if not parts[1].isdigit():

            return (
                "Guild ID / Role IDが正しくありません。",
                400,
            )


        grant_guild_id = parts[0]

        grant_role_id = parts[1]


    # ------------------------------------
    # OAuth用の安全なstateを生成
    # ------------------------------------

    oauth_state = secrets.token_urlsafe(32)


    # ------------------------------------
    # Flask Sessionへ保存
    # ------------------------------------

    session["oauth_state"] = oauth_state

    session["grant_guild_id"] = grant_guild_id

    session["grant_role_id"] = grant_role_id


    # ------------------------------------
    # Discord OAuth2 URL
    # ------------------------------------

    params = {

        "client_id": CLIENT_ID,

        "response_type": "code",

        "redirect_uri": REDIRECT_URI,

        "scope": "identify guilds",

        "state": oauth_state,

    }


    discord_login_url = (
        "https://discord.com/oauth2/authorize?"
        + urllib.parse.urlencode(params)
    )


    return redirect(
        discord_login_url
    )


# ========================================
# OAuth2 Callback
# ========================================

@app.route("/auth/callback")
def auth_callback():

    code = request.args.get(
        "code"
    )

    returned_state = request.args.get(
        "state"
    )

    oauth_error = request.args.get(
        "error"
    )


    # ------------------------------------
    # OAuthエラー
    # ------------------------------------

    if oauth_error:

        return (
            "Discord認証がキャンセルまたは失敗しました: "
            f"{oauth_error}",
            400,
        )


    if not code:

        return (
            "認証コードが取得できませんでした。",
            400,
        )


    if not returned_state:

        return (
            "OAuth stateがありません。",
            400,
        )


    # ------------------------------------
    # state検証
    # ------------------------------------

    saved_state = session.pop(
        "oauth_state",
        None,
    )


    if not saved_state:

        return (
            "認証セッションが見つかりません。"
            "最初から認証をやり直してください。",
            400,
        )


    if not secrets.compare_digest(
        returned_state,
        saved_state,
    ):

        return (
            "不正なOAuth stateです。",
            400,
        )


    # ------------------------------------
    # ロール付与先
    # ------------------------------------

    grant_guild_id = session.pop(
        "grant_guild_id",
        None,
    )

    grant_role_id = session.pop(
        "grant_role_id",
        None,
    )


    # ====================================
    # Authorization Code
    # → Access Token
    # ====================================

    token_data = {

        "client_id": CLIENT_ID,

        "client_secret": CLIENT_SECRET,

        "grant_type": "authorization_code",

        "code": code,

        "redirect_uri": REDIRECT_URI,

    }


    token_headers = {

        "Content-Type":
            "application/x-www-form-urlencoded",

    }


    try:

        response = requests.post(

            f"{API_BASE}/oauth2/token",

            data=token_data,

            headers=token_headers,

            timeout=10,

        )

    except requests.RequestException as e:

        print(
            f"❌ Discord OAuth2 Token API通信失敗: {e}",
            flush=True,
        )

        return (
            "Discordとの通信に失敗しました。",
            502,
        )


    try:

        tokens = response.json()

    except ValueError:

        print(
            "❌ Discord Token APIから不正なJSON: "
            f"{response.text}",
            flush=True,
        )

        return (
            "Discordから不正な応答が返されました。",
            502,
        )


    if "access_token" not in tokens:

        return (
            "アクセストークンの取得に失敗しました: "
            f"{tokens.get('error_description', tokens)}",
            400,
        )


    access_token = tokens["access_token"]


    api_headers = {

        "Authorization":
            f"Bearer {access_token}",

    }


    # ====================================
    # ユーザー情報取得
    # ====================================

    try:

        user_info_response = requests.get(

            f"{API_BASE}/users/@me",

            headers=api_headers,

            timeout=10,

        )

    except requests.RequestException as e:

        print(
            f"❌ ユーザー情報取得失敗: {e}",
            flush=True,
        )

        return (
            "Discordとの通信に失敗しました。",
            502,
        )


    if user_info_response.status_code != 200:

        print(
            "❌ ユーザー情報取得APIエラー: "
            f"{user_info_response.text}",
            flush=True,
        )

        return (
            "Discordユーザー情報の取得に失敗しました。",
            400,
        )


    try:

        user_data = (
            user_info_response.json()
        )

    except ValueError:

        return (
            "Discordユーザー情報が不正です。",
            400,
        )


    user_id = user_data.get(
        "id"
    )

    username = user_data.get(
        "username",
        "不明",
    )


    if not user_id:

        return (
            "DiscordユーザーIDを取得できませんでした。",
            400,
        )


    # ====================================
    # ユーザー参加Guild取得
    #
    # 禁止サーバー判定に使用。
    #
    # 禁止サーバーにBotがいなくてもOK。
    # ====================================

    try:

        guilds_response = requests.get(

            f"{API_BASE}/users/@me/guilds",

            headers=api_headers,

            timeout=10,

        )

    except requests.RequestException as e:

        print(
            f"❌ Guild一覧取得失敗: {e}",
            flush=True,
        )

        return (
            "Discordとの通信に失敗しました。",
            502,
        )


    if guilds_response.status_code != 200:

        print(
            "❌ Guild一覧APIエラー: "
            f"{guilds_response.text}",
            flush=True,
        )

        return (
            "参加サーバー情報の取得に失敗しました。",
            400,
        )


    try:

        user_guilds = (
            guilds_response.json()
        )

    except ValueError:

        return (
            "Discordのサーバー情報が不正です。",
            400,
        )


    if not isinstance(
        user_guilds,
        list,
    ):

        print(
            "❌ Guild一覧が配列ではありません: "
            f"{user_guilds}",
            flush=True,
        )

        return (
            "サーバー情報の取得に失敗しました。",
            400,
        )


    # ====================================
    # 禁止サーバー判定
    # ====================================

    banned_hit_guilds = []


    for guild in user_guilds:

        guild_id = str(
            guild.get(
                "id",
                "",
            )
        )


        if guild_id in BANNED_GUILD_IDS:

            banned_hit_guilds.append(
                guild_id
            )


    # ====================================
    # 禁止サーバーに参加している場合
    # ====================================

    if banned_hit_guilds:

        print(
            "🚨 【認証ブロック】 "
            f"ユーザー: {username} "
            f"(ID: {user_id}) が "
            f"禁止サーバーID: {banned_hit_guilds} "
            "に参加しているため認証を拒否しました。",
            flush=True,
        )


        return """
        <!DOCTYPE html>

        <html lang="ja">

        <head>

            <meta charset="UTF-8">

            <meta name="viewport"
                  content="width=device-width, initial-scale=1.0">

            <title>認証失敗</title>


            <style>

                body {

                    background-color: #1e1e2e;

                    color: #cdd6f4;

                    font-family: sans-serif;

                    display: flex;

                    justify-content: center;

                    align-items: center;

                    height: 100vh;

                    margin: 0;

                }


                .card {

                    background-color: #313244;

                    padding: 2.5rem;

                    border-radius: 16px;

                    box-shadow:
                        0 8px 24px rgba(0,0,0,0.3);

                    text-align: center;

                    max-width: 400px;

                    width: 90%;

                }


                .icon {

                    font-size: 3rem;

                    margin-bottom: 1rem;

                }


                h1 {

                    color: #f38ba8;

                    font-size: 1.5rem;

                    margin-bottom: 1rem;

                }


                p {

                    color: #a6adc8;

                    font-size: 0.95rem;

                    line-height: 1.6;

                }

            </style>

        </head>


        <body>

            <div class="card">

                <div class="icon">
                    ❌
                </div>


                <h1>
                    認証に失敗しました
                </h1>


                <p>
                    参加が禁止されている特定のサーバーに
                    加入しているため、ロールを付与できません。
                </p>

            </div>

        </body>

        </html>
        """


    # ====================================
    # 禁止サーバーなし
    # → ロール付与
    # ====================================

    if grant_guild_id and grant_role_id:

        if not DISCORD_TOKEN:

            print(
                "❌ DISCORD_TOKENが設定されていません。",
                flush=True,
            )

            return (
                "Botの設定に問題があるため、"
                "ロールを付与できません。",
                500,
            )


        role_headers = {

            "Authorization":
                f"Bot {DISCORD_TOKEN}",

            "Content-Type":
                "application/json",

        }


        role_url = (

            f"{API_BASE}/guilds/"
            f"{grant_guild_id}/members/"
            f"{user_id}/roles/"
            f"{grant_role_id}"

        )


        try:

            role_res = requests.put(

                role_url,

                headers=role_headers,

                timeout=10,

            )

        except requests.RequestException as e:

            print(
                f"❌ ロール付与API通信失敗: {e}",
                flush=True,
            )

            return (
                "Discordとの通信に失敗したため、"
                "ロールを付与できませんでした。",
                502,
            )


        if role_res.status_code == 204:

            print(
                "✅ ロール付与成功: "
                f"user={user_id}, "
                f"guild={grant_guild_id}, "
                f"role={grant_role_id}",
                flush=True,
            )


        else:

            print(
                "❌ ロール付与失敗: "
                f"HTTP {role_res.status_code} "
                f"{role_res.text}",
                flush=True,
            )


            return """
            <!DOCTYPE html>

            <html lang="ja">

            <head>

                <meta charset="UTF-8">

                <meta name="viewport"
                      content="width=device-width, initial-scale=1.0">

                <title>ロール付与失敗</title>


                <style>

                    body {

                        background-color: #1e1e2e;

                        color: #cdd6f4;

                        font-family: sans-serif;

                        display: flex;

                        justify-content: center;

                        align-items: center;

                        height: 100vh;

                        margin: 0;

                    }


                    .card {

                        background-color: #313244;

                        padding: 2.5rem;

                        border-radius: 16px;

                        text-align: center;

                        max-width: 400px;

                        width: 90%;

                    }


                    .icon {

                        font-size: 3rem;

                        margin-bottom: 1rem;

                    }


                    h1 {

                        color: #fab387;

                    }


                    p {

                        color: #a6adc8;

                        line-height: 1.6;

                    }

                </style>

            </head>


            <body>

                <div class="card">

                    <div class="icon">
                        ⚠️
                    </div>


                    <h1>
                        ロール付与に失敗しました
                    </h1>


                    <p>
                        認証は完了しましたが、
                        ロールを付与できませんでした。
                        Botの権限やロール階層を確認してください。
                    </p>

                </div>

            </body>

            </html>
            """


    # ====================================
    # 認証成功
    # ====================================

    return """
    <!DOCTYPE html>

    <html lang="ja">

    <head>

        <meta charset="UTF-8">

        <meta name="viewport"
              content="width=device-width, initial-scale=1.0">

        <title>認証完了</title>


        <style>

            body {

                background-color: #1e1e2e;

                color: #cdd6f4;

                font-family: sans-serif;

                display: flex;

                justify-content: center;

                align-items: center;

                height: 100vh;

                margin: 0;

            }


            .card {

                background-color: #313244;

                padding: 2.5rem;

                border-radius: 16px;

                box-shadow:
                    0 8px 24px rgba(0,0,0,0.3);

                text-align: center;

                max-width: 400px;

                width: 90%;

            }


            .icon {

                font-size: 3rem;

                margin-bottom: 1rem;

            }


            h1 {

                color: #a6e3a1;

                font-size: 1.5rem;

                margin-bottom: 1rem;

            }


            p {

                color: #a6adc8;

                font-size: 0.95rem;

                line-height: 1.6;

            }

        </style>

    </head>


    <body>

        <div class="card">

            <div class="icon">
                ✨
            </div>


            <h1>
                認証に成功しました！
            </h1>


            <p>
                ロールが正常に付与されました。
                Discordに戻って確認してください。
            </p>

        </div>

    </body>

    </html>
    """


# ========================================
# Discord Bot
# ========================================

intents = discord.Intents.default()

intents.message_content = True

intents.voice_states = True

intents.members = True


class MyBot(commands.Bot):

    async def setup_hook(self):

        # --------------------------------
        # PostgreSQL / Supabase
        # --------------------------------

        if DATABASE_URL:

            try:

                self.pool = await asyncpg.create_pool(

                    DATABASE_URL,

                    min_size=1,

                    max_size=5,

                    statement_cache_size=0,

                )


                print(
                    "✅ PostgreSQLへの接続に成功しました！",
                    flush=True,
                )


            except Exception as e:

                print(
                    f"❌ PostgreSQL接続失敗: {e}",
                    flush=True,
                )


        # --------------------------------
        # Cog読み込み
        # --------------------------------

        if os.path.exists("./cogs"):

            for filename in os.listdir("./cogs"):

                if not filename.endswith(".py"):

                    continue


                if filename.startswith("_"):

                    continue


                cog_name = (
                    f"cogs.{filename[:-3]}"
                )


                if cog_name in self.extensions:

                    continue


                try:

                    await self.load_extension(
                        cog_name
                    )


                    print(
                        "✅ Cog読み込み成功: "
                        f"{cog_name}",
                        flush=True,
                    )


                except Exception as e:

                    print(
                        "❌ Cog読み込み失敗: "
                        f"{cog_name}: {e}",
                        flush=True,
                    )


        # --------------------------------
        # Slash Commands同期
        # --------------------------------

        try:

            synced = await self.tree.sync()


            print(
                "🌟 スラッシュコマンド同期成功 "
                f"({len(synced)}個)",
                flush=True,
            )


        except Exception as e:

            print(
                f"❌ スラッシュコマンド同期失敗: {e}",
                flush=True,
            )


# ========================================
# Bot生成
# ========================================

def create_bot():

    new_bot = MyBot(

        command_prefix="!",

        intents=intents,

    )


    # ====================================
    # Botステータス
    # ====================================

    async def update_bot_status():

        server_count = len(
            new_bot.guilds
        )


        activity = discord.Activity(

            type=discord.ActivityType.watching,

            name=f"{server_count}個のサーバー",

        )


        await new_bot.change_presence(

            activity=activity

        )


    # ====================================
    # Bot Ready
    # ====================================

    @new_bot.event
    async def on_ready():

        print(

            f"=== ログイン成功: "
            f"{new_bot.user.name} "
            f"(ID: {new_bot.user.id}) ===",

            flush=True,

        )


        await update_bot_status()


    # ====================================
    # Guild参加
    # ====================================

    @new_bot.event
    async def on_guild_join(guild):

        await update_bot_status()


    # ====================================
    # Guild退出
    # ====================================

    @new_bot.event
    async def on_guild_remove(guild):

        await update_bot_status()


    return new_bot


# ========================================
# Discord Bot起動
# ========================================

def start_discord_bot():

    if not DISCORD_TOKEN:

        print(

            "❌ DISCORD_TOKENが設定されていません。"
            "Discord Botを起動できません。",

            flush=True,

        )

        return


    # ------------------------------------
    # 429再試行設定
    #
    # 60秒
    # → 120秒
    # → 240秒
    # → 480秒
    # → 900秒
    #
    # 最大15分待機
    # ------------------------------------

    retry_delays = [

        60,

        120,

        240,

        480,

        900,

    ]


    retry_count = 0


    while True:

        bot = None


        try:

            # --------------------------------
            # 毎回新しいBotインスタンスを作成
            #
            # Session is closed対策
            # --------------------------------

            bot = create_bot()


            print(
                "🔵 Discord Botを起動しています...",
                flush=True,
            )


            bot.run(
                DISCORD_TOKEN
            )


            # --------------------------------
            # 正常終了
            # --------------------------------

            print(
                "⚠️ Discord Botが終了しました。",
                flush=True,
            )

            return


        except discord.HTTPException as e:

            # --------------------------------
            # HTTP 429
            # --------------------------------

            if e.status == 429:

                if retry_count < len(
                    retry_delays
                ):

                    wait_time = retry_delays[
                        retry_count
                    ]

                else:

                    wait_time = 900


                retry_count += 1


                print(
                    "⚠️ Discord APIが429 "
                    "Rate Limitを返しました。",
                    flush=True,
                )


                print(
                    "⏳ "
                    f"{wait_time}秒待ってから"
                    "Discord Botを再接続します。",
                    flush=True,
                )


                print(
                    "🔄 "
                    f"429再試行回数: {retry_count}",
                    flush=True,
                )


                # --------------------------------
                # 古いBotを破棄
                # --------------------------------

                bot = None


                time.sleep(
                    wait_time
                )


                continue


            # --------------------------------
            # 429以外のHTTPエラー
            # --------------------------------

            print(
                "❌ Discord HTTPエラー: "
                f"HTTP {e.status}: {e}",
                flush=True,
            )

            return


        except discord.LoginFailure as e:

            print(
                "❌ Discordログイン失敗: "
                "Botトークンが無効です。",
                flush=True,
            )


            print(
                f"詳細: {e}",
                flush=True,
            )


            return


        except discord.GatewayNotFound as e:

            print(
                "❌ Discord Gatewayが見つかりません。",
                flush=True,
            )


            print(
                f"詳細: {e}",
                flush=True,
            )


            return


        except discord.ConnectionClosed as e:

            print(
                "⚠️ Discord Gateway接続が終了しました。",
                flush=True,
            )


            print(
                f"詳細: {e}",
                flush=True,
            )


            print(
                "⏳ 60秒待って再接続します。",
                flush=True,
            )


            bot = None


            time.sleep(
                60
            )


            continue


        except RuntimeError as e:

            # --------------------------------
            # Session is closed等
            #
            # 念のため古いBotを破棄して
            # 新しいインスタンスで再試行
            # --------------------------------

            error_text = str(e)


            if "Session is closed" in error_text:

                print(
                    "⚠️ Discord内部Sessionが閉じられました。",
                    flush=True,
                )


                print(
                    "⏳ 60秒待って新しいBot "
                    "インスタンスで再接続します。",
                    flush=True,
                )


                bot = None


                time.sleep(
                    60
                )


                continue


            print(
                "❌ RuntimeError: "
                f"{e}",
                flush=True,
            )


            return


        except Exception as e:

            print(
                "❌ Bot起動中に予期しないエラーが"
                "発生しました: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )


            return


# ========================================
# メインインスタンスの場合のみ
# Discord Bot起動
# ========================================

if IS_PRIMARY_INSTANCE:

    threading.Thread(

        target=start_discord_bot,

        daemon=True,

    ).start()


# ========================================
# Flaskサーバー起動
# ========================================

if __name__ == "__main__":

    port = int(

        os.environ.get(

            "PORT",

            10000,

        )

    )


    app.run(

        host="0.0.0.0",

        port=port,

    )
