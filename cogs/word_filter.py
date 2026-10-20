import re
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
            # 既存のテーブルと登録済みデータは削除しない
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

            # 既存設定テーブルに新しい設定項目を追加
            await conn.execute("""
                ALTER TABLE word_filter_settings
                ADD COLUMN IF NOT EXISTS admin_exempt BOOLEAN
                NOT NULL DEFAULT TRUE
            """)

            await conn.execute("""
                ALTER TABLE word_filter_settings
                ADD COLUMN IF NOT EXISTS filter_mode TEXT
                NOT NULL DEFAULT 'channel_only'
            """)

            # サーバー共通の禁止ワード
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS server_blocked_words (
                    guild_id BIGINT NOT NULL,
                    word TEXT NOT NULL,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (guild_id, word)
                )
            """)

            # 適用除外チャンネル
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS word_filter_excluded_channels (
                    guild_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (guild_id, channel_id)
                )
            """)

            # Bot全体で共有するテンプレート
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_word_filter_templates (
                    template_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    words TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 初期テンプレート
            templates = [
                (
                    "sexual",
                    "下ネタ・性的な発言",
                    "一般向けチャットで避けたい性的な表現",
                    [
                        "エロ画像",
                        "裸の写真送って",
                        "性的な画像送って",
                        "下着の写真送って",
                        "卑猥な画像",
                        "セックスしよう",
                        "体の写真送って",
                    ],
                ),
                (
                    "insults",
                    "暴言・悪口・侮辱",
                    "相手への直接的な暴言や脅迫的な表現",
                    [
                        "死ね",
                        "くたばれ",
                        "黙れカス",
                        "お前はゴミ",
                        "殺すぞ",
                        "消えろ",
                    ],
                ),
                (
                    "hate",
                    "差別・ヘイト",
                    "属性を理由にした攻撃的な表現",
                    [
                        "人種差別していい",
                        "国籍を理由に追い出せ",
                        "障害者は出ていけ",
                        "特定の民族は出ていけ",
                    ],
                ),
                (
                    "spam",
                    "荒らし・スパム",
                    "迷惑な宣伝や勧誘に使われやすい表現",
                    [
                        "無料で稼げる",
                        "今すぐ登録",
                        "拡散希望",
                        "招待コード",
                        "投資で必ず儲かる",
                        "アカウントを売ります",
                    ],
                ),
                (
                    "links",
                    "怪しいURL・勧誘",
                    "不審なURLや外部サービスへの誘導",
                    [
                        "discord.gg/",
                        "bit.ly/",
                        "tinyurl.com/",
                        "無料で稼げるサイト",
                        "個人情報を入力",
                        "電子マネーを送って",
                    ],
                ),
            ]

            async with conn.transaction():
                for template_key, name, description, words in templates:
                    await conn.execute(
                        """
                        INSERT INTO bot_word_filter_templates
                            (template_key, name, description, words)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (template_key) DO UPDATE SET
                            name = EXCLUDED.name,
                            description = EXCLUDED.description,
                            words = EXCLUDED.words,
                            updated_at = NOW()
                        """,
                        template_key,
                        name,
                        description,
                        words,
                    )

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

    async def get_server_words(self, guild_id: int):
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT word
                FROM server_blocked_words
                WHERE guild_id = $1
                ORDER BY word
                """,
                guild_id,
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

    async def get_filter_settings(self, guild_id: int):
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT admin_exempt, filter_mode, log_channel_id
                FROM word_filter_settings
                WHERE guild_id = $1
                """,
                guild_id,
            )

        if row is None:
            return {
                "admin_exempt": True,
                "filter_mode": "channel_only",
                "log_channel_id": None,
            }

        return dict(row)

    async def get_template(self, template_key: str):
        async with self.db_pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT template_key, name, description, words
                FROM bot_word_filter_templates
                WHERE template_key = $1
                """,
                template_key,
            )

    async def get_excluded_channel_ids(self, guild_id: int):
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT channel_id
                FROM word_filter_excluded_channels
                WHERE guild_id = $1
                """,
                guild_id,
            )
        return {row["channel_id"] for row in rows}
        
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
        content = message.content or "（テキスト本文なし。添付ファイル等のみの可能性があります）"
        chunks = [
            content[i:i + 3500]
            for i in range(0, len(content), 3500)
        ] or ["（テキスト本文なし）"]

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
                    value=(
                        f"{message.author.mention}\n"
                        f"`{message.author}`\n"
                        f"ID: `{message.author.id}`"
                    ),
                    inline=True,
                )
                embed.add_field(
                    name="チャンネル",
                    value=(
                        f"{message.channel.mention}\n"
                        f"ID: `{message.channel.id}`"
                    ),
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
                embed.description = f"**削除されたメッセージの本文**\n{chunk}"
            else:
                embed.description = chunk

            embed.set_footer(
                text=(
                    f"サーバー: {message.guild.name} • "
                    f"メッセージ {index + 1}/{len(chunks)}"
                )
            )

            try:
                await log_channel.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                print(
                    "[WordFilter] Embedログ送信に失敗しました: "
                    f"guild={message.guild.id}, message={message.id}"
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
        if not word or len(word) > 200:
            await interaction.response.send_message(
                "ワードは1〜200文字で入力してください。",
                ephemeral=True,
            )
            return

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO channel_blocked_words
                    (channel_id, word, created_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (channel_id, word) DO NOTHING
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
                WHERE channel_id = $1 AND word = $2
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
        text = (
            f"**{channel.mention} の禁止ワード一覧**\n"
            + "\n".join(f"・`{word}`" for word in words)
            if words
            else f"{channel.mention} に禁止ワードはありません。"
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
        name="word-server-add",
        description="サーバー共通の禁止ワードを追加します",
    )
    @app_commands.describe(word="禁止するワード")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_server_add(
        self,
        interaction: discord.Interaction,
        word: str,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        word = word.strip().casefold()
        if not word or len(word) > 200:
            await interaction.response.send_message(
                "ワードは1〜200文字で入力してください。",
                ephemeral=True,
            )
            return

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO server_blocked_words
                    (guild_id, word, created_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, word) DO NOTHING
                """,
                interaction.guild_id,
                word,
                interaction.user.id,
            )

        text = (
            "その禁止ワードはすでに登録されています。"
            if result.endswith(" 0")
            else f"サーバー共通の禁止ワード `{word}` を追加しました。"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(
        name="word-server-remove",
        description="サーバー共通の禁止ワードを削除します",
    )
    @app_commands.describe(word="削除するワード")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_server_remove(
        self,
        interaction: discord.Interaction,
        word: str,
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
                DELETE FROM server_blocked_words
                WHERE guild_id = $1 AND word = $2
                """,
                interaction.guild_id,
                word.strip().casefold(),
            )

        text = (
            "そのワードは登録されていません。"
            if result.endswith(" 0")
            else "サーバー共通の禁止ワードを削除しました。"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(
        name="word-server-list",
        description="サーバー共通の禁止ワード一覧を表示します",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_server_list(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        words = await self.get_server_words(interaction.guild_id)
        text = (
            "**サーバー共通の禁止ワード一覧**\n"
            + "\n".join(f"・`{word}`" for word in words)
            if words
            else "サーバー共通の禁止ワードはありません。"
        )
        await interaction.response.send_message(text[:1900], ephemeral=True)

    @app_commands.command(
        name="word-server-clear",
        description="サーバー共通の禁止ワードをすべて削除します",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_server_clear(
        self,
        interaction: discord.Interaction,
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
                DELETE FROM server_blocked_words
                WHERE guild_id = $1
                """,
                interaction.guild_id,
            )

        count = int(result.split()[-1])
        await interaction.response.send_message(
            f"サーバー共通の禁止ワードを {count} 件削除しました。",
            ephemeral=True,
        )
        
    @app_commands.command(
        name="word-template-list",
        description="禁止ワードテンプレート一覧を表示します",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_template_list(
        self,
        interaction: discord.Interaction,
    ):
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT template_key, name, description, words
                FROM bot_word_filter_templates
                ORDER BY name
                """
            )

        embed = discord.Embed(
            title="禁止ワードテンプレート一覧",
            description="キーを指定してプレビュー・追加できます。",
            color=discord.Color.blurple(),
        )

        for row in rows:
            embed.add_field(
                name=f"{row['name']} (`{row['template_key']}`)",
                value=(
                    f"{row['description']}\n"
                    f"収録ワード数：{len(row['words'])}"
                ),
                inline=False,
            )

        if not rows:
            embed.description = "テンプレートがありません。"

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    @app_commands.command(
        name="word-template-preview",
        description="テンプレートの収録ワードを確認します",
    )
    @app_commands.describe(template_key="一覧に表示されたテンプレートキー")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_template_preview(
        self,
        interaction: discord.Interaction,
        template_key: str,
    ):
        row = await self.get_template(template_key.strip().casefold())

        if row is None:
            await interaction.response.send_message(
                "テンプレートが見つかりません。/word-template-list を確認してください。",
                ephemeral=True,
            )
            return

        words = row["words"]
        embed = discord.Embed(
            title=f"テンプレート：{row['name']}",
            description=row["description"],
            color=discord.Color.blurple(),
        )

        word_text = "\n".join(f"・{word}" for word in words)
        if not word_text:
            word_text = "収録ワードはありません。"

        # Discord Embedの説明文・フィールド値の上限を考慮
        embed.add_field(
            name=f"収録ワード（{len(words)}件）",
            value=word_text[:1000],
            inline=False,
        )
        embed.set_footer(text=f"キー: {row['template_key']}")

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )

    @app_commands.command(
        name="word-template-add",
        description="テンプレートのワードをサーバー共通一覧に追加します",
    )
    @app_commands.describe(template_key="追加するテンプレートキー")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def word_template_add(
        self,
        interaction: discord.Interaction,
        template_key: str,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        row = await self.get_template(template_key.strip().casefold())
        if row is None:
            await interaction.response.send_message(
                "テンプレートが見つかりません。/word-template-list を確認してください。",
                ephemeral=True,
            )
            return

        words = list(dict.fromkeys(
            word.strip().casefold()
            for word in row["words"]
            if word and word.strip() and len(word.strip()) <= 200
        ))

        if not words:
            await interaction.response.send_message(
                "追加できるワードがありません。",
                ephemeral=True,
            )
            return

        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO server_blocked_words
                    (guild_id, word, created_by)
                SELECT $1, word, $3
                FROM UNNEST($2::TEXT[]) AS word
                ON CONFLICT (guild_id, word) DO NOTHING
                """,
                interaction.guild_id,
                words,
                interaction.user.id,
            )

        added = int(result.split()[-1])
        skipped = len(words) - added

        await interaction.response.send_message(
            f"テンプレート「{row['name']}」の処理が完了しました。\n"
            f"新規追加：{added}件\n"
            f"登録済みなどで未追加：{skipped}件",
            ephemeral=True,
        )

    @app_commands.command(
        name="word-admin-exempt",
        description="管理者を禁止ワード検知の対象外にするか設定します",
    )
    @app_commands.describe(enabled="Trueなら管理者を除外します")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def word_admin_exempt(
        self,
        interaction: discord.Interaction,
        enabled: bool,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO word_filter_settings
                    (guild_id, admin_exempt)
                VALUES ($1, $2)
                ON CONFLICT (guild_id)
                DO UPDATE SET admin_exempt = EXCLUDED.admin_exempt
                """,
                interaction.guild_id,
                enabled,
            )

        status = "管理者を検知対象から除外します。" if enabled else "管理者も検知対象にします。"
        await interaction.response.send_message(status, ephemeral=True)

    @app_commands.command(
        name="word-mode",
        description="禁止ワードの適用範囲を設定します",
    )
    @app_commands.describe(mode="禁止ワードを適用する範囲")
    @app_commands.choices(
        mode=[
            app_commands.Choice(
                name="チャンネル指定",
                value="channel_only",
            ),
            app_commands.Choice(
                name="サーバー共通（除外チャンネルを除く）",
                value="except_channels",
            ),
            app_commands.Choice(
                name="サーバー全体",
                value="server_all",
            ),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def word_mode(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO word_filter_settings
                    (guild_id, filter_mode)
                VALUES ($1, $2)
                ON CONFLICT (guild_id)
                DO UPDATE SET filter_mode = EXCLUDED.filter_mode
                """,
                interaction.guild_id,
                mode.value,
            )

        labels = {
            "channel_only": "チャンネル指定",
            "except_channels": "サーバー共通（除外チャンネルを除く）",
            "server_all": "サーバー全体",
        }
        await interaction.response.send_message(
            f"禁止ワードの適用範囲を「{labels[mode.value]}」に設定しました。",
            ephemeral=True,
        )

    @app_commands.command(
        name="word-exclude-add",
        description="禁止ワードの検知からチャンネルを除外します",
    )
    @app_commands.describe(channel="除外するチャンネル")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def word_exclude_add(
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
                INSERT INTO word_filter_excluded_channels
                    (guild_id, channel_id, created_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (guild_id, channel_id) DO NOTHING
                """,
                interaction.guild_id,
                channel.id,
                interaction.user.id,
            )

        text = (
            f"{channel.mention} はすでに除外されています。"
            if result.endswith(" 0")
            else f"{channel.mention} を検知対象から除外しました。"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(
        name="word-exclude-remove",
        description="除外チャンネルを検知対象に戻します",
    )
    @app_commands.describe(channel="対象チャンネル")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def word_exclude_remove(
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
                DELETE FROM word_filter_excluded_channels
                WHERE guild_id = $1 AND channel_id = $2
                """,
                interaction.guild_id,
                channel.id,
            )

        text = (
            f"{channel.mention} は除外リストにありません。"
            if result.endswith(" 0")
            else f"{channel.mention} を除外リストから削除しました。"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(
        name="word-exclude-list",
        description="禁止ワードの除外チャンネル一覧を表示します",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def word_exclude_list(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "サーバー内で使用してください。",
                ephemeral=True,
            )
            return

        ids = sorted(await self.get_excluded_channel_ids(interaction.guild_id))
        channels = [
            interaction.guild.get_channel(channel_id)
            for channel_id in ids
        ]
        lines = [
            channel.mention if channel else f"`{channel_id}`（チャンネル未取得）"
            for channel_id, channel in zip(ids, channels)
        ]

        await interaction.response.send_message(
            "**除外チャンネル一覧**\n" + ("\n".join(lines) if lines else "除外チャンネルはありません。"),
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
            if bot_member is None and self.bot.user is not None:
                bot_member = interaction.guild.get_member(self.bot.user.id)

            permissions = (
                channel.permissions_for(bot_member)
                if bot_member else None
            )
            if (
                permissions is None
                or not permissions.send_messages
                or not permissions.embed_links
            ):
                await interaction.response.send_message(
                    "Botに「メッセージを送信」と「埋め込みリンク」の権限が必要です。",
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
            text = f"Embedログの送信先を {channel.mention} に設定しました。"
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
            text = "禁止ワードのログ送信を解除しました。"

        await interaction.response.send_message(text, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        settings = await self.get_filter_settings(message.guild.id)

        # ログチャンネル自身のメッセージは検知しない
        if settings["log_channel_id"] == message.channel.id:
            return

        # 管理者除外
        if (
            settings["admin_exempt"]
            and isinstance(message.author, discord.Member)
            and message.author.guild_permissions.administrator
        ):
            return

        # 適用除外チャンネル
        excluded_ids = await self.get_excluded_channel_ids(message.guild.id)
        if message.channel.id in excluded_ids:
            return

        mode = settings["filter_mode"]

        if mode == "channel_only":
            words = await self.get_words(
                message.guild.id,
                message.channel.id,
            )
        elif mode in ("except_channels", "server_all"):
            words = await self.get_server_words(message.guild.id)
        else:
            # 不正な設定値の場合は安全側に倒してチャンネル指定にする
            words = await self.get_words(
                message.guild.id,
                message.channel.id,
            )

        if not words:
            return

        content = message.content.casefold()
        matched_word = next(
            (word for word in words if word.casefold() in content),
            None,
        )
        if matched_word is None:
            return

        try:
            await message.delete()
        except discord.NotFound:
            return
        except (discord.Forbidden, discord.HTTPException):
            print(
                "[WordFilter] メッセージ削除に失敗しました: "
                f"guild={message.guild.id}, message={message.id}"
            )
            return

        await self.send_log(message, matched_word)


async def setup(bot: commands.Bot):
    cog = WordFilter(bot)
    await cog.setup_database()
    await bot.add_cog(cog)
