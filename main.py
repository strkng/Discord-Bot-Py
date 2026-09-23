import fcntl
import logging
import os
import secrets
import threading
import time
import urllib.parse
import html

import asyncpg
import discord
from discord.ext import commands

from flask import Flask, request, redirect, session
import requests


# =========================================================
# Environment Variables
# =========================================================

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
REDIRECT_URI = os.environ.get("REDIRECT_URI")

DATABASE_URL = os.environ.get("DATABASE_URL")

PORT = int(os.environ.get("PORT", "10000"))


# =========================================================
# Logging
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# Flask
# =========================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    os.urandom(32)
)


# =========================================================
# Banned Guilds
# =========================================================

BANNED_GUILDS = {
    "1392780216241491968",
    "1122334455667788990",
}


# =========================================================
# Discord OAuth URLs
# =========================================================

DISCORD_OAUTH_AUTHORIZE = (
    "https://discord.com/oauth2/authorize"
)

DISCORD_OAUTH_TOKEN = (
    "https://discord.com/api/oauth2/token"
)

DISCORD_API = (
    "https://discord.com/api/v10"
)


# =========================================================
# Discord REST API Rate Limit Helper
# =========================================================

DISCORD_REQUEST_MAX_RETRIES = 5

DISCORD_REQUEST_FALLBACK_DELAYS = [
    5,
    15,
    30,
    60,
    120,
]


def get_http_retry_after(response):
    """
    Discord REST APIの429レスポンスからRetry-Afterを取得します。

    優先順位:
    1. Retry-After HTTPヘッダー
    2. JSONの retry_after
    3. None
    """

    try:
        value = response.headers.get("Retry-After")

        if value is not None:
            return max(float(value), 0.0)

    except (TypeError, ValueError):
        pass

    try:
        data = response.json()

        value = data.get("retry_after")

        if value is not None:
            return max(float(value), 0.0)

    except (TypeError, ValueError, AttributeError):
        pass

    return None


def discord_rest_request(
    method,
    url,
    *,
    max_retries=DISCORD_REQUEST_MAX_RETRIES,
    **kwargs
):
    """
    Discord REST APIへのHTTPリクエストを行います。

    429の場合はDiscordのRetry-Afterを尊重して再試行します。
    Retry-Afterが取得できない場合はフォールバック待機時間を使います。

    ネットワークエラーはrequests.RequestExceptionとして呼び出し元へ返します。
    """

    last_response = None

    for attempt in range(max_retries + 1):

        try:
            response = requests.request(
                method,
                url,
                **kwargs
            )

        except requests.RequestException:
            raise

        last_response = response

        if response.status_code != 429:
            return response

        retry_after = get_http_retry_after(
            response
        )

        if retry_after is None:

            index = min(
                attempt,
                len(DISCORD_REQUEST_FALLBACK_DELAYS) - 1
            )

            retry_after = DISCORD_REQUEST_FALLBACK_DELAYS[index]

        try:
            response_json = response.json()
        except (ValueError, TypeError):
            response_json = {}

        is_global = bool(
            response_json.get("global", False)
        )

        logger.warning(
            "Discord REST API rate limited: "
            "method=%s url=%s status=429 global=%s "
            "retry_after=%.1f attempt=%s/%s",
            method,
            url,
            is_global,
            retry_after,
            attempt + 1,
            max_retries + 1,
        )

        if attempt >= max_retries:

            logger.error(
                "Discord REST API 429 retry limit reached: "
                "method=%s url=%s",
                method,
                url,
            )

            return response

        time.sleep(
            retry_after
        )

    return last_response


# =========================================================
# Discord Icon
# =========================================================

DISCORD_ICON_URL = (
    "https://raw.githubusercontent.com/strkng/Discord-Bot-Py/"
    "refs/heads/main/Discord_icon.png"
)


# =========================================================
# Authentication Result Page
# =========================================================

def auth_page(
    title,
    heading,
    message,
    success=True,
    status_code=200
):
    """
    OAuth認証結果を表示するHTMLページ。
    """

    if success:

        accent = "#57F287"
        accent_dark = "#3BA55D"
        icon_shadow = "rgba(87, 242, 135, 0.30)"

        status_text = "認証成功"
        status_icon = "✓"

    else:

        accent = "#ED4245"
        accent_dark = "#A12D2F"
        icon_shadow = "rgba(237, 66, 69, 0.30)"

        status_text = "認証エラー"
        status_icon = "!"

    safe_title = html.escape(
        str(title)
    )

    safe_heading = html.escape(
        str(heading)
    )

    safe_message = html.escape(
        str(message)
    )

    return f"""<!DOCTYPE html>
<html lang="ja">

<head>

    <meta charset="UTF-8">

    <meta
        name="viewport"
        content="width=device-width, initial-scale=1.0"
    >

    <title>{safe_title}</title>

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

            align-items: center;
            justify-content: center;

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
                    circle at top,
                    #313338 0%,
                    #1e1f22 45%,
                    #111214 100%
                );

            overflow-x: hidden;
        }}

        body::before {{

            content: "";

            position: fixed;

            inset: 0;

            pointer-events: none;

            background:

                radial-gradient(
                    circle at 20% 20%,
                    rgba(88, 101, 242, 0.15),
                    transparent 30%
                ),

                radial-gradient(
                    circle at 80% 80%,
                    rgba(87, 242, 135, 0.08),
                    transparent 30%
                );
        }}

        .container {{

            position: relative;

            z-index: 1;

            width: 100%;

            max-width: 520px;

            animation:
                cardIn 0.55s ease-out;
        }}

        .card {{

            position: relative;

            padding: 42px 36px 38px;

            text-align: center;

            background:
                rgba(30, 31, 34, 0.92);

            border:
                1px solid rgba(255, 255, 255, 0.08);

            border-radius: 24px;

            box-shadow:

                0 25px 70px
                rgba(0, 0, 0, 0.45),

                0 0 0 1px
                rgba(255, 255, 255, 0.02);

            backdrop-filter: blur(18px);

            -webkit-backdrop-filter:
                blur(18px);
        }}


        /* =================================================
           Discord Icon
           ================================================= */

        .icon {{

            width: 120px;

            height: 100px;

            margin: 0 auto 24px;

            display: flex;

            align-items: center;

            justify-content: center;

            background: transparent;

            border-radius: 0;

            overflow: visible;

            filter:
                drop-shadow(
                    0 12px 25px
                    {icon_shadow}
                );

            animation:
                iconIn 0.65s ease-out;
        }}

        .icon img {{

            /*
             * coverではなくcontainにすることで
             * Discordロゴ全体を表示します。
             */

            width: 100%;

            height: 100%;

            display: block;

            object-fit: contain;

            object-position: center;
        }}


        /* =================================================
           Status
           ================================================= */

        .status {{

            display: inline-flex;

            align-items: center;

            justify-content: center;

            gap: 7px;

            padding: 7px 13px;

            margin-bottom: 18px;

            border-radius: 999px;

            color: {accent};

            background:
                rgba(255, 255, 255, 0.045);

            border:
                1px solid
                rgba(255, 255, 255, 0.08);

            font-size: 13px;

            font-weight: 700;

            letter-spacing: 0.02em;
        }}

        .status-icon {{

            width: 18px;

            height: 18px;

            display: inline-flex;

            align-items: center;

            justify-content: center;

            border-radius: 50%;

            color: #ffffff;

            background: {accent_dark};

            font-size: 12px;

            font-weight: 800;
        }}


        /* =================================================
           Heading
           ================================================= */

        h1 {{

            margin: 0 0 16px;

            font-size: 28px;

            line-height: 1.35;

            font-weight: 750;

            color: #ffffff;

            letter-spacing: -0.02em;
        }}


        /* =================================================
           Message
           ================================================= */

        .message {{

            margin: 0;

            color: #b5bac1;

            font-size: 15px;

            line-height: 1.8;

            word-break: break-word;
        }}


        /* =================================================
           Divider
           ================================================= */

        .divider {{

            width: 100%;

            height: 1px;

            margin: 30px 0 22px;

            background:
                linear-gradient(
                    to right,
                    transparent,
                    rgba(255,255,255,0.10),
                    transparent
                );
        }}


        /* =================================================
           Footer
           ================================================= */

        .footer {{

            color: #72767d;

            font-size: 12px;

            line-height: 1.6;
        }}

        .brand {{

            color: #949ba4;

            font-weight: 600;
        }}


        /* =================================================
           Animations
           ================================================= */

        @keyframes cardIn {{

            from {{

                opacity: 0;

                transform:
                    translateY(18px)
                    scale(0.98);
            }}

            to {{

                opacity: 1;

                transform:
                    translateY(0)
                    scale(1);
            }}
        }}

        @keyframes iconIn {{

            from {{

                opacity: 0;

                transform:
                    scale(0.75);
            }}

            70% {{

                transform:
                    scale(1.05);
            }}

            to {{

                opacity: 1;

                transform:
                    scale(1);
            }}
        }}


        /* =================================================
           Mobile
           ================================================= */

        @media (max-width: 600px) {{

            body {{

                padding: 16px;
            }}

            .card {{

                padding:
                    34px
                    24px
                    30px;

                border-radius: 20px;
            }}

            .icon {{

                width: 110px;

                height: 92px;

                margin-bottom: 20px;
            }}

            h1 {{

                font-size: 24px;
            }}

            .message {{

                font-size: 14px;
            }}
        }}


        /* =================================================
           Reduce Motion
           ================================================= */

        @media (prefers-reduced-motion: reduce) {{

            *,
            *::before,
            *::after {{

                animation-duration:
                    0.01ms !important;

                animation-iteration-count:
                    1 !important;
            }}
        }}

    </style>

</head>


<body>

    <main class="container">

        <section class="card">


            <!-- Discord Icon -->

            <div class="icon">

                <img
                    src="{DISCORD_ICON_URL}"
                    alt="Discord Bot"
                >

            </div>


            <!-- Status -->

            <div class="status">

                <span class="status-icon">
                    {status_icon}
                </span>

                {status_text}

            </div>


            <!-- Heading -->

            <h1>
                {safe_heading}
            </h1>


            <!-- Message -->

            <p class="message">
                {safe_message}
            </p>


            <!-- Divider -->

            <div class="divider"></div>


            <!-- Footer -->

            <div class="footer">

                <span class="brand">
                    Discord Bot
                </span>

                <br>

                このページを閉じてDiscordに戻ってください。

            </div>


        </section>

    </main>

</body>

</html>
"""


# =========================================================
# OAuth Login
# =========================================================

@app.route("/auth/login")
def auth_login():

    state_parameter = request.args.get(
        "state",
        ""
    )

    # ---------------------------------------------
    # GUILD_ID_ROLE_ID
    # ---------------------------------------------

    parts = state_parameter.split("_")

    if len(parts) != 2:

        return auth_page(
            "認証エラー",
            "認証情報が正しくありません",
            "サーバーIDまたはロールIDが正しく指定されていません。",
            False,
            400
        ), 400

    guild_id, role_id = parts

    # ---------------------------------------------
    # Numeric Check
    # ---------------------------------------------

    if (
        not guild_id.isdigit()
        or not role_id.isdigit()
    ):

        return auth_page(
            "認証エラー",
            "認証情報が正しくありません",
            "サーバーIDまたはロールIDが正しくありません。",
            False,
            400
        ), 400

    # ---------------------------------------------
    # Session
    # ---------------------------------------------

    session["guild_id"] = guild_id

    session["role_id"] = role_id

    # ---------------------------------------------
    # OAuth State
    # ---------------------------------------------

    oauth_state = secrets.token_urlsafe(32)

    session["oauth_state"] = oauth_state

    # ---------------------------------------------
    # OAuth Parameters
    # ---------------------------------------------

    params = {

        "client_id":
            CLIENT_ID,

        "redirect_uri":
            REDIRECT_URI,

        "response_type":
            "code",

        "scope":
            "identify guilds",

        "state":
            oauth_state,
    }

    url = (
        DISCORD_OAUTH_AUTHORIZE
        + "?"
        + urllib.parse.urlencode(params)
    )

    return redirect(url)


# =========================================================
# OAuth Callback
# =========================================================

@app.route("/auth/callback")
def auth_callback():

    code = request.args.get(
        "code"
    )

    received_state = request.args.get(
        "state"
    )


    # =====================================================
    # OAuth State Check
    # =====================================================

    if not code:

        return auth_page(
            "認証エラー",
            "認証コードがありません",
            "Discordから認証コードを受け取れませんでした。",
            False,
            400
        ), 400


    if not received_state:

        return auth_page(
            "認証エラー",
            "認証状態がありません",
            "OAuth認証の状態情報が見つかりませんでした。",
            False,
            400
        ), 400


    saved_state = session.get(
        "oauth_state"
    )


    if (
        not saved_state
        or received_state != saved_state
    ):

        return auth_page(
            "認証エラー",
            "認証に失敗しました",
            "認証状態が一致しません。もう一度認証してください。",
            False,
            400
        ), 400


    # =====================================================
    # Guild / Role
    # =====================================================

    guild_id = session.get(
        "guild_id"
    )

    role_id = session.get(
        "role_id"
    )


    if not guild_id or not role_id:

        return auth_page(
            "認証エラー",
            "認証情報がありません",
            "サーバーIDまたはロールIDがセッションに保存されていません。",
            False,
            400
        ), 400


    # =====================================================
    # OAuth Token
    # =====================================================

    token_data = {

        "client_id":
            CLIENT_ID,

        "client_secret":
            CLIENT_SECRET,

        "grant_type":
            "authorization_code",

        "code":
            code,

        "redirect_uri":
            REDIRECT_URI,
    }


    try:

        token_response = discord_rest_request(

            "POST",

            DISCORD_OAUTH_TOKEN,

            data=token_data,

            timeout=15
        )

    except requests.RequestException as error:

        logger.exception(
            "OAuth token request failed: %s",
            error
        )

        return auth_page(
            "認証エラー",
            "Discordとの通信に失敗しました",
            "Discordへの接続中にエラーが発生しました。しばらくしてからもう一度お試しください。",
            False,
            500
        ), 500


    if token_response.status_code != 200:

        logger.error(
            "OAuth token failed: status=%s body=%s",
            token_response.status_code,
            token_response.text
        )

        return auth_page(
            "認証エラー",
            "Discord認証に失敗しました",
            "Discordからアクセストークンを取得できませんでした。もう一度認証してください。",
            False,
            400
        ), 400


    try:

        token_json = (
            token_response.json()
        )

    except ValueError:

        return auth_page(
            "認証エラー",
            "認証情報を読み取れませんでした",
            "Discordから返された認証情報が正しくありません。",
            False,
            500
        ), 500


    access_token = token_json.get(
        "access_token"
    )


    if not access_token:

        return auth_page(
            "認証エラー",
            "アクセストークンがありません",
            "Discordからアクセストークンを取得できませんでした。",
            False,
            500
        ), 500


    # =====================================================
    # Discord User
    # =====================================================

    headers = {

        "Authorization":
            f"Bearer {access_token}"
    }


    try:

        user_response = discord_rest_request(

            "GET",

            f"{DISCORD_API}/users/@me",

            headers=headers,

            timeout=15
        )

    except requests.RequestException as error:

        logger.exception(
            "Discord user request failed: %s",
            error
        )

        return auth_page(
            "認証エラー",
            "ユーザー情報を取得できませんでした",
            "Discordからユーザー情報を取得できませんでした。",
            False,
            500
        ), 500


    if user_response.status_code != 200:

        logger.error(
            "Discord user request failed: status=%s body=%s",
            user_response.status_code,
            user_response.text
        )

        return auth_page(
            "認証エラー",
            "ユーザー情報の取得に失敗しました",
            "Discordアカウント情報を取得できませんでした。",
            False,
            400
        ), 400


    try:

        user = user_response.json()

    except ValueError:

        return auth_page(
            "認証エラー",
            "ユーザー情報を読み取れませんでした",
            "Discordから返されたユーザー情報が正しくありません。",
            False,
            500
        ), 500


    user_id = user.get(
        "id"
    )


    if not user_id:

        return auth_page(
            "認証エラー",
            "ユーザーIDを取得できませんでした",
            "DiscordアカウントのユーザーIDを取得できませんでした。",
            False,
            500
        ), 500


    # =====================================================
    # User Guilds
    # =====================================================

    try:

        guilds_response = discord_rest_request(

            "GET",

            f"{DISCORD_API}/users/@me/guilds",

            headers=headers,

            timeout=15
        )

    except requests.RequestException as error:

        logger.exception(
            "Discord guild request failed: %s",
            error
        )

        return auth_page(
            "認証エラー",
            "サーバー情報を取得できませんでした",
            "Discordからサーバー情報を取得できませんでした。",
            False,
            500
        ), 500


    if guilds_response.status_code != 200:

        logger.error(
            "Discord guild request failed: status=%s body=%s",
            guilds_response.status_code,
            guilds_response.text
        )

        return auth_page(
            "認証エラー",
            "サーバー情報の取得に失敗しました",
            "Discordから参加サーバー情報を取得できませんでした。",
            False,
            400
        ), 400


    try:

        user_guilds = (
            guilds_response.json()
        )

    except ValueError:

        return auth_page(
            "認証エラー",
            "サーバー情報を読み取れませんでした",
            "Discordから返されたサーバー情報が正しくありません。",
            False,
            500
        ), 500


    user_guild_ids = {

        str(guild.get("id"))

        for guild in user_guilds

        if guild.get("id")
    }


    # =====================================================
    # Banned Guild Check
    # =====================================================

    if BANNED_GUILDS.intersection(
        user_guild_ids
    ):

        logger.warning(
            "Blocked OAuth user=%s because user is in banned guild",
            user_id
        )

        return auth_page(
            "認証拒否",
            "認証できません",
            "このDiscordアカウントでは認証を利用できません。",
            False,
            403
        ), 403


    # =====================================================
    # Target Guild Check
    # =====================================================

    if str(guild_id) not in user_guild_ids:

        return auth_page(
            "認証エラー",
            "サーバーに参加していません",
            "指定されたDiscordサーバーに参加しているアカウントで認証してください。",
            False,
            403
        ), 403


    # =====================================================
    # Give Role
    # =====================================================

    bot_headers = {

        "Authorization":
            f"Bot {DISCORD_TOKEN}"
    }


    role_url = (

        f"{DISCORD_API}/guilds/"
        f"{guild_id}/members/"
        f"{user_id}/roles/"
        f"{role_id}"
    )


    try:

        role_response = discord_rest_request(

            "PUT",

            role_url,

            headers=bot_headers,

            timeout=15
        )

    except requests.RequestException as error:

        logger.exception(
            "Role assignment request failed: %s",
            error
        )

        return auth_page(
            "認証エラー",
            "ロール付与に失敗しました",
            "Discordサーバーへのロール付与中に通信エラーが発生しました。",
            False,
            500
        ), 500


    # =====================================================
    # Success
    # =====================================================

    if role_response.status_code == 204:

        logger.info(
            "OAuth role assignment succeeded: "
            "user=%s, guild=%s, role=%s",
            user_id,
            guild_id,
            role_id
        )

        return auth_page(
            "認証完了",
            "認証が完了しました！",
            "Discordサーバーへの認証が完了し、ロールが付与されました。",
            True,
            200
        ), 200


    # =====================================================
    # Role Assignment Failed
    # =====================================================

    logger.error(
        "Role assignment failed: "
        "status=%s body=%s user=%s guild=%s role=%s",
        role_response.status_code,
        role_response.text,
        user_id,
        guild_id,
        role_id
    )


    if role_response.status_code == 403:

        message = (
            "Botにロールを付与する権限がありません。"
            "Botの権限とロールの位置を確認してください。"
        )


    elif role_response.status_code == 404:

        message = (
            "指定されたサーバー、ユーザー、またはロールが見つかりません。"
        )


    elif role_response.status_code == 429:

        message = (
            "Discord APIのレート制限により"
            "ロールを付与できませんでした。"
            "しばらくしてからもう一度お試しください。"
        )


    else:

        message = (
            "ロールの付与に失敗しました。"
            f"Discord API HTTP {role_response.status_code}"
        )


    return auth_page(
        "認証エラー",
        "ロールの付与に失敗しました",
        message,
        False,
        500
    ), 500


# =========================================================
# Flask Server
# =========================================================

def run_flask():

    logger.info(
        "Starting Flask server on port %s",
        PORT
    )


    app.run(

        host="0.0.0.0",

        port=PORT,

        debug=False,

        use_reloader=False
    )


# =========================================================
# Discord Bot
# =========================================================

class MyBot(commands.Bot):


    def __init__(self):

        intents = (
            discord.Intents.default()
        )

        intents.message_content = True

        intents.voice_states = True

        intents.members = True


        super().__init__(

            command_prefix="!",

            intents=intents
        )


        self.db_pool = None

        # 旧Cog互換
        self.pool = None


    # =====================================================
    # Setup Hook
    # =====================================================

    async def setup_hook(self):

        logger.info(
            "Running setup_hook..."
        )


        # -------------------------------------------------
        # PostgreSQL
        # -------------------------------------------------

        if DATABASE_URL:

            logger.info(
                "Connecting to PostgreSQL..."
            )


            try:

                self.db_pool = (
                    await asyncpg.create_pool(

                        DATABASE_URL,

                        min_size=1,

                        max_size=5,

                        statement_cache_size=0
                    )
                )


                # 旧Cogとの互換
                self.pool = self.db_pool


                logger.info(
                    "PostgreSQL connection pool created."
                )


            except Exception as error:

                logger.exception(
                    "Failed to connect to PostgreSQL: %s",
                    error
                )

                self.db_pool = None

                self.pool = None


        else:

            logger.warning(
                "DATABASE_URL is not configured."
            )


        # -------------------------------------------------
        # Load Cogs
        # -------------------------------------------------

        loaded_cogs = 0

        cogs_dir = "cogs"


        if os.path.isdir(
            cogs_dir
        ):

            for filename in sorted(
                os.listdir(cogs_dir)
            ):

                if not filename.endswith(
                    ".py"
                ):
                    continue


                if filename.startswith(
                    "_"
                ):
                    continue


                extension = (
                    f"cogs.{filename[:-3]}"
                )


                try:

                    await self.load_extension(
                        extension
                    )

                    loaded_cogs += 1


                    logger.info(
                        "Loaded Cog: %s",
                        extension
                    )


                except Exception:

                    logger.exception(
                        "Failed to load Cog: %s",
                        extension
                    )


        else:

            logger.warning(
                "cogs directory does not exist."
            )


        logger.info(
            "Cogs loaded: %s",
            loaded_cogs
        )


        # -------------------------------------------------
        # Sync Slash Commands
        # -------------------------------------------------

        try:

            synced = (
                await self.tree.sync()
            )


            logger.info(
                "Synced %s slash commands.",
                len(synced)
            )


        except Exception:

            logger.exception(
                "Failed to sync slash commands."
            )


    # =====================================================
    # Ready
    # =====================================================

    async def on_ready(self):

        logger.info(
            "=========================================="
        )


        logger.info(
            "Bot Ready: %s (%s)",
            self.user,
            self.user.id
            if self.user
            else "unknown"
        )


        logger.info(
            "Guild count: %s",
            len(self.guilds)
        )


        logger.info(
            "=========================================="
        )


        # -------------------------------------------------
        # Status
        # -------------------------------------------------

        try:

            await self.change_presence(

                status=discord.Status.online,

                activity=discord.Game(

                    name=
                        f"{len(self.guilds)}個のサーバー"
                )
            )


            logger.info(
                "Bot status updated."
            )


        except Exception:

            logger.exception(
                "Failed to update bot status."
            )


    # =====================================================
    # Close
    # =====================================================

    async def close(self):

        logger.info(
            "Closing Discord bot..."
        )


        if self.db_pool:

            try:

                await self.db_pool.close()


                logger.info(
                    "PostgreSQL pool closed."
                )


            except Exception:

                logger.exception(
                    "Failed to close PostgreSQL pool."
                )


        self.db_pool = None

        self.pool = None


        await super().close()


# =========================================================
# Discord Bot Retry
# =========================================================

def get_retry_after(error):

    """
    Discord API 429からRetry-Afterを取得します。
    """


    # -----------------------------------------------------
    # Response Header
    # -----------------------------------------------------

    try:

        response = getattr(
            error,
            "response",
            None
        )


        if response:

            headers = getattr(
                response,
                "headers",
                None
            )


            if headers:

                value = headers.get(
                    "Retry-After"
                )


                if value is not None:

                    return float(value)


    except Exception:

        pass


    # -----------------------------------------------------
    # discord.py retry_after
    # -----------------------------------------------------

    try:

        retry_after = getattr(
            error,
            "retry_after",
            None
        )


        if retry_after is not None:

            return float(
                retry_after
            )


    except Exception:

        pass


    return None


# =========================================================
# Run Discord Bot
# =========================================================

def run_discord_bot():

    if not DISCORD_TOKEN:

        logger.error(
            "DISCORD_TOKEN is not configured."
        )

        return


    fallback_delays = [

        60,

        120,

        240,

        480,

        900
    ]


    attempt = 0


    while True:

        bot = None


        try:

            logger.info(
                "Creating Discord Bot instance..."
            )


            bot = MyBot()


            logger.info(
                "Starting Discord bot..."
            )


            bot.run(
                DISCORD_TOKEN
            )


            logger.warning(
                "Discord bot stopped normally."
            )


            break


        except discord.HTTPException as error:

            if error.status == 429:

                retry_after = (
                    get_retry_after(error)
                )


                if retry_after is None:

                    index = min(

                        attempt,

                        len(
                            fallback_delays
                        ) - 1
                    )


                    retry_after = (
                        fallback_delays[index]
                    )


                logger.warning(
                    "Discord API global rate limit detected. "
                    "Bot will remain OFFLINE and retry after %.1f seconds (%.1f minutes).",
                    retry_after,
                    retry_after / 60.0,
                )

                # Discordから指定されたRetry-Afterをそのまま使用します。
                # 待機中はGatewayへ再接続しないため、BotはDiscord上で
                # オフラインのままになります。
                deadline = time.monotonic() + retry_after
                last_reported = None

                while True:
                    remaining = max(0.0, deadline - time.monotonic())

                    if remaining <= 0:
                        break

                    rounded = int(remaining)

                    if (
                        rounded % 60 == 0
                        or rounded <= 10
                    ) and rounded != last_reported:
                        logger.info(
                            "Discord API rate limit: "
                            "Bot remains OFFLINE. %s seconds remaining.",
                            rounded,
                        )
                        last_reported = rounded

                    time.sleep(min(1.0, remaining))

                logger.info(
                    "Discord API rate-limit wait finished. "
                    "Starting Discord bot again..."
                )

                attempt += 1
                continue


            logger.exception(
                "Discord HTTPException occurred."
            )


        except discord.LoginFailure:

            logger.exception(
                "Discord login failed. "
                "Check DISCORD_TOKEN."
            )


            break


        except Exception:

            logger.exception(
                "Unexpected Discord bot error."
            )


        # -------------------------------------------------
        # Generic Retry
        # -------------------------------------------------

        index = min(

            attempt,

            len(
                fallback_delays
            ) - 1
        )


        delay = (
            fallback_delays[index]
        )


        attempt += 1


        logger.info(

            "Restarting Discord bot in %s seconds...",

            delay
        )


        time.sleep(
            delay
        )


# =========================================================
# Process Lock
# =========================================================

LOCK_FILE = "bot_instance.lock"


def acquire_lock():

    lock_file = open(

        LOCK_FILE,

        "w"
    )


    try:

        fcntl.flock(

            lock_file.fileno(),

            fcntl.LOCK_EX
            | fcntl.LOCK_NB
        )


    except BlockingIOError:

        logger.error(
            "Another bot instance is already running."
        )


        lock_file.close()


        return None


    return lock_file


# =========================================================
# Main
# =========================================================

if __name__ == "__main__":

    logger.info(
        "=========================================="
    )


    logger.info(
        "Starting Discord Bot + Flask OAuth"
    )


    logger.info(
        "=========================================="
    )


    # -----------------------------------------------------
    # Prevent duplicate bot instances
    # -----------------------------------------------------

    lock = acquire_lock()


    if lock is None:

        raise SystemExit(1)


    # -----------------------------------------------------
    # Flask
    # -----------------------------------------------------

    flask_thread = threading.Thread(

        target=run_flask,

        daemon=True
    )


    flask_thread.start()


    logger.info(
        "Flask thread started."
    )


    # -----------------------------------------------------
    # Discord
    # -----------------------------------------------------

    run_discord_bot()
