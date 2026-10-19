
import discord
from discord import app_commands
from discord.ext import commands


class WordFilter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_pool = None

    async def setup_database(self):
        self.db_pool = getattr(self.bot, "db_pool", None)

        if self.db_pool is None:
            self.db_pool = getattr(self.bot, "pool", None)

        if self.db_pool is None:
            raise RuntimeError(
                "データベースプールが見つかりません。"
                "main.py の DB 初期化を確認してください。"
            )

        async with self.db_pool.acquire() as conn:
            # チャンネルごとの禁止ワード
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_blocked_words (
                    guild_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    word TEXT NOT NULL,
                    created_by BIGINT,
                    PRIMARY KEY (guild_id, channel_id, word)
                )
            """)

            # サーバーごとのログチャンネル設定
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS word_filter_settings (
                    guild_id BIGINT PRIMARY KEY,
                    log_channel_id BIGINT
                )
            """)

    async def get_words(self, guild_id: int, channel_id: int):
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT word
                FROM channel_blocked_words
                WHERE guild_id = $1 AND channel_id = $2
                ORDER BY word
                """,
                guild_id,
                channel_id,
            )

        return [row["word"] for row in rows]

    async def get_log_channel_id(self, guild_id: int):
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT log_channel_id
                FROM word_filter_settings
                WHERE guild_id = $1
                """,
                guild_id,
            )

        return row["log_channel_id"] if row else None

    async def send_log(
        self,
        message: discord.Message,
        matched_word: str,
    ):
        if message.guild is None:
            return

        log_channel_id = await self.get_log_channel_id(
            message.guild.id
        )

        # ログチャンネルが未設定なら削除だけ行う
        if not log_channel_id:
            return

        log_channel = message.guild.get_channel(log_channel_id)

        if log_channel is None:
            try:
                log_channel = await self.bot.fetch_channel(
                    log_channel_id
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return

        if not isinstance(log_channel, discord.TextChannel):
            return

        # 元のメッセージを記録
        created_at_unix = int(message.created_at.timestamp())
        content = message.content or "（テキスト本文なし）"

        header = (
            "【禁止ワード検出ログ】\n"
            f"ユーザー: {message.author} "
            f"(ID: {message.author.id})\n"
            f"元のチャンネル: {message.channel.mention} "
            f"(ID: {message.channel.id})\n"
            f"送信時間: <t:{created_at_unix}:F>\n"
            f"対象ワード: {matched_word}\n"
            f"メッセージID: {message.id}\n"
            "元のメッセージ全文:\n"
        )

        # Discordのメッセージ上限を超えないよう分割する
        # 長文でも全文がログに残るようにする
        limit = 1700
        chunks = [
            content[i:i + limit]
            for i in range(0, len(content), limit)
        ]

        if not chunks:
            chunks = ["（テキスト本文なし）"]

        allowed_mentions = discord.AllowedMentions.none()

        for index, chunk in enumerate(chunks):
            if index == 0:
                log_text = header + chunk
            else:
                log_text = (
                    f"【ログ本文の続き {index + 1}/{len(chunks)}】\n"
                    f"メッセージID: {message.id}\n"
                    + chunk
                )

            try:
                await log_channel.send(
                    log_text,
                    allowed_mentions=allowed_mentions,
                )
            except (discord.Forbidden, discord.HTTPException):
                # ログ送信に失敗してもBot全体は停止させない
                print(
                    "[WordFilter] ログ送信に失敗しました: "
                    f"guild={message.guild.id}, "
                    f"message={message.id}"
                )
                return

    @app_commands.command(
        name="word-add",
        description="指定チャンネルに禁止ワードを追加します",
    )
    @app_commands.describe(
        channel="禁止ワードを適用するチャンネル",
        word="禁止するワード",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        word: str,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        word = word.strip().casefold()

        if not word:
            await interaction.response.send_message(
                "ワードを入力してください。",
                ephemeral=True,
            )
            return

        if len(word) > 200:
            await interaction.response.send_message(
                "ワードは200文字以内にしてください。",
                ephemeral=True,
            )
            return

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO channel_blocked_words
                    (guild_id, channel_id, word, created_by)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id, channel_id, word)
                DO NOTHING
                """,
                interaction.guild_id,
                channel.id,
                word,
                interaction.user.id,
            )

        if result.endswith(" 0"):
            text = "その禁止ワードはすでに登録されています。"
        else:
            text = (
                f"{channel.mention} に禁止ワード "
                f"`{word}` を追加しました。"
            )

        await interaction.response.send_message(
            text,
            ephemeral=True,
        )

    @app_commands.command(
        name="word-remove",
        description="指定チャンネルの禁止ワードを削除します",
    )
    @app_commands.describe(
        channel="対象チャンネル",
        word="削除するワード",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_remove(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        word: str,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        word = word.strip().casefold()

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM channel_blocked_words
                WHERE guild_id = $1
                  AND channel_id = $2
                  AND word = $3
                """,
                interaction.guild_id,
                channel.id,
                word,
            )

        if result.endswith(" 0"):
            text = "その禁止ワードは登録されていません。"
        else:
            text = (
                f"{channel.mention} から禁止ワード "
                f"`{word}` を削除しました。"
            )

        await interaction.response.send_message(
            text,
            ephemeral=True,
        )

    @app_commands.command(
        name="word-list",
        description="指定チャンネルの禁止ワードを表示します",
    )
    @app_commands.describe(channel="対象チャンネル")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_list(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        words = await self.get_words(
            interaction.guild_id,
            channel.id,
        )

        if not words:
            text = f"{channel.mention} に禁止ワードはありません。"
        else:
            text = (
                f"**{channel.mention} の禁止ワード一覧**\n"
                + "\n".join(f"・`{word}`" for word in words)
            )

        await interaction.response.send_message(
            text[:1900],
            ephemeral=True,
        )

    @app_commands.command(
        name="word-clear",
        description="指定チャンネルの禁止ワードをすべて削除します",
    )
    @app_commands.describe(channel="対象チャンネル")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_clear(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM channel_blocked_words
                WHERE guild_id = $1 AND channel_id = $2
                """,
                interaction.guild_id,
                channel.id,
            )

        count = int(result.split()[-1])

        await interaction.response.send_message(
            f"{channel.mention} の禁止ワードを "
            f"{count} 件削除しました。",
            ephemeral=True,
        )

    @app_commands.command(
        name="word-log-channel",
        description="禁止ワード検出ログの送信先を設定・解除します",
    )
    @app_commands.describe(
        channel="ログ送信先。省略するとログを解除します",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def word_log_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        if channel is not None:
            permissions = channel.permissions_for(
                interaction.guild.me
            )

            if not permissions.send_messages:
                await interaction.response.send_message(
                    "指定チャンネルにメッセージを送信できません。"
                    "Botの権限を確認してください。",
                    ephemeral=True,
                )
                return

            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO word_filter_settings
                        (guild_id, log_channel_id)
                    VALUES ($1, $2)
                    ON CONFLICT (guild_id)
                    DO UPDATE SET log_channel_id = EXCLUDED.log_channel_id
                    """,
                    interaction.guild_id,
                    channel.id,
                )

            text = (
                f"禁止ワードのログ送信先を "
                f"{channel.mention} に設定しました。"
            )
        else:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO word_filter_settings
                        (guild_id, log_channel_id)
                    VALUES ($1, NULL)
                    ON CONFLICT (guild_id)
                    DO UPDATE SET log_channel_id = NULL
                    """,
                    interaction.guild_id,
                )

            text = (
                "ログ送信を解除しました。"
                "今後はメッセージの削除のみ行います。"
            )

        await interaction.response.send_message(
            text,
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        if message.author.bot:
            return

        # ログチャンネルのログ自体が削除されるのを防ぐ
        log_channel_id = await self.get_log_channel_id(
            message.guild.id
        )
        if log_channel_id == message.channel.id:
            return

        words = await self.get_words(
            message.guild.id,
            message.channel.id,
        )

        if not words:
            return

        content = message.content.casefold()

        matched_word = next(
            (word for word in words if word in content),
            None,
        )

        if matched_word is None:
            return

        # メッセージを削除する
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        # ログチャンネルが設定されている場合だけ記録する
        await self.send_log(message, matched_word)


async def setup(bot: commands.Bot):
    cog = WordFilter(bot)
    await cog.setup_database()
    await bot.add_cog(cog)
