import fcntl
import logging
import math
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


# ============================================================
# 基本設定
# ============================================================

PROJECT_NAME = "Discord Bot"

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
REDIRECT_URI = os.environ.get("REDIRECT_URI")

DATABASE_URL = os.environ.get("DATABASE_URL")

PORT = int(os.environ.get("PORT", "10000"))


# ============================================================
# Flask
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    os.urandom(32)
)


# ============================================================
# OAuthでアクセスを拒否するサーバー
# ============================================================

BANNED_GUILD_IDS = {
    "1392780216241491968",
    "1122334455667788990",
}


# ============================================================
# 排他制御
# ============================================================

LOCK_FILE = "bot_instance.lock"

lock_file = None
IS_PRIMARY_INSTANCE = False

try:

    lock_file = open(LOCK_FILE, "w")

    fcntl.flock(
        lock_file.fileno(),
        fcntl.LOCK_EX | fcntl.LOCK_NB
    )

    IS_PRIMARY_INSTANCE = True

    print(
        "🔒 【排他制御】ロック取得成功。Botを起動します。",
        flush=True
    )

except BlockingIOError:

    print(
        "⚠️ 【排他制御】別のBotプロセスが起動中です。",
        flush=True
    )

    IS_PRIMARY_INSTANCE = False

except Exception as e:

    print(
        f"⚠️ 【排他制御】ロック処理でエラー: {e}",
        flush=True
    )

    IS_PRIMARY_INSTANCE = False


# ============================================================
# Flaskトップページ
# ============================================================

@app.route("/")
def index():

    return "Bot is running!"


# ============================================================
# OAuth Login
# ============================================================

@app.route("/auth/login")
def auth_login():

    state_parameter = request.args.get("state")

    if not state_parameter:

        return "Missing state parameter", 400

    # --------------------------------------------------------
    # GUILD_ID_ROLE_ID
    # --------------------------------------------------------

    parts = state_parameter.split("_")

    if len(parts) != 2:

        return "Invalid state parameter", 400

    guild_id, role_id = parts

    if not guild_id.isdigit() or not role_id.isdigit():

        return "Invalid guild or role ID", 400

    # --------------------------------------------------------
    # セッション保存
    # --------------------------------------------------------

    session["guild_id"] = guild_id
    session["role_id"] = role_id

    # --------------------------------------------------------
    # OAuth CSRF対策
    # --------------------------------------------------------

    oauth_state = secrets.token_urlsafe(32)

    session["oauth_state"] = oauth_state

    # --------------------------------------------------------
    # Discord OAuth2
    # --------------------------------------------------------

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": oauth_state,
    }

    url = (
        "https://discord.com/oauth2/authorize?"
        + urllib.parse.urlencode(params)
    )

    return redirect(url)


# ============================================================
# OAuth Callback
# ============================================================

@app.route("/auth/callback")
def auth_callback():

    code = request.args.get("code")
    received_state = request.args.get("state")

    # --------------------------------------------------------
    # state確認
    # --------------------------------------------------------

    saved_state = session.get("oauth_state")

    if not saved_state or received_state != saved_state:

        return """
        <!DOCTYPE html>
        <html lang="ja">
        <head>
            <meta charset="UTF-8">
            <title>認証失敗</title>
        </head>
        <body>
            <h1>❌ 認証失敗</h1>
            <p>OAuth stateが一致しません。</p>
        </body>
        </html>
        """, 400

    if not code:

        return """
        <!DOCTYPE html>
        <html lang="ja">
        <head>
            <meta charset="UTF-8">
            <title>認証失敗</title>
        </head>
        <body>
            <h1>❌ 認証失敗</h1>
            <p>認証コードがありません。</p>
        </body>
        </html>
        """, 400

    # --------------------------------------------------------
    # 対象Guild / Role
    # --------------------------------------------------------

    guild_id = session.get("guild_id")
    role_id = session.get("role_id")

    if not guild_id or not role_id:

        return """
        <!DOCTYPE html>
        <html lang="ja">
        <head>
            <meta charset="UTF-8">
            <title>認証失敗</title>
        </head>
        <body>
            <h1>❌ 認証失敗</h1>
            <p>対象サーバー情報がありません。</p>
        </body>
        </html>
        """, 400

    # ========================================================
    # Discord OAuth2 Token取得
    # ========================================================

    token_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    try:

        token_response = requests.post(
            "https://discord.com/api/v10/oauth2/token",
            data=token_data,
            timeout=15
        )

    except Exception as e:

        print(
            f"❌ OAuth token request error: {e}",
            flush=True
        )

        return """
        <h1>❌ 認証失敗</h1>
        <p>Discordへの接続に失敗しました。</p>
        """, 500

    if token_response.status_code != 200:

        print(
            "❌ OAuth token取得失敗:",
            token_response.status_code,
            token_response.text,
            flush=True
        )

        return """
        <h1>❌ 認証失敗</h1>
        <p>Discord OAuth2トークンを取得できませんでした。</p>
        """, 400

    try:

        token_json = token_response.json()

    except Exception:

        return """
        <h1>❌ 認証失敗</h1>
        <p>Discordから不正なレスポンスが返されました。</p>
        """, 500

    access_token = token_json.get("access_token")

    if not access_token:

        return """
        <h1>❌ 認証失敗</h1>
        <p>アクセストークンを取得できませんでした。</p>
        """, 400

    # ========================================================
    # ユーザー情報取得
    # ========================================================

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    try:

        user_response = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers=headers,
            timeout=15
        )

    except Exception as e:

        print(
            f"❌ User API request error: {e}",
            flush=True
        )

        return """
        <h1>❌ 認証失敗</h1>
        <p>Discordユーザー情報の取得に失敗しました。</p>
        """, 500

    if user_response.status_code != 200:

        print(
            "❌ ユーザー情報取得失敗:",
            user_response.status_code,
            user_response.text,
            flush=True
        )

        return """
        <h1>❌ 認証失敗</h1>
        <p>Discordユーザー情報を取得できませんでした。</p>
        """, 400

    try:

        user_data = user_response.json()

    except Exception:

        return """
        <h1>❌ 認証失敗</h1>
        <p>ユーザー情報を解析できませんでした。</p>
        """, 500

    user_id = user_data.get("id")

    if not user_id:

        return """
        <h1>❌ 認証失敗</h1>
        <p>ユーザーIDを取得できませんでした。</p>
        """, 400

    # ========================================================
    # ユーザーが所属しているGuild一覧
    # ========================================================

    try:

        guild_response = requests.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers=headers,
            timeout=15
        )

    except Exception as e:

        print(
            f"❌ Guild API request error: {e}",
            flush=True
        )

        return """
        <h1>❌ 認証失敗</h1>
        <p>Discordサーバー情報の取得に失敗しました。</p>
        """, 500

    if guild_response.status_code != 200:

        print(
            "❌ Guild一覧取得失敗:",
            guild_response.status_code,
            guild_response.text,
            flush=True
        )

        return """
        <h1>❌ 認証失敗</h1>
        <p>Discordサーバー一覧を取得できませんでした。</p>
        """, 400

    try:

        user_guilds = guild_response.json()

    except Exception:

        return """
        <h1>❌ 認証失敗</h1>
        <p>サーバー一覧を解析できませんでした。</p>
        """, 500

    # ========================================================
    # BANサーバー所属チェック
    # ========================================================

    user_guild_ids = {
        guild.get("id")
        for guild in user_guilds
        if guild.get("id")
    }

    banned_guilds = user_guild_ids.intersection(
        BANNED_GUILD_IDS
    )

    if banned_guilds:

        banned_guild = next(iter(banned_guilds))

        print(
            f"🚫 OAuth拒否: "
            f"user={user_id}, "
            f"banned_guild={banned_guild}",
            flush=True
        )

        return """
        <!DOCTYPE html>
        <html lang="ja">
        <head>
            <meta charset="UTF-8">
            <title>認証拒否</title>
        </head>
        <body>
            <h1>❌ 認証できません</h1>
            <p>参加しているサーバーの関係で認証が拒否されました。</p>
        </body>
        </html>
        """, 403

    # ========================================================
    # Bot Token確認
    # ========================================================

    if not DISCORD_TOKEN:

        print(
            "❌ DISCORD_TOKENが設定されていません。",
            flush=True
        )

        return """
        <h1>❌ エラー</h1>
        <p>Bot設定が正しくありません。</p>
        """, 500

    # ========================================================
    # Role付与
    # ========================================================

    bot_headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}"
    }

    role_url = (
        f"https://discord.com/api/v10/"
        f"guilds/{guild_id}/members/{user_id}/roles/{role_id}"
    )

    try:

        role_response = requests.put(
            role_url,
            headers=bot_headers,
            timeout=15
        )

    except Exception as e:

        print(
            f"❌ Role付与リクエストエラー: {e}",
            flush=True
        )

        return """
        <h1>❌ 認証失敗</h1>
        <p>Discordへの接続に失敗しました。</p>
        """, 500

    # --------------------------------------------------------
    # 成功
    # --------------------------------------------------------

    if role_response.status_code == 204:

        print(
            f"✅ Role付与成功: "
            f"user={user_id}, "
            f"guild={guild_id}, "
            f"role={role_id}",
            flush=True
        )

        return """
        <!DOCTYPE html>
        <html lang="ja">
        <head>
            <meta charset="UTF-8">
            <title>認証成功</title>
        </head>
        <body>
            <h1>✅ 認証成功</h1>
            <p>ロールが付与されました。</p>
        </body>
        </html>
        """

    # --------------------------------------------------------
    # 失敗
    # --------------------------------------------------------

    print(
        f"❌ Role付与失敗: "
        f"status={role_response.status_code}, "
        f"text={role_response.text}",
        flush=True
    )

    return f"""
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <title>認証失敗</title>
    </head>
    <body>
        <h1>❌ 認証失敗</h1>
        <p>ロールを付与できませんでした。</p>
        <p>HTTP Status: {role_response.status_code}</p>
    </body>
    </html>
    """, 400


# ============================================================
# Discord API 429 詳細表示
# ============================================================

def print_429_details(error):

    print("")
    print("=" * 60)
    print("🚨 Discord API 429 詳細情報")
    print("=" * 60)

    print(
        f"HTTP Status: "
        f"{getattr(error, 'status', 'Unknown')}"
    )

    print(
        f"Exception Type: "
        f"{type(error).__name__}"
    )

    print(
        f"Exception: {error}"
    )

    response = getattr(
        error,
        "response",
        None
    )

    if response is not None:

        print("----- Discord Response -----")

        print(
            "Response Status:",
            getattr(response, "status", "Unknown")
        )

        print(
            "Response Method:",
            getattr(response, "method", "Unknown")
        )

        print(
            "Response URL:",
            getattr(response, "url", "Unknown")
        )

        print(
            "Response Reason:",
            getattr(response, "reason", "Unknown")
        )

        print("----- Response Headers -----")

        headers = getattr(
            response,
            "headers",
            None
        )

        if headers:

            for key, value in headers.items():

                key_lower = key.lower()

                if key_lower in {
                    "retry-after",
                    "x-ratelimit-global",
                    "x-ratelimit-limit",
                    "x-ratelimit-remaining",
                    "x-ratelimit-reset",
                    "x-ratelimit-reset-after",
                }:

                    print(
                        f"{key}: {value}"
                    )

    # --------------------------------------------------------
    # Discordエラーテキスト
    # --------------------------------------------------------

    error_text = getattr(
        error,
        "text",
        None
    )

    if error_text:

        print(
            "----- Discord Error Text -----"
        )

        print(
            error_text
        )

        try:

            import json

            parsed = json.loads(
                error_text
            )

            print(
                "----- Parsed JSON -----"
            )

            print(
                parsed
            )

        except Exception:

            print(
                "⚠️ エラーテキストをJSONとして解析できませんでした。"
            )

    # --------------------------------------------------------
    # discord.py retry_after
    # --------------------------------------------------------

    retry_after_attr = getattr(
        error,
        "retry_after",
        None
    )

    if retry_after_attr is not None:

        print(
            f"discord.py retry_after: "
            f"{retry_after_attr}"
        )

    print("=" * 60)
    print("")


# ============================================================
# DiscordのRetry-Afterを取得
# ============================================================

def get_retry_after(error):

    """
    Discordの429レスポンスからRetry-Afterを取得します。

    優先順位:

        1. Discord Response Header
        2. discord.py の retry_after
        3. None
    """

    # --------------------------------------------------------
    # Discord Response Header
    # --------------------------------------------------------

    response = getattr(
        error,
        "response",
        None
    )

    if response is not None:

        headers = getattr(
            response,
            "headers",
            None
        )

        if headers:

            for key, value in headers.items():

                if key.lower() == "retry-after":

                    try:

                        retry_after = float(
                            value
                        )

                        if retry_after >= 0:

                            return retry_after

                    except (
                        ValueError,
                        TypeError
                    ):

                        pass

    # --------------------------------------------------------
    # discord.py側
    # --------------------------------------------------------

    retry_after_attr = getattr(
        error,
        "retry_after",
        None
    )

    if retry_after_attr is not None:

        try:

            retry_after = float(
                retry_after_attr
            )

            if retry_after >= 0:

                return retry_after

        except (
            ValueError,
            TypeError
        ):

            pass

    return None


# ============================================================
# 秒数を見やすい形式に変換
# ============================================================

def format_wait_time(seconds):

    seconds = int(seconds)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60

    if hours > 0:

        return (
            f"{hours}時間 "
            f"{minutes}分 "
            f"{remaining_seconds}秒"
        )

    if minutes > 0:

        return (
            f"{minutes}分 "
            f"{remaining_seconds}秒"
        )

    return f"{remaining_seconds}秒"


# ============================================================
# Bot作成
# ============================================================

def create_bot():

    print(
        "🟣 create_bot()開始",
        flush=True
    )

    intents = discord.Intents.default()

    intents.message_content = True
    intents.voice_states = True
    intents.members = True

    print(
        "🟣 Discord Intents設定完了",
        flush=True
    )

    class MyBot(commands.Bot):

        def __init__(self):

            print(
                "🟣 MyBot.__init__()開始",
                flush=True
            )

            super().__init__(
                command_prefix="!",
                intents=intents
            )

            # =================================================
            # PostgreSQL Pool
            # =================================================

            self.db_pool = None

            # 旧Cog互換用
            #
            # 既存Cog:
            #     self.bot.pool
            #
            # 新しいCog:
            #     self.bot.db_pool
            #
            # の両方を使用できるようにする。
            self.pool = None

            print(
                "🟣 MyBot.__init__()完了",
                flush=True
            )

        # ====================================================
        # setup_hook
        # ====================================================

        async def setup_hook(self):

            print(
                "🔧 Bot setup_hook開始",
                flush=True
            )

            # ------------------------------------------------
            # PostgreSQL / Supabase
            # ------------------------------------------------

            if DATABASE_URL:

                try:

                    print(
                        "🗄️ PostgreSQLへ接続しています...",
                        flush=True
                    )

                    self.db_pool = (
                        await asyncpg.create_pool(
                            DATABASE_URL,
                            min_size=1,
                            max_size=5,
                            statement_cache_size=0
                        )
                    )

                    # =================================================
                    # 旧Cog互換
                    # =================================================
                    #
                    # self.bot.pool
                    # self.bot.db_pool
                    #
                    # のどちらでも同じPoolを使用する。
                    self.pool = self.db_pool

                    print(
                        "✅ PostgreSQL接続成功",
                        flush=True
                    )

                    print(
                        "🔗 DB Pool互換設定完了: "
                        "self.bot.pool / self.bot.db_pool",
                        flush=True
                    )

                except Exception as e:

                    print(
                        f"❌ PostgreSQL接続エラー: {e}",
                        flush=True
                    )

                    self.db_pool = None
                    self.pool = None

            else:

                print(
                    "⚠️ DATABASE_URLが設定されていません。",
                    flush=True
                )

                self.db_pool = None
                self.pool = None

            # ------------------------------------------------
            # Cogs
            # ------------------------------------------------

            cogs_path = "cogs"

            if os.path.isdir(cogs_path):

                loaded_count = 0

                print(
                    "📦 Cog読み込みを開始します...",
                    flush=True
                )

                for filename in sorted(
                    os.listdir(cogs_path)
                ):

                    if not filename.endswith(".py"):
                        continue

                    if filename.startswith("_"):
                        continue

                    extension = (
                        f"cogs."
                        f"{filename[:-3]}"
                    )

                    try:

                        print(
                            f"🔧 Cog読み込み中: "
                            f"{extension}",
                            flush=True
                        )

                        await self.load_extension(
                            extension
                        )

                        loaded_count += 1

                        print(
                            f"✅ Cog読み込み成功: "
                            f"{extension}",
                            flush=True
                        )

                    except Exception as e:

                        print(
                            f"❌ Cog読み込み失敗: "
                            f"{extension}",
                            flush=True
                        )

                        print(
                            f"   エラー: {e}",
                            flush=True
                        )

                print(
                    f"📦 Cog読み込み完了: "
                    f"{loaded_count}個",
                    flush=True
                )

            else:

                print(
                    "ℹ️ cogsフォルダがありません。",
                    flush=True
                )

            # ------------------------------------------------
            # Slash Commands同期
            # ------------------------------------------------

            try:

                print(
                    "🔄 Slash Commandを同期しています...",
                    flush=True
                )

                synced = await self.tree.sync()

                print(
                    f"✅ Slash Command同期完了: "
                    f"{len(synced)}個",
                    flush=True
                )

            except Exception as e:

                print(
                    f"❌ Slash Command同期失敗: {e}",
                    flush=True
                )

            print(
                "🔧 Bot setup_hook完了",
                flush=True
            )

        # ====================================================
        # Bot終了時
        # ====================================================

        async def close(self):

            print(
                "🔴 Discord Botを終了しています...",
                flush=True
            )

            if self.db_pool is not None:

                try:

                    await self.db_pool.close()

                    print(
                        "🗄️ PostgreSQL接続を終了しました。",
                        flush=True
                    )

                except Exception as e:

                    print(
                        f"⚠️ PostgreSQL終了エラー: {e}",
                        flush=True
                    )

            # =================================================
            # DB Pool参照をクリア
            # =================================================

            self.db_pool = None
            self.pool = None

            await super().close()

        # ====================================================
        # Guild参加
        # ====================================================

        async def on_guild_join(self, guild):

            print(
                f"➕ Guild参加: "
                f"{guild.name} "
                f"({guild.id})",
                flush=True
            )

            await self.update_status()

        # ====================================================
        # Guild退出
        # ====================================================

        async def on_guild_remove(self, guild):

            print(
                f"➖ Guild退出: "
                f"{guild.name} "
                f"({guild.id})",
                flush=True
            )

            await self.update_status()

        # ====================================================
        # ステータス更新
        # ====================================================

        async def update_status(self):

            try:

                server_count = len(
                    self.guilds
                )

                activity = discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"{server_count}個のサーバー"
                )

                await self.change_presence(
                    activity=activity
                )

                print(
                    f"🟢 Botステータス更新: "
                    f"{server_count}個のサーバー",
                    flush=True
                )

            except Exception as e:

                print(
                    f"⚠️ ステータス更新失敗: {e}",
                    flush=True
                )

        # ====================================================
        # Ready
        # ====================================================

        async def on_ready(self):

            print(
                "",
                flush=True
            )

            print(
                "=" * 60,
                flush=True
            )

            print(
                "🟢 Discord Bot Ready",
                flush=True
            )

            print(
                "=" * 60,
                flush=True
            )

            if self.user:

                print(
                    f"Bot: "
                    f"{self.user} "
                    f"({self.user.id})",
                    flush=True
                )

            print(
                f"Guild数: "
                f"{len(self.guilds)}",
                flush=True
            )

            print(
                "=" * 60,
                flush=True
            )

            print(
                "",
                flush=True
            )

            await self.update_status()

    print(
        "🟣 MyBotクラス定義完了",
        flush=True
    )

    bot = MyBot()

    print(
        "🟣 MyBotインスタンス作成完了",
        flush=True
    )

    return bot


# ============================================================
# Discord Bot 起動
# ============================================================

def run_discord_bot():

    if not DISCORD_TOKEN:

        print(
            "❌ DISCORD_TOKENが設定されていません。",
            flush=True
        )

        return

    print(
        "",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    print(
        "🔵 Discord Bot起動処理",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    print(
        "",
        flush=True
    )

    retry_count = 0

    # Retry-Afterが取得できなかった場合のみ使用する
    fallback_wait_times = [
        60,
        120,
        240,
        480,
        900,
    ]

    while True:

        bot = None

        try:

            retry_count += 1

            print(
                "🔵 Discord Botを起動しています...",
                flush=True
            )

            print(
                f"🔄 接続試行回数: {retry_count}",
                flush=True
            )

            # ------------------------------------------------
            # Botインスタンス作成
            # ------------------------------------------------

            print(
                "🟣 Botインスタンス作成開始...",
                flush=True
            )

            bot = create_bot()

            print(
                "🟣 Botインスタンス作成完了",
                flush=True
            )

            # ------------------------------------------------
            # Discord接続
            # ------------------------------------------------

            print(
                "🟣 bot.run()を開始します...",
                flush=True
            )

            bot.run(
                DISCORD_TOKEN
            )

            print(
                "🟣 bot.run()が終了しました",
                flush=True
            )

            # ------------------------------------------------
            # bot.run()正常終了
            # ------------------------------------------------

            print(
                "🟡 Discord Botが終了しました。",
                flush=True
            )

            break

        # ====================================================
        # Discord API 429
        # ====================================================

        except discord.HTTPException as e:

            if e.status == 429:

                print_429_details(e)

                # ------------------------------------------------
                # Discord指定のRetry-After取得
                # ------------------------------------------------

                retry_after = get_retry_after(e)

                if retry_after is not None:

                    wait_time = max(
                        1,
                        math.ceil(retry_after)
                    )

                    print(
                        "⚠️ Discord APIが429 "
                        "Rate Limitを返しました。",
                        flush=True
                    )

                    print(
                        f"📡 Discord指定 "
                        f"Retry-After: "
                        f"{retry_after}秒",
                        flush=True
                    )

                    print(
                        f"⏳ 待機時間: "
                        f"{wait_time}秒",
                        flush=True
                    )

                    print(
                        f"⏰ 約 "
                        f"{format_wait_time(wait_time)} "
                        f"待ってから再接続します。",
                        flush=True
                    )

                else:

                    index = min(
                        retry_count - 1,
                        len(fallback_wait_times) - 1
                    )

                    wait_time = fallback_wait_times[index]

                    print(
                        "⚠️ Discord APIが429 "
                        "Rate Limitを返しました。",
                        flush=True
                    )

                    print(
                        "⚠️ Retry-Afterを取得できなかったため、"
                        "フォールバック待機時間を使用します。",
                        flush=True
                    )

                    print(
                        f"⏳ 待機時間: "
                        f"{wait_time}秒",
                        flush=True
                    )

                    print(
                        f"⏰ 約 "
                        f"{format_wait_time(wait_time)} "
                        f"待ってから再接続します。",
                        flush=True
                    )

                print(
                    "🔄 待機開始...",
                    flush=True
                )

                time.sleep(
                    wait_time
                )

                print(
                    "✅ 待機終了。",
                    flush=True
                )

                print(
                    "🔄 新しいBotインスタンスで"
                    "Discordへ再接続します。",
                    flush=True
                )

                print(
                    "",
                    flush=True
                )

                continue

            # ------------------------------------------------
            # 429以外
            # ------------------------------------------------

            print(
                "❌ Discord HTTPException:",
                flush=True
            )

            print(
                e,
                flush=True
            )

            break

        # ====================================================
        # Tokenなどのログインエラー
        # ====================================================

        except discord.LoginFailure as e:

            print(
                "❌ Discord LoginFailure:",
                flush=True
            )

            print(
                e,
                flush=True
            )

            print(
                "⚠️ DISCORD_TOKENが正しいか確認してください。",
                flush=True
            )

            break

        # ====================================================
        # その他の例外
        # ====================================================

        except Exception as e:

            print(
                "❌ Bot起動エラー:",
                flush=True
            )

            print(
                f"Exception Type: "
                f"{type(e).__name__}",
                flush=True
            )

            print(
                f"Exception: {e}",
                flush=True
            )

            print(
                "⏳ 30秒後にBotを再起動します...",
                flush=True
            )

            time.sleep(
                30
            )

            print(
                "🔄 Botを再起動します。",
                flush=True
            )

            print(
                "",
                flush=True
            )

        finally:

            bot = None


# ============================================================
# Flask起動
# ============================================================

def run_flask():

    print(
        f"🌐 Flaskを起動します。"
        f" Port={PORT}",
        flush=True
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


# ============================================================
# メイン
# ============================================================

def main():

    print(
        "",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    print(
        f"🚀 {PROJECT_NAME} 起動",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    print(
        "",
        flush=True
    )

    # --------------------------------------------------------
    # Flaskは別スレッドで起動
    # --------------------------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    print(
        "🌐 Flaskスレッドを起動しました。",
        flush=True
    )

    # --------------------------------------------------------
    # Bot起動
    # --------------------------------------------------------

    if IS_PRIMARY_INSTANCE:

        print(
            "🔵 このプロセスをDiscord Botの"
            "Primary Instanceとして使用します。",
            flush=True
        )

        run_discord_bot()

    else:

        print(
            "⚠️ Primary Instanceではないため、"
            "Discord Botは起動しません。",
            flush=True
        )

        while True:

            time.sleep(
                3600
            )


# ============================================================
# 実行
# ============================================================

if __name__ == "__main__":

    main()
