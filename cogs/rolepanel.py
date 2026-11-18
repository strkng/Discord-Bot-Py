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
        auth_url = (
            "https://discord-bot-py-4mzn.onrender.com"
            f"/auth/login?state={guild_id}_{role_id}"
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
            emoji = (
                ROLE_EMOJIS[index]
                if index < len(ROLE_EMOJIS)
                else "🏷️"
            )

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

        if not hasattr(self.bot, "db_pool") or self.bot.db_pool is None:
            print("⚠️ PostgreSQLが利用できません。", flush=True)
            return

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

            print("✅ role_panels テーブル確認完了", flush=True)

        except Exception as e:
            print(f"❌ テーブル作成エラー: {e}", flush=True)
            return

        try:
            await self.restore_panels()
        except Exception as e:
            print(f"❌ パネル復元エラー: {e}", flush=True)

        print("🔧 rolepanel: cog_load完了", flush=True)

    # =========================================================
    # 既存パネル復元
    # =========================================================
    async def restore_panels(self):
        rows = await self.bot.db_pool.fetch(
            """
            SELECT id, guild_id, channel_id, message_id, role_ids
            FROM role_panels
            WHERE message_id IS NOT NULL
            ORDER BY id
            """
        )

        restored = 0

        for row in rows:
            guild = self.bot.get_guild(row["guild_id"])

            if guild is None:
                continue

            channel = guild.get_channel(row["channel_id"])

            if channel is None:
                continue

            roles = []

            for role_id in row["role_ids"]:
                role = guild.get_role(role_id)

                if role is not None:
                    roles.append((role.id, role.name))

            if not roles:
                continue

            try:
                view = RolePanelView(guild.id, roles)

                self.bot.add_view(
                    view,
                    message_id=row["message_id"],
                )

                restored += 1

            except Exception as e:
                print(
                    f"❌ パネル復元失敗: "
                    f"message_id={row['message_id']}: {e}",
                    flush=True,
                )

        print(f"🔄 ロールパネル復元: {restored}個", flush=True)

    # =========================================================
    # /rolepanel
    # =========================================================
    @app_commands.command(
        name="rolepanel",
        description="ロール取得パネルを作成します",
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
        if self.bot.db_pool is None:
            await interaction.response.send_message(
                "❌ PostgreSQLに接続されていません。",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ サーバー内でのみ使用できます。",
                ephemeral=True,
            )
            return

        guild = interaction.guild

        role_ids = list(dict.fromkeys(
            role.id
            for role in (role1, role2, role3)
            if role is not None
        ))

        selected_roles = [
            guild.get_role(role_id)
            for role_id in role_ids
        ]

        selected_roles = [
            role for role in selected_roles
            if role is not None
        ]

        if not selected_roles:
            await interaction.response.send_message(
                "❌ 有効なロールがありません。",
                ephemeral=True,
            )
            return

        me = guild.me

        if me is not None:
            for role in selected_roles:
                if role >= me.top_role:
                    await interaction.response.send_message(
                        f"❌ Botより上位または同じ位置のロールです。\n"
                        f"対象: {role.mention}",
                        ephemeral=True,
                    )
                    return

        roles_for_view = [
            (role.id, role.name)
            for role in selected_roles
        ]

        view = RolePanelView(guild.id, roles_for_view)

        # Discordへ先に送信してmessage_idを取得
        try:
            await interaction.response.send_message(
                "🎫 **ロール取得パネル**\n\n"
                "下のボタンから取得したいロールを選択してください。",
                view=view,
            )

            message = await interaction.original_response()

        except Exception as e:
            print(f"❌ パネル送信エラー: {e}", flush=True)

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ パネル送信に失敗しました。",
                        ephemeral=True,
                    )
            except Exception:
                pass

            return

        # message_idを含めてDB登録
        try:
            await self.bot.db_pool.execute(
                """
                INSERT INTO role_panels (
                    guild_id,
                    channel_id,
                    message_id,
                    role_ids
                )
                VALUES ($1, $2, $3, $4)
                """,
                guild.id,
                interaction.channel_id,
                message.id,
                role_ids,
            )

            print(
                f"✅ ロールパネル作成完了: "
                f"guild={guild.id}, "
                f"channel={interaction.channel_id}, "
                f"message={message.id}",
                flush=True,
            )

        except Exception as e:
            print(f"❌ DB登録エラー: {e}", flush=True)

            try:
                await message.delete()
            except Exception as delete_error:
                print(f"❌ パネル削除エラー: {delete_error}", flush=True)

            try:
                await interaction.followup.send(
                    "❌ DB登録に失敗したため、パネルを削除しました。",
                    ephemeral=True,
                )
            except Exception:
                pass

    # =========================================================
    # /rolepanel_delete
    # =========================================================
    @app_commands.command(
        name="rolepanel_delete",
        description="このチャンネルのロールパネル登録を削除します",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def rolepanel_delete(
        self,
        interaction: discord.Interaction,
    ):
        if self.bot.db_pool is None:
            await interaction.response.send_message(
                "❌ PostgreSQLに接続されていません。",
                ephemeral=True,
            )
            return

        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ サーバー内でのみ使用できます。",
                ephemeral=True,
            )
            return

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

            deleted_count = int(result.split()[-1])

            if deleted_count == 0:
                await interaction.response.send_message(
                    "ℹ️ このチャンネルに登録されたパネルはありません。",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                f"✅ {deleted_count}個のパネル登録を削除しました。",
                ephemeral=True,
            )

            print(
                f"🗑️ パネル登録削除: "
                f"guild={interaction.guild.id}, "
                f"channel={interaction.channel_id}, "
                f"count={deleted_count}",
                flush=True,
            )

        except Exception as e:
            print(f"❌ 削除エラー: {e}", flush=True)

            await interaction.response.send_message(
                "❌ パネル削除に失敗しました。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(RolePanel(bot))
