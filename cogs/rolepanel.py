import discord
from discord import app_commands
from discord.ext import commands


ROLE_EMOJIS = ["1️⃣", "2️⃣", "3️⃣"]


class RoleButton(discord.ui.Button):
    def __init__(
        self,
        guild_id: int,
        role_id: int,
        label: str,
        emoji: str,
    ):
        state_value = f"{guild_id}_{role_id}"

        auth_url = (
            "https://discord-bot-py-4mzn.onrender.com"
            f"/auth/login?state={state_value}"
        )

        super().__init__(
            label=f"{label} を取得",
            emoji=emoji,
            style=discord.ButtonStyle.link,
            url=auth_url,
        )


class RolePanelView(discord.ui.View):
    def __init__(
        self,
        guild_id: int,
        roles: list[tuple[int, str]],
    ):
        super().__init__(timeout=None)

        for index, (role_id, role_name) in enumerate(roles):
            if index < len(ROLE_EMOJIS):
                emoji = ROLE_EMOJIS[index]
            else:
                emoji = "🏷️"

            self.add_item(
                RoleButton(
                    guild_id=guild_id,
                    role_id=role_id,
                    label=role_name,
                    emoji=emoji,
                )
            )


class RolePanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # =========================================================
    # Cog読み込み時
    # =========================================================
    async def cog_load(self):
        print("🔧 rolepanel: cog_load開始", flush=True)

        # main.pyでは db_pool という名前でPostgreSQL Poolを保存している
        if not hasattr(self.bot, "db_pool") or self.bot.db_pool is None:
            print(
                "⚠️ rolepanel: PostgreSQLが利用できません。",
                flush=True,
            )
            return

        print(
            "🗄️ rolepanel: PostgreSQL接続を確認しました。",
            flush=True,
        )

        # -----------------------------------------------------
        # テーブル作成
        # -----------------------------------------------------
        try:
            await self.bot.db_pool.execute(
                """
                CREATE TABLE IF NOT EXISTS role_panels (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    message_id BIGINT UNIQUE,
                    role_ids BIGINT[] NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )

            print(
                "✅ role_panels テーブルを確認しました。",
                flush=True,
            )

        except Exception as e:
            print(
                f"❌ role_panels テーブル作成エラー: {e}",
                flush=True,
            )
            return

        # -----------------------------------------------------
        # 既存パネルの復元
        # -----------------------------------------------------
        try:
            await self.restore_panels()

        except Exception as e:
            print(
                f"❌ ロールパネル復元エラー: {e}",
                flush=True,
            )

        print(
            "🔧 rolepanel: cog_load完了",
            flush=True,
        )

    # =========================================================
    # 既存ロールパネルの復元
    # =========================================================
    async def restore_panels(self):
        if not hasattr(self.bot, "db_pool") or self.bot.db_pool is None:
            print(
                "⚠️ rolepanel: restore_panels時にDBがありません。",
                flush=True,
            )
            return

        print(
            "🔄 rolepanel: 既存パネルを検索しています...",
            flush=True,
        )

        rows = await self.bot.db_pool.fetch(
            """
            SELECT
                id,
                guild_id,
                channel_id,
                message_id,
                role_ids
            FROM role_panels
            WHERE message_id IS NOT NULL
            ORDER BY id
            """
        )

        restored = 0
        deleted = 0

        for row in rows:
            panel_id = row["id"]
            guild_id = row["guild_id"]
            channel_id = row["channel_id"]
            message_id = row["message_id"]
            role_ids = row["role_ids"]

            # -------------------------------------------------
            # Guild取得
            # -------------------------------------------------
            guild = self.bot.get_guild(guild_id)

            if guild is None:
                continue

            # -------------------------------------------------
            # Channel取得
            # -------------------------------------------------
            channel = guild.get_channel(channel_id)

            if channel is None:
                continue

            # -------------------------------------------------
            # Role取得
            # -------------------------------------------------
            roles = []

            for role_id in role_ids:
                role = guild.get_role(role_id)

                if role is not None:
                    roles.append(
                        (
                            role.id,
                            role.name,
                        )
                    )

            # -------------------------------------------------
            # 有効なロールが1つもない場合
            # -------------------------------------------------
            if not roles:
                try:
                    await self.bot.db_pool.execute(
                        """
                        DELETE FROM role_panels
                        WHERE id = $1
                        """,
                        panel_id,
                    )

                    deleted += 1

                except Exception as e:
                    print(
                        f"❌ DB削除エラー: {e}",
                        flush=True,
                    )

                continue

            # -------------------------------------------------
            # Persistent Viewを登録
            # -------------------------------------------------
            try:
                view = RolePanelView(
                    guild_id=guild.id,
                    roles=roles,
                )

                self.bot.add_view(
                    view,
                    message_id=message_id,
                )

                restored += 1

            except Exception as e:
                print(
                    f"❌ パネル復元失敗 "
                    f"(message_id={message_id}): {e}",
                    flush=True,
                )

        print(
            f"🔄 ロールパネル復元: {restored}個 / "
            f"削除: {deleted}個",
            flush=True,
        )

    # =========================================================
    # /rolepanel
    # =========================================================
    @app_commands.command(
        name="rolepanel",
        description="ロールを取得するためのパネルを作成します",
    )
    @app_commands.describe(
        role1="1つ目のロール",
        role2="2つ目のロール",
        role3="3つ目のロール",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def rolepanel(
        self,
        interaction: discord.Interaction,
        role1: discord.Role,
        role2: discord.Role | None = None,
        role3: discord.Role | None = None,
    ):
        # -----------------------------------------------------
        # DBチェック
        # -----------------------------------------------------
        if not hasattr(self.bot, "db_pool") or self.bot.db_pool is None:
            await interaction.response.send_message(
                "❌ PostgreSQLに接続されていません。",
                ephemeral=True,
            )
            return

        # -----------------------------------------------------
        # Guildチェック
        # -----------------------------------------------------
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ このコマンドはサーバー内でのみ使用できます。",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        # -----------------------------------------------------
        # ロール一覧作成
        # -----------------------------------------------------
        selected_roles = [
            role1,
        ]

        if role2 is not None:
            selected_roles.append(role2)

        if role3 is not None:
            selected_roles.append(role3)

        # -----------------------------------------------------
        # 重複ロールチェック
        # -----------------------------------------------------
        role_ids = []

        for role in selected_roles:
            if role.id not in role_ids:
                role_ids.append(role.id)

        selected_roles = [
            guild.get_role(role_id)
            for role_id in role_ids
        ]

        selected_roles = [
            role
            for role in selected_roles
            if role is not None
        ]

        if not selected_roles:
            await interaction.response.send_message(
                "❌ 有効なロールが指定されていません。",
                ephemeral=True,
            )
            return

        # -----------------------------------------------------
        # Botのロールより上のロールは操作できない
        # -----------------------------------------------------
        me = guild.me

        if me is not None and me.top_role is not None:
            for role in selected_roles:
                if role >= me.top_role:
                    await interaction.response.send_message(
                        "❌ Botより上位、または同じ位置のロールは"
                        "パネルに設定できません。\n"
                        f"対象ロール: {role.mention}",
                        ephemeral=True,
                    )
                    return

        # -----------------------------------------------------
        # View作成
        # -----------------------------------------------------
        roles_for_view = [
            (
                role.id,
                role.name,
            )
            for role in selected_roles
        ]

        view = RolePanelView(
            guild_id=guild.id,
            roles=roles_for_view,
        )

        # -----------------------------------------------------
        # DBへ仮登録
        #
        # message_idは後からDiscordのメッセージIDを入れる
        # -----------------------------------------------------
        try:
            row = await self.bot.db_pool.fetchrow(
                """
                INSERT INTO role_panels (
                    guild_id,
                    channel_id,
                    message_id,
                    role_ids
                )
                VALUES (
                    $1,
                    $2,
                    NULL,
                    $3
                )
                RETURNING id
                """,
                guild.id,
                interaction.channel_id,
                role_ids,
            )

            panel_id = row["id"]

        except Exception as e:
            print(
                f"❌ rolepanel DB登録エラー: {e}",
                flush=True,
            )

            await interaction.response.send_message(
                "❌ ロールパネルのDB登録に失敗しました。",
                ephemeral=True,
            )
            return

        # -----------------------------------------------------
        # Discordへパネル送信
        # -----------------------------------------------------
        try:
            await interaction.response.send_message(
                "🎫 **ロール取得パネル**\n\n"
                "下のボタンから取得したいロールを選択してください。",
                view=view,
            )

            message = await interaction.original_response()

        except Exception as e:
            print(
                f"❌ rolepanel メッセージ送信エラー: {e}",
                flush=True,
            )

            # Discordへの送信に失敗した場合、仮登録を削除
            try:
                await self.bot.db_pool.execute(
                    """
                    DELETE FROM role_panels
                    WHERE id = $1
                    """,
                    panel_id,
                )

            except Exception as db_error:
                print(
                    f"❌ 失敗したパネルのDB削除エラー: {db_error}",
                    flush=True,
                )

            # interaction.responseが既に使われている可能性があるため、
            # followupでエラーを通知
            try:
                await interaction.followup.send(
                    "❌ ロールパネルの作成に失敗しました。",
                    ephemeral=True,
                )
            except Exception:
                pass

            return

        # -----------------------------------------------------
        # DiscordのMessage IDをDBへ保存
        # -----------------------------------------------------
        try:
            await self.bot.db_pool.execute(
                """
                UPDATE role_panels
                SET message_id = $1
                WHERE id = $2
                """,
                message.id,
                panel_id,
            )

            print(
                f"✅ ロールパネル作成完了: "
                f"guild={guild.id}, "
                f"channel={interaction.channel_id}, "
                f"message={message.id}",
                flush=True,
            )

        except Exception as e:
            print(
                f"❌ message_id更新エラー: {e}",
                flush=True,
            )

            # View自体は既にDiscord上に存在しているため、
            # DBだけ削除して終了
            try:
                await self.bot.db_pool.execute(
                    """
                    DELETE FROM role_panels
                    WHERE id = $1
                    """,
                    panel_id,
                )

            except Exception as db_error:
                print(
                    f"❌ DBクリーンアップエラー: {db_error}",
                    flush=True,
                )

    # =========================================================
    # /rolepanel_delete
    # =========================================================
    @app_commands.command(
        name="rolepanel_delete",
        description="このチャンネルのロールパネルをDBから削除します",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def rolepanel_delete(
        self,
        interaction: discord.Interaction,
    ):
        # -----------------------------------------------------
        # DBチェック
        # -----------------------------------------------------
        if not hasattr(self.bot, "db_pool") or self.bot.db_pool is None:
            await interaction.response.send_message(
                "❌ PostgreSQLに接続されていません。",
                ephemeral=True,
            )
            return

        # -----------------------------------------------------
        # Guildチェック
        # -----------------------------------------------------
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ このコマンドはサーバー内でのみ使用できます。",
                ephemeral=True,
            )
            return

        # -----------------------------------------------------
        # Channelチェック
        # -----------------------------------------------------
        if interaction.channel_id is None:
            await interaction.response.send_message(
                "❌ チャンネルを取得できませんでした。",
                ephemeral=True,
            )
            return

        # -----------------------------------------------------
        # DBから削除
        # -----------------------------------------------------
        try:
            result = await self.bot.db_pool.execute(
                """
                DELETE FROM role_panels
                WHERE guild_id = $1
                  AND channel_id = $2
                """,
                interaction.guild.id,
                interaction.channel_id,
            )

            # asyncpgの結果は "DELETE n" 形式
            deleted_count = int(
                result.split()[-1]
            )

            if deleted_count == 0:
                await interaction.response.send_message(
                    "ℹ️ このチャンネルには登録された"
                    "ロールパネルがありません。",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                f"✅ {deleted_count}個のロールパネルを"
                "DBから削除しました。",
                ephemeral=True,
            )

            print(
                f"🗑️ ロールパネル削除: "
                f"guild={interaction.guild.id}, "
                f"channel={interaction.channel_id}, "
                f"count={deleted_count}",
                flush=True,
            )

        except Exception as e:
            print(
                f"❌ rolepanel_delete DBエラー: {e}",
                flush=True,
            )

            await interaction.response.send_message(
                "❌ ロールパネルの削除に失敗しました。",
                ephemeral=True,
            )


# =============================================================
# Cog setup
# =============================================================
async def setup(bot: commands.Bot):
    await bot.add_cog(
        RolePanel(bot)
    )
