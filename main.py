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

app = Flask(__name__)

# Renderなどの環境では環境変数を設定することを推奨
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    os.urandom(32)
)


# ============================================================
# OAuthでアクセスを拒否するサーバー
# ============================================================

BANNED_GUILD_IDS = {
    "1392780216241491968",
    "1541042102152986664",
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

    print("🔒 【排他制御】ロック取得成功。Botを起動します。")

except BlockingIOError:
    print("⚠️ 【排他制御】別のBotプロセスが起動中です。")
    IS_PRIMARY_INSTANCE = False

except Exception as e:
    print(f"⚠️ 【排他制御】ロック処理でエラー: {e}")
    IS_PRIMARY_INSTANCE = False


# ============================================================
# Flask
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

    # セッションに対象を保存
    session["guild_id"] = guild_id
    session["role_id"] = role_id

    # --------------------------------------------------------
    # OAuth CSRF対策用のランダムstate
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
        <h1>認証失敗</h1>
        <p>OAuth stateが一致しません。</p>
        """, 400

    if not code:
        return """
        <h1>認証失敗</h1>
        <p>認証コードがありません。</p>
        """, 400

    # --------------------------------------------------------
    # 対象Guild / Role
    # --------------------------------------------------------

    guild_id = session.get("guild_id")
    role_id = session.get("role_id")

    if not guild_id or not role_id:
        return """
        <h1>認証失敗</h1>
        <p>対象サーバー情報がありません。</p>
        """, 400

    # --------------------------------------------------------
    # Discord OAuth2 Token取得
    # --------------------------------------------------------

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

        print(f"❌ OAuth token request error: {e}")

        return """
        <h1>認証失敗</h1>
        <p>Discordへの接続に失敗しました。</p>
        """, 500

    if token_response.status_code != 200:

        print(
            "❌ OAuth token取得失敗:",
            token_response.status_code,
            token_response.text
        )

        return """
        <h1>認証失敗</h1>
        <p>Discord OAuth2トークンを取得できませんでした。</p>
        """, 400

    try:
        token_json = token_response.json()

    except Exception:

        return """
        <h1>認証失敗</h1>
        <p>Discordから不正なレスポンスが返されました。</p>
        """, 500

    access_token = token_json.get("access_token")

    if not access_token:
        return """
        <h1>認証失敗</h1>
        <p>アクセストークンが取得できませんでした。</p>
        """, 400

    # --------------------------------------------------------
    # ユーザー情報
    # --------------------------------------------------------

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

        print(f"❌ User API request error: {e}")

        return """
        <h1>認証失敗</h1>
        <p>Discordユーザー情報の取得に失敗しました。</p>
        """, 500

    if user_response.status_code != 200:

        print(
            "❌ ユーザー情報取得失敗:",
            user_response.status_code,
            user_response.text
        )

        return """
        <h1>認証失敗</h1>
        <p>Discordユーザー情報を取得できませんでした。</p>
        """, 400

    try:
        user_data = user_response.json()

    except Exception:

        return """
        <h1>認証失敗</h1>
        <p>ユーザー情報を解析できませんでした。</p>
        """, 500

    user_id = user_data.get("id")

    if not user_id:
        return """
        <h1>認証失敗</h1>
        <p>ユーザーIDを取得できませんでした。</p>
        """, 400

    # --------------------------------------------------------
    # ユーザーが参加しているGuild一覧
    # --------------------------------------------------------

    try:

        guild_response = requests.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers=headers,
            timeout=15
        )

    except Exception as e:

        print(f"❌ Guild API request error: {e}")

        return """
        <h1>認証失敗</h1>
        <p>Discordサーバー情報の取得に失敗しました。</p>
        """, 500

    if guild_response.status_code != 200:

        print(
            "❌ Guild一覧取得失敗:",
            guild_response.status_code,
            guild_response.text
        )

        return """
        <h1>認証失敗</h1>
        <p>Discordサーバー一覧を取得できませんでした。</p>
        """, 400

    try:
        user_guilds = guild_response.json()

    except Exception:

        return """
        <h1>認証失敗</h1>
        <p>サーバー一覧を解析できませんでした。</p>
        """, 500

    # --------------------------------------------------------
    # BANサーバー所属チェック
    # --------------------------------------------------------

    user_guild_ids = {
        guild.get("id")
        for guild in user_guilds
        if guild.get("id")
    }

    banned_guild = user_guild_ids.intersection(
        BANNED_GUILD_IDS
    )

    if banned_guild:

        print(
            f"🚫 OAuth拒否: "
            f"user={user_id}, "
            f"banned_guild={next(iter(banned_guild))}"
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

    # --------------------------------------------------------
    # Bot Tokenを使用してRole付与
    # --------------------------------------------------------

    if not DISCORD_TOKEN:

        print("❌ DISCORD_TOKENが設定されていません。")

        return """
        <h1>エラー</h1>
        <p>Bot設定が正しくありません。</p>
        """, 500

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

        print(f"❌ Role付与リクエストエラー: {e}")

        return """
        <h1>認証失敗</h1>
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
            f"role={role_id}"
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
    # Role付与失敗
    # --------------------------------------------------------

    print(
        f"❌ Role付与失敗: "
        f"status={role_response.status_code}, "
        f"text={role_response.text}"
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
# 429 詳細表示
# ============================================================

def print_429_details(error):

    print("")
    print("=" * 60)
    print("🚨 Discord API 429 詳細情報")
    print("=" * 60)

    print(f"HTTP Status: {getattr(error, 'status', 'Unknown')}")
    print(f"Exception Type: {type(error).__name__}")
    print(f"Exception: {error}")

    response = getattr(error, "response", None)

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

        headers = getattr(response, "headers", None)

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

                    print(f"{key}: {value}")

    # --------------------------------------------------------
    # Exception text
    # --------------------------------------------------------

    error_text = getattr(error, "text", None)

    if error_text:

        print("----- Discord Error Text -----")
        print(error_text)

        # DiscordがJSONではなくプレーンテキストを
        # 返す場合もあるため、JSON解析は必須にしない
        try:

            import json

            parsed = json.loads(error_text)

            print("----- Parsed JSON -----")
            print(parsed)

        except Exception:

            print(
                "⚠️ エラーテキストをJSONとして解析できませんでした。"
            )

    # --------------------------------------------------------
    # discord.py側のretry_after
    # --------------------------------------------------------

    retry_after_attr = getattr(
        error,
        "retry_after",
        None
    )

    if retry_after_attr is not None:

        print(
            f"discord.py retry_after: {retry_after_attr}"
        )

    print("=" * 60)
    print("")


# ============================================================
# 429 Retry-After取得
# ============================================================

def get_retry_after(error):

    """
    Discordの429レスポンスからRetry-Afterを取得する。

    優先順位:
        1. Discord Response Header
        2. discord.py HTTPException.retry_after
        3. None
    """

    # --------------------------------------------------------
    # Response Header
    # --------------------------------------------------------

    response = getattr(error, "response", None)

    if response is not None:

        headers = getattr(response, "headers", None)

        if headers:

            # aiohttpのヘッダーは基本的に
            # 大文字小文字を区別しないが、
            # 念のため手動検索する
            for key, value in headers.items():

                if key.lower() == "retry-after":

                    try:

                        retry_after = float(value)

                        if retry_after >= 0:
                            return retry_after

                    except (ValueError, TypeError):

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

            retry_after = float(retry_after_attr)

            if retry_after >= 0:
                return retry_after

        except (ValueError, TypeError):

            pass

    return None


# ============================================================
# Bot作成
# ============================================================

def create_bot():

    intents = discord.Intents.default()

    intents.message_content = True
    intents.voice_states = True
    intents.members = True

    class MyBot(commands.Bot):

        def __init__(self):

            super().__init__(
                command_prefix="!",
                intents=intents
            )

            self.db_pool = None

        async def setup_hook(self):

            print("🔧 Bot setup_hook開始")

            # ------------------------------------------------
            # PostgreSQL / Supabase
            # ------------------------------------------------

            if DATABASE_URL:

                try:

                    print("🗄️ PostgreSQLへ接続しています...")

                    self.db_pool = await asyncpg.create_pool(
                        DATABASE_URL,
                        min_size=1,
                        max_size=5,
                        statement_cache_size=0
                    )

                    print("✅ PostgreSQL接続成功")

                except Exception as e:

                    print(
                        f"❌ PostgreSQL接続エラー: {e}"
                    )

                    self.db_pool = None

            else:

                print(
                    "⚠️ DATABASE_URLが設定されていません。"
                )

            # ------------------------------------------------
            # Cogs
            # ------------------------------------------------

            cogs_path = "cogs"

            if
