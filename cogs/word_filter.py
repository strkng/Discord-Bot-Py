import discord
from discord import app_commands
from discord.ext import commands
from datetime import timezone


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
            # 既存テーブルを削除・再作成しない。
            # 既存DBに guild_id がない構成でも動作するよう、
            # channel_blocked_words は channel_id と word のみを使用する。
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_blocked_words (
                    channel_id BIGINT NOT NULL,
                    word TEXT NOT NULL,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (channel_id, word)
                )
            """)

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
                WHERE channel_id = $1
                ORDER BY word
                """,
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

    async def send_log(self, message: discord.Message, matched_word: str):
        if message.guild is None:
            return

        log_channel_id = await self.get_log_channel_id(message.guild.id)
        if not log_channel_id:
            return

        log_channel = message.guild.get_channel(log_channel_id)
        if log_channel is None:
            try:
                log_channel = await self.bot.fetch_channel(log_channel_id)
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                return

        if not isinstance(log_channel, discord.TextChannel):
            return

        timestamp = int(message.created_at.timestamp())
        content = message.content or "（テキスト本文なし。画像・添付ファイル等のみの可能性があります）"
        allowed_mentions = discord.AllowedMentions.none()

        # Embedのフィールド値には1024文字、説明文には4096文字の上限があるため、
        # 本文は安全な長さに分けて複数のEmbedに記録する。
        chunks = [content[i:i + 3500] for i in range(0, len(content), 3500)]
        if not chunks:
            chunks = ["（テキスト本文なし）"]

        try:
            for index, chunk in enumerate(chunks):
                embed = discord.Embed(
                    title=(
                        "🚨 禁止ワードを検知しました"
                        if index == 0
                        else f"📄 削除メッセージ本文（続き {index + 1}/{len(chunks)}）"
                    ),
                    color=discord.Color.red(),
                    timestamp=message.created_at,
                )

                if index == 0:
                    embed.add_field(
                        name="ユーザー",
                        value=f"{message.author.mention}\n`{message.author}`\nID: `{message.author.id}`",
                        inline=True,
                    )
                    embed.add_field(
                        name="チャンネル",
                        value=f"{message.channel.mention}\nID: `{message.channel.id}`",
                        inline=True,
                    )
                    embed.add_field(
                        name="検知した禁止ワード",
                        value=f"`{matched_word[:1000]}`",
                        inline=False,
                    )
                    embed.add_field(
                        name="送信日時",
                        value=f"<t:{timestamp}:F>（<t:{timestamp}:R>）",
                        inline=True,
                    )
                    embed.add_field(
                        name="メッセージID",
                        value=f"`{message.id}`",
                        inline=True,
                    )
                    embed.description = f"**削除されたメッセージの全文**\n{chunk}"
                else:
                    embed.description = chunk

                embed.set_footer(
                    text=f"サーバー: {message.guild.name} • メッセージ {index + 1}/{len(chunks)}"
                )

                await log_channel.send(
                    embed=embed,
                    allowed_mentions=allowed_mentions,
                )
        except (discord.Forbidden, discord.HTTPException):
            print(
                "[WordFilter] Embedログ送信に失敗しました: "
                f"guild={message.guild.id}, message={message.id}"
            )

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
                    (channel_id, word, created_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (channel_id, word)
                DO NOTHING
                """,
                channel.id,
                word,
                interaction.user.id,
            )

        if result.endswith(" 0"):
            text = "その禁止ワードはすでに登録されています。"
        else:
            text = f"{channel.mention} に禁止ワード `{word}` を追加しました。"

        await interaction.response.send_message(text, ephemeral=True)

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
                WHERE channel_id = $1
                  AND word = $2
                """,
                channel.id,
                word,
            )

        if result.endswith(" 0"):
            text = "その禁止ワードは登録されていません。"
        else:
            text = f"{channel.mention} から禁止ワード `{word}` を削除しました。"

        await interaction.response.send_message(text, ephemeral=True)

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

        words = await self.get_words(interaction.guild_id, channel.id)
        if not words:
            text = f"{channel.mention} に禁止ワードはありません。"
        else:
            text = (
                f"**{channel.mention} の禁止ワード一覧**\n"
                + "\n".join(f"・`{word}`" for word in words)
            )

        await interaction.response.send_message(text[:1900], ephemeral=True)

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
                WHERE channel_id = $1
                """,
                channel.id,
            )

        count = int(result.split()[-1])
        await interaction.response.send_message(
            f"{channel.mention} の禁止ワードを {count} 件削除しました。",
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
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        if channel is not None:
            bot_member = interaction.guild.me
            if bot_member is None:
                bot_member = interaction.guild.get_member(self.bot.user.id)

            permissions = channel.permissions_for(bot_member) if bot_member else None
            if permissions is None or not permissions.send_messages or not permissions.embed_links:
                await interaction.response.send_message(
                    "指定チャンネルでEmbedを送信するには、Botに「メッセージを送信」と「埋め込みリンク」の権限が必要です。",
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
            text = f"禁止ワードのEmbedログ送信先を {channel.mention} に設定しました。"
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
            text = "ログ送信を解除しました。今後はメッセージの削除のみ行います。"

        await interaction.response.send_message(text, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        log_channel_id = await self.get_log_channel_id(message.guild.id)
        if log_channel_id == message.channel.id:
            return

        words = await self.get_words(message.guild.id, message.channel.id)
        if not words:
            return

        content = message.content.casefold()
        matched_word = next((word for word in words if word in content), None)
        if matched_word is None:
            return

        try:
            await message.delete()
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return

        await self.send_log(message, matched_word)


async def setup(bot: commands.Bot):
    cog = WordFilter(bot)
    await cog.setup_database()
    await bot.add_cog(cog)
