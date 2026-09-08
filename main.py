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
# 認証画面HTML
# ============================================================

def auth_page(
    title,
    heading,
    description,
    icon="✅",
    success=True,
    status_code=200,
    username=None
):

    if success:
        theme_class = "success"
        status_text = "AUTHENTICATION SUCCESS"
    else:
        theme_class = "error"
        status_text = "AUTHENTICATION ERROR"

    username_html = ""

    if username:
        username_html = f"""
        <div class="user-box">
            <div class="user-icon">👤</div>
            <div>
                <div class="user-label">認証ユーザー</div>
                <div class="username">
                    {username}
                </div>
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="ja">
    <head>

        <meta charset="UTF-8">

        <meta
            name="viewport"
            content="width=device-width, initial-scale=1.0"
        >

        <meta
            name="theme-color"
            content="#5865f2"
        >

        <title>{title}</title>

        <style>

            * {{
                box-sizing: border-box;
            }}

            html,
            body {{
                margin: 0;
                padding: 0;
                width: 100%;
                min-height: 100%;
            }}

            body {{
                min-height: 100vh;

                display: flex;
                justify-content: center;
                align-items: center;

                padding: 24px;

                font-family:
                    -apple-system,
                    BlinkMacSystemFont,
                    "Segoe UI",
                    "Noto Sans JP",
                    sans-serif;

                color: #f2f3f5;

                background:
                    radial-gradient(
                        circle at 20% 20%,
                        rgba(88, 101, 242, 0.22),
                        transparent 35%
                    ),
                    radial-gradient(
                        circle at 80% 80%,
                        rgba(114, 137, 218, 0.15),
                        transparent 35%
                    ),
                    #0f1117;

                overflow-x: hidden;
            }}

            /* 背景のぼかし円 */

            body::before {{
                content: "";

                position: fixed;

                width: 320px;
                height: 320px;

                border-radius: 50%;

                background: rgba(88, 101, 242, 0.12);

                filter: blur(80px);

                top: -120px;
                right: -100px;

                pointer-events: none;
            }}

            body::after {{
                content: "";

                position: fixed;

                width: 280px;
                height: 280px;

                border-radius: 50%;

                background: rgba(87, 242, 135, 0.07);

                filter: blur(80px);

                bottom: -100px;
                left: -100px;

                pointer-events: none;
            }}

            .container {{
                width: 100%;
                max-width: 500px;

                position: relative;
                z-index: 1;

                animation:
                    cardAppear
                    0.55s
                    cubic-bezier(.2,.8,.2,1)
                    forwards;
            }}

            @keyframes cardAppear {{
                from {{
                    opacity: 0;
                    transform:
                        translateY(25px)
                        scale(0.97);
                }}

                to {{
                    opacity: 1;
                    transform:
                        translateY(0)
                        scale(1);
                }}
            }}

            .card {{
                position: relative;

                padding: 42px 36px 36px;

                border-radius: 24px;

                background:
                    linear-gradient(
                        145deg,
                        rgba(35, 39, 49, 0.96),
                        rgba(22, 25, 32, 0.96)
                    );

                border:
                    1px solid
                    rgba(255,255,255,0.08);

                box-shadow:
                    0 30px 80px
                    rgba(0,0,0,0.45),

                    0 8px 30px
                    rgba(0,0,0,0.25);

                backdrop-filter: blur(20px);

                text-align: center;
            }}

            .brand {{
                display: flex;

                justify-content: center;
                align-items: center;

                gap: 10px;

                margin-bottom: 28px;

                font-size: 15px;

                color: #b5bac1;

                font-weight: 600;
            }}

            .brand-icon {{
                width: 34px;
                height: 34px;

                display: flex;
                align-items: center;
                justify-content: center;

                border-radius: 10px;

                background: #5865f2;

                font-size: 18px;

                box-shadow:
                    0 8px 20px
                    rgba(88,101,242,0.35);
            }}

            .icon {{
                width: 92px;
                height: 92px;

                margin: 0 auto 24px;

                display: flex;
                justify-content: center;
                align-items: center;

                border-radius: 50%;

                font-size: 42px;

                animation:
                    iconPop
                    0.55s
                    0.15s
                    both;
            }}

            @keyframes iconPop {{
                from {{
                    opacity: 0;
                    transform: scale(0.5);
                }}

                70% {{
                    transform: scale(1.08);
                }}

                to {{
                    opacity: 1;
                    transform: scale(1);
                }}
            }}

            .success .icon {{
                background:
                    rgba(87,242,135,0.12);

                border:
                    1px solid
                    rgba(87,242,135,0.25);

                box-shadow:
                    0 0 45px
                    rgba(87,242,135,0.12);
            }}

            .error .icon {{
                background:
                    rgba(237,66,69,0.12);

                border:
                    1px solid
                    rgba(237,66,69,0.25);

                box-shadow:
                    0 0 45px
                    rgba(237,66,69,0.12);
            }}

            .status {{
                display: inline-block;

                margin-bottom: 14px;

                padding: 6px 12px;

                border-radius: 999px;

                font-size: 10px;

                letter-spacing: 1.5px;

                font-weight: 700;
            }}

            .success .status {{
                color: #57f287;

                background:
                    rgba(87,242,135,0.09);
            }}

            .error .status {{
                color: #ed4245;

                background:
                    rgba(237,66,69,0.09);
            }}

            h1 {{
                margin: 0 0 14px;

                font-size: 28px;

                line-height: 1.3;

                font-weight: 750;

                letter-spacing: -0.5px;
            }}

            .description {{
                margin: 0 auto;

                max-width: 390px;

                color: #b5bac1;

                font-size: 14px;

                line-height: 1.8;
            }}

            .user-box {{
                margin-top: 28px;

                padding: 14px 16px;

                display: flex;

                align-items: center;

                text-align: left;

                gap: 12px;

                border-radius: 14px;

                background:
                    rgba(255,255,255,0.04);

                border:
                    1px solid
                    rgba(255,255,255,0.06);
            }}

            .user-icon {{
                width: 40px;
                height: 40px;

                display: flex;
                justify-content: center;
                align-items: center;

                border-radius: 50%;

                background:
                    rgba(88,101,242,0.18);

                font-size: 18px;
            }}

            .user-label {{
                color: #949ba4;

                font-size: 11px;

                margin-bottom: 3px;
            }}

            .username {{
                color: #f2f3f5;

                font-size: 14px;

                font-weight: 650;

                word-break: break-all;
            }}

            .divider {{
                height: 1px;

                margin: 28px 0 22px;

                background:
                    rgba(255,255,255,0.07);
            }}

            .footer {{
                color: #72767d;

                font-size: 11px;

                line-height: 1.7;
            }}

            .footer strong {{
                color: #949ba4;
            }}

            .close-hint {{
                margin-top: 18px;

                font-size: 12px;

                color: #72767d;
            }}

            @media (max-width: 600px) {{

                body {{
                    padding: 16px;
                }}

                .card {{
                    padding: 34px 22px 28px;

                    border-radius: 20px;
                }}

                .icon {{
                    width: 80px;
                    height: 80px;

                    font-size: 36px;
                }}

                h1 {{
                    font-size: 24px;
                }}

                .description {{
                    font-size: 13px;
                }}

            }}

        </style>

    </head>

    <body>

        <main class="container">

            <section class="card {theme_class}">

                <div class="brand">

                    <div class="brand-icon">
                        🤖
                    </div>

                    <span>
                        {PROJECT_NAME}
                    </span>

                </div>

                <div class="icon">
                    {icon}
                </div>

                <div class="status">
                    {status_text}
                </div>

                <h1>
                    {heading}
                </h1>

                <p class="description">
                    {description}
                </p>

                {username_html}

                <div class="divider"></div>

                <div class="footer">
                    <strong>Discord OAuth2</strong><br>
                    安全な認証処理が完了しました。
                </div>

                <div class="close-hint">
                    このページを閉じても問題ありません。
                </div>

            </section>

        </main>

    </body>
    </html>
    """, status_code


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

        return auth_page(
            "認証エラー",
            "認証できません",
            "認証情報が指定されていません。",
            icon="❌",
            success=False,
            status_code=400
        )

    # --------------------------------------------------------
    # GUILD_ID_ROLE_ID
    # --------------------------------------------------------

    parts = state_parameter.split("_")

    if len(parts) != 2:

        return auth_page(
            "認証エラー",
            "認証情報が不正です",
            "サーバーまたはロールの情報を確認できませんでした。",
            icon="❌",
            success=False,
            status_code=400
        )

    guild_id, role_id = parts

    if not guild_id.isdigit() or not role_id.isdigit():

        return auth_page(
            "認証エラー",
            "認証情報が不正です",
            "サーバーIDまたはロールIDが正しくありません。",
            icon="❌",
            success=False,
            status_code=400
        )

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

        return auth_page(
            "認証失敗",
            "認証に失敗しました",
            "OAuth stateが一致しません。\n"
            "もう一度認証をやり直してください。",
            icon="❌",
            success=False,
            status_code=400
        )

    if not code:

        return auth_page(
            "認証失敗",
            "認証コードがありません",
            "Discordから認証コードを受け取れませんでした。",
            icon="❌",
            success=False,
            status_code=400
        )

    # --------------------------------------------------------
    # 対象Guild / Role
    # --------------------------------------------------------

    guild_id = session.get("guild_id")
    role_id = session.get("role_id")

    if not guild_id or not role_id:

        return auth_page(
            "認証失敗",
            "サーバー情報がありません",
            "対象サーバーまたはロールの情報を確認できませんでした。",
            icon="❌",
            success=False,
            status_code=400
        )

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

        return auth_page(
            "認証失敗",
            "Discordへの接続に失敗しました",
            "Discordとの通信中にエラーが発生しました。\n"
            "しばらく待ってからもう一度お試しください。",
            icon="❌",
            success=False,
            status_code=500
        )

    if token_response.status_code != 200:

        print(
            "❌ OAuth token取得失敗:",
            token_response.status_code,
            token_response.text,
            flush=True
        )

        return auth_page(
            "認証失敗",
            "OAuth2認証に失敗しました",
            "Discord OAuth2トークンを取得できませんでした。",
            icon="❌",
            success=False,
            status_code=400
        )

    try:

        token_json = token_response.json()

    except Exception:

        return auth_page(
            "認証失敗",
            "不正なレスポンスです",
            "Discordから不正なレスポンスが返されました。",
            icon="❌",
            success=False,
            status_code=500
        )

    access_token = token_json.get("access_token")

    if not access_token:

        return auth_page(
            "認証失敗",
            "アクセストークンを取得できませんでした",
            "Discordからアクセストークンを取得できませんでした。",
            icon="❌",
            success=False,
            status_code=400
        )

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

        return auth_page(
            "認証失敗",
            "ユーザー情報を取得できませんでした",
            "Discordユーザー情報の取得中にエラーが発生しました。",
            icon="❌",
            success=False,
            status_code=500
        )

    if user_response.status_code != 200:

        print(
            "❌ ユーザー情報取得失敗:",
            user_response.status_code,
            user_response.text,
            flush=True
        )

        return auth_page(
            "認証失敗",
            "ユーザー情報を取得できませんでした",
            "Discordユーザー情報を取得できませんでした。",
            icon="❌",
            success=False,
            status_code=400
        )

    try:

        user_data = user_response.json()

    except Exception:

        return auth_page(
            "認証失敗",
            "ユーザー情報を解析できませんでした",
            "Discordから受け取ったユーザー情報を解析できませんでした。",
            icon="❌",
            success=False,
            status_code=500
        )

    user_id = user_data.get("id")

    if not user_id:

        return auth_page(
            "認証失敗",
            "ユーザーIDを取得できませんでした",
            "DiscordユーザーIDを取得できませんでした。",
            icon="❌",
            success=False,
            status_code=400
        )

    # --------------------------------------------------------
    # 表示用ユーザー名
    # --------------------------------------------------------

    username = user_data.get("global_name")

    if not username:

        username = user_data.get("username")

    if not username:

        username = "Discord User"

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

        return auth_page(
            "認証失敗",
            "サーバー情報を取得できませんでした",
            "Discordサーバー情報の取得中にエラーが発生しました。",
            icon="❌",
            success=False,
            status_code=500
        )

    if guild_response.status_code != 200:

        print(
            "❌ Guild一覧取得失敗:",
            guild_response.status_code,
            guild_response.text,
            flush=True
        )

        return auth_page(
            "認証失敗",
            "サーバー一覧を取得できませんでした",
            "Discordサーバー一覧を取得できませんでした。",
            icon="❌",
            success=False,
            status_code=400
        )

    try:

        user_guilds = guild_response.json()

    except Exception:

        return auth_page(
            "認証失敗",
            "サーバー一覧を解析できませんでした",
            "Discordから受け取ったサーバー一覧を解析できませんでした。",
            icon="❌",
            success=False,
            status_code=500
        )

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

        return auth_page(
            "認証拒否",
            "認証できません",
            "参加しているサーバーの関係で認証が拒否されました。",
            icon="🚫",
            success=False,
            status_code=403
        )

    # ========================================================
    # Bot Token確認
    # ========================================================

    if not DISCORD_TOKEN:

        print(
            "❌ DISCORD_TOKENが設定されていません。",
            flush=True
        )

        return auth_page(
            "システムエラー",
            "Bot設定に問題があります",
            "Bot設定が正しくありません。",
            icon="⚠️",
            success=False,
            status_code=500
        )

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

        return auth_page(
            "認証失敗",
            "ロールを付与できませんでした",
            "Discordへの接続中にエラーが発生しました。",
            icon="❌",
            success=False,
            status_code=500
        )

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

        return auth_page(
            "認証成功",
            "認証が完了しました！",
            "Discordアカウントの認証に成功し、"
            "指定されたロールが付与されました。",
            icon="✓",
            success=True,
            status_code=200,
            username=username
        )

    # --------------------------------------------------------
    # 失敗
    # --------------------------------------------------------

    print(
        f"❌ Role付与失敗: "
        f"status={role_response.status_code}, "
        f"text={role_response.text}",
        flush=True
    )

    return auth_page(
        "認証失敗",
        "ロールを付与できませんでした",
        "Discord側でロールの付与に失敗しました。",
        icon="❌",
        success=False,
        status_code=400
    )


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

                        retry_after = float(value)

                        if retry_after >= 0:

                            return retry_after

                    except (
                        ValueError,
                        TypeError
                    ):

                        pass

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

            self.db_pool = None

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

            print(
                "🟣 Botインスタンス作成開始...",
                flush=True
            )

            bot = create_bot()

            print(
                "🟣 Botインスタンス作成完了",
                flush=True
            )

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

            print(
                "🟡 Discord Botが終了しました。",
                flush=True
            )

            break

        except discord.HTTPException as e:

            if e.status == 429:

                print_429_details(e)

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

            print(
                "❌ Discord HTTPException:",
                flush=True
            )

            print(
                e,
                flush=True
            )

            break

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
