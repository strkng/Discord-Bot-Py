
import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class WordFilter(commands.Cog):
    """チャンネルごとの規制ワード機能"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def ensure_table(self):
        """規制ワード保存用テーブルを作成する。"""

        pool = getattr(self.bot, "db_pool", None)

        if pool is None:
            raise RuntimeError("PostgreSQLに接続されていません。")

        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_blocked_words (
                channel_id BIGINT NOT NULL,
                word TEXT NOT NULL,
                created_by BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (channel_id, word)
            )
            """
        )

    async def get_pool(self):
        pool = getattr(self.bot, "db_pool", None)

        if pool is None:
            return None

        return pool

    async def initialize(self):
        """Cogの初期化時にテーブルを準備する。"""
        await self.ensure_table()

    async def interaction_ready(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """DB接続を確認する。"""

        pool = await self.get_pool()

        if pool is None:
            message = (
                "データベースに接続できません。"
                "管理者に確認してください。"
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )

            return False

        return True

    # -----------------------------------------------------
    # /word-add
    # -----------------------------------------------------

    @app_commands.command(
        name="word-add",
        description="指定チャンネルに規制ワードを追加します",
    )
    @app_commands.describe(
        channel="規制ワードを設定するチャンネル",
        word="規制する文字列",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def word_add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        word: str,
    ):
        word = word.strip()

        if not word:
            await interaction.response.send_message(
                "規制ワードを入力してください。",
                ephemeral=True,
            )
            return

        if len(word) > 100:
            await interaction.response.send_message(
                "規制ワードは100文字以内にしてください。",
                ephemeral=True,
            )
            return

        if not await self.interaction_ready(interaction):
            return

        pool = await self.get_pool()
        normalized_word = word.casefold()

        result = await pool.execute(
            """
            INSERT INTO channel_blocked_words
                (channel_id, word, created_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (channel_id, word) DO NOTHING
            """,
            channel.id,
            normalized_word,
            interaction.user.id,
        )

        if result.endswith(" 0"):
            await interaction.response.send_message(
                f"「{discord.utils.escape_markdown(word)}」は"
                f"{channel.mention} に既に設定されています。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"{channel.mention} に規制ワード "
            f"「{discord.utils.escape_markdown(word)}」を追加しました。",
            ephemeral=True,
        )

        logger.info(
            "Blocked word added: channel=%s word=%r by=%s",
            channel.id,
            normalized_word,
            interaction.user.id,
        )

    # -----------------------------------------------------
    # /word-remove
    # -----------------------------------------------------

    @app_commands.command(
        name="word-remove",
        description="指定チャンネルの規制ワードを削除します",
    )
    @app_commands.describe(
        channel="規制ワードを削除するチャンネル",
        word="削除する規制ワード",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def word_remove(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        word: str,
    ):
        word = word.strip()

        if not word:
            await interaction.response.send_message(
                "削除する規制ワードを入力してください。",
                ephemeral=True,
            )
            return

        if not await self.interaction_ready(interaction):
            return

        pool = await self.get_pool()

        result = await pool.execute(
            """
            DELETE FROM channel_blocked_words
            WHERE channel_id = $1 AND word = $2
            """,
            channel.id,
            word.casefold(),
        )

        if result.endswith(" 0"):
            await interaction.response.send_message(
                f"{channel.mention} にその規制ワードはありません。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"{channel.mention} から規制ワード "
            f"「{discord.utils.escape_markdown(word)}」を削除しました。",
            ephemeral=True,
        )

        logger.info(
            "Blocked word removed: channel=%s word=%r by=%s",
            channel.id,
            word.casefold(),
            interaction.user.id,
        )

    # -----------------------------------------------------
    # /word-list
    # -----------------------------------------------------

    @app_commands.command(
        name="word-list",
        description="チャンネルに設定された規制ワード一覧を表示します",
    )
    @app_commands.describe(
        channel="一覧を確認するチャンネル",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def word_list(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if not await self.interaction_ready(interaction):
            return

        pool = await self.get_pool()

        rows = await pool.fetch(
            """
            SELECT word
            FROM channel_blocked_words
            WHERE channel_id = $1
            ORDER BY word
            """,
            channel.id,
        )

        if not rows:
            await interaction.response.send_message(
                f"{channel.mention} には規制ワードが設定されていません。",
                ephemeral=True,
            )
            return

        lines = [
            f"• {discord.utils.escape_markdown(row['word'])}"
            for row in rows
        ]

        output = f"**{channel.mention} の規制ワード一覧**\n"
        output += "\n".join(lines)

        # Discordのメッセージ長制限を超えないようにする。
        if len(output) > 1900:
            output = output[:1850] + "\n…（長いため省略）"

        await interaction.response.send_message(
            output,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -----------------------------------------------------
    # /word-clear
    # -----------------------------------------------------

    @app_commands.command(
        name="word-clear",
        description="チャンネルの規制ワードをすべて削除します",
    )
    @app_commands.describe(
        channel="規制ワードを全削除するチャンネル",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def word_clear(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if not await self.interaction_ready(interaction):
            return

        pool = await self.get_pool()

        result = await pool.execute(
            """
            DELETE FROM channel_blocked_words
            WHERE channel_id = $1
            """,
            channel.id,
        )

        deleted_count = int(result.rsplit(" ", 1)[-1])

        await interaction.response.send_message(
            f"{channel.mention} の規制ワードを"
            f" {deleted_count} 件削除しました。",
            ephemeral=True,
        )

        logger.info(
            "Blocked words cleared: channel=%s count=%s by=%s",
            channel.id,
            deleted_count,
            interaction.user.id,
        )

    # -----------------------------------------------------
    # Message Filter
    # -----------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        # 通常のテキストチャンネルだけを対象にする。
        if not isinstance(message.channel, discord.TextChannel):
            return

        pool = await self.get_pool()

        if pool is None:
            return

        try:
            rows = await pool.fetch(
                """
                SELECT word
                FROM channel_blocked_words
                WHERE channel_id = $1
                """,
                message.channel.id,
            )
        except Exception:
            logger.exception(
                "Failed to load blocked words for channel %s",
                message.channel.id,
            )
            return

        if not rows:
            return

        content = message.content.casefold()

        matched_word = next(
            (
                row["word"]
                for row in rows
                if row["word"] and row["word"] in content
            ),
            None,
        )

        if matched_word is None:
            return

        try:
            await message.delete()

            logger.info(
                "Message removed by word filter: "
                "guild=%s channel=%s user=%s word=%r",
                message.guild.id,
                message.channel.id,
                message.author.id,
                matched_word,
            )

        except discord.Forbidden:
            logger.warning(
                "Cannot delete message: missing permission "
                "guild=%s channel=%s",
                message.guild.id,
                message.channel.id,
            )
            return

        except discord.NotFound:
            # 他のBotやモデレーターが先に削除した場合。
            return

        except discord.HTTPException:
            logger.exception(
                "Failed to delete blocked-word message."
            )
            return

        # 削除通知。送信に失敗してもフィルター自体は停止しない。
        try:
            await message.channel.send(
                f"{message.author.mention} "
                "規制ワードが含まれているため、メッセージを削除しました。",
                delete_after=5,
                allowed_mentions=discord.AllowedMentions(
                    users=[message.author],
                ),
            )
        except discord.HTTPException:
            logger.warning(
                "Could not send word-filter notification."
            )


async def setup(bot: commands.Bot):
    cog = WordFilter(bot)
    await cog.initialize()
    await bot.add_cog(cog)
