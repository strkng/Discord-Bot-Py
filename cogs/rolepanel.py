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

        if interaction.channel_id is None:
            await interaction.response.send_message(
                "❌ チャンネルを取得できませんでした。",
                ephemeral=True,
            )
            return

        # -----------------------------------------------------
        # ロール一覧作成
        # -----------------------------------------------------
        selected_roles = [role1]

        if role2 is not None:
            selected_roles.append(role2)

        if role3 is not None:
            selected_roles.append(role3)

        # -----------------------------------------------------
        # 重複ロール除去
        # -----------------------------------------------------
        role_ids = list(dict.fromkeys(
            role.id for role in selected_roles
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
                "❌ 有効なロールが指定されていません。",
                ephemeral=True,
            )
            return

        # -----------------------------------------------------
        # Botのロール位置チェック
        # -----------------------------------------------------
        me = guild.me

        if me is not None:
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
            (role.id, role.name)
            for role in selected_roles
        ]

        view = RolePanelView(
            guild_id=guild.id,
            roles=roles_for_view,
        )

        # -----------------------------------------------------
        # Discordへパネル送信
        # -----------------------------------------------------
        try:
            await interaction.response.send_message(
                "🎫 **ロール取得パネル**\n\n"
                "下のボタンから取得したいロールを選択してください。",
                view=view,
            )

            # Discordが作成したメッセージを取得
            message = await interaction.original_response()

        except Exception as e:
            print(
                f"❌ rolepanel メッセージ送信エラー: {e}",
                flush=True,
            )

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ ロールパネルの送信に失敗しました。",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "❌ ロールパネルの送信に失敗しました。",
                        ephemeral=True,
                    )
            except Exception as notify_error:
                print(
                    f"❌ エラー通知失敗: {notify_error}",
                    flush=True,
                )

            return

        # -----------------------------------------------------
        # メッセージIDを含めてDB登録
        # -----------------------------------------------------
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
            print(
                f"❌ rolepanel DB登録エラー: {e}",
                flush=True,
            )

            # DB登録できなかったパネルをDiscordから削除
            try:
                await message.delete()

            except Exception as delete_error:
                print(
                    f"❌ パネル削除エラー: {delete_error}",
                    flush=True,
                )

            try:
                await interaction.followup.send(
                    "❌ ロールパネルのDB登録に失敗しました。",
                    ephemeral=True,
                )
            except Exception as notify_error:
                print(
                    f"❌ エラー通知失敗: {notify_error}",
                    flush=True,
                )

            return
