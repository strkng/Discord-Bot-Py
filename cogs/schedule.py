from datetime import datetime, timedelta
import traceback

import discord
from discord import app_commands
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


class Schedule(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

        # 10秒ごとに予約を確認
        self.schedule_worker.start()

    def cog_unload(self):
        self.schedule_worker.cancel()

    # =====================================================
    # Scheduled Message Worker
    # =====================================================

    @tasks.loop(seconds=10)
    async def schedule_worker(self):

        try:

            pool = self.bot.pool

            if pool is None:
                return

            # DBには「JSTのタイムゾーン情報なし」で保存しているため、
            # 比較する現在時刻も同じ形式にする
            now = datetime.now(JST).replace(
                tzinfo=None
            )

            rows = await pool.fetch(
                """
                SELECT
                    id,
                    guild_id,
                    channel_id,
                    author_id,
                    message_content,
                    send_at
                FROM scheduled_messages
                WHERE send_at <= $1
                ORDER BY send_at ASC
                LIMIT 20
                """,
                now,
            )

            if not rows:
                return

            for row in rows:

                reservation_id = row["id"]

                channel_id = row["channel_id"]

                message_content = row["message_content"]

                try:

                    # -------------------------------------------------
                    # チャンネル取得
                    # -------------------------------------------------

                    channel = self.bot.get_channel(
                        int(channel_id)
                    )

                    # キャッシュにない場合はAPIから取得
                    if channel is None:

                        try:

                            channel = await self.bot.fetch_channel(
                                int(channel_id)
                            )

                        except discord.NotFound:

                            print(
                                f"[Schedule] "
                                f"Channel not found: "
                                f"{channel_id}"
                            )

                            continue

                        except discord.Forbidden:

                            print(
                                f"[Schedule] "
                                f"No permission to access channel: "
                                f"{channel_id}"
                            )

                            continue

                    # -------------------------------------------------
                    # メッセージ送信
                    # -------------------------------------------------

                    await channel.send(
                        message_content
                    )

                    print(
                        f"[Schedule] "
                        f"Sent scheduled message: "
                        f"id={reservation_id}, "
                        f"channel={channel_id}"
                    )

                    # -------------------------------------------------
                    # 送信成功後にDBから削除
                    # -------------------------------------------------

                    deleted = await self.bot.pool.fetchrow(
                        """
                        DELETE FROM scheduled_messages
                        WHERE id = $1
                        RETURNING id
                        """,
                        reservation_id,
                    )

                    if deleted:

                        print(
                            f"[Schedule] "
                            f"Deleted completed reservation: "
                            f"id={reservation_id}"
                        )

                except discord.Forbidden:

                    # 権限不足の場合は削除しない
                    # → 管理者が権限を直した後に再送可能
                    print(
                        f"[Schedule] "
                        f"Missing permission: "
                        f"id={reservation_id}"
                    )

                except discord.HTTPException as e:

                    # Discord APIエラーの場合も削除しない
                    print(
                        f"[Schedule] "
                        f"Discord API error: "
                        f"id={reservation_id}, "
                        f"status={e.status}"
                    )

                except Exception:

                    print(
                        f"[Schedule] "
                        f"Failed to process reservation: "
                        f"id={reservation_id}"
                    )

                    traceback.print_exc()

        except Exception:

            print(
                "[Schedule] Worker error:"
            )

            traceback.print_exc()

    @schedule_worker.before_loop
    async def before_schedule_worker(self):

        await self.bot.wait_until_ready()

        print(
            "[Schedule] "
            "Scheduled message worker started."
        )

    # =====================================================
    # /schedule
    # =====================================================

    schedule_group = app_commands.Group(
        name="schedule",
        description="メッセージの送信予約を行います"
    )

    # =====================================================
    # /schedule set
    # =====================================================

    @schedule_group.command(
        name="set",
        description="新しいメッセージの送信を予約します（最大1年以内）"
    )
    @app_commands.describe(
        channel="送信先のチャンネル",
        message="送信するメッセージの内容",
        time="送信日時 (例: 2026-07-30 15:00)",
    )
    @app_commands.checks.has_permissions(
        manage_messages=True
    )
    async def schedule_set(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
        time: str,
    ):

        await interaction.response.defer(
            ephemeral=True
        )

        try:

            guild_id = str(
                interaction.guild_id
            )

            # -------------------------------------------------
            # 日時解析
            # -------------------------------------------------

            normalized_time_str = (
                time.replace(" ", "T")
                + "+09:00"
            )

            try:

                target_date = (
                    datetime.fromisoformat(
                        normalized_time_str
                    )
                )

            except ValueError:

                await interaction.followup.send(
                    "❌ 日時の形式が正しくありません。"
                    "「`YYYY-MM-DD HH:MM`」の形式で入力してください"
                    "（例: `2026-07-30 15:00`）。",
                    ephemeral=True,
                )

                return

            # -------------------------------------------------
            # 時刻チェック
            # -------------------------------------------------

            now = datetime.now(JST)

            one_year_later = (
                now + timedelta(days=365)
            )

            if target_date <= now:

                await interaction.followup.send(
                    "❌ 過去の日時は指定できません。"
                    "未来の時間を設定してください。",
                    ephemeral=True,
                )

                return

            if target_date > one_year_later:

                await interaction.followup.send(
                    "❌ 予約できるのは現在から1年以内までです。",
                    ephemeral=True,
                )

                return

            # -------------------------------------------------
            # JSTのnaive datetimeとして保存
            # -------------------------------------------------

            target_date_jst = (
                target_date
                .astimezone(JST)
                .replace(tzinfo=None)
            )

            pool = self.bot.pool

            if pool is None:

                await interaction.followup.send(
                    "❌ データベースに接続されていません。",
                    ephemeral=True,
                )

                return

            # -------------------------------------------------
            # DB保存
            # -------------------------------------------------

            await pool.execute(
                """
                INSERT INTO scheduled_messages
                (
                    guild_id,
                    channel_id,
                    author_id,
                    message_content,
                    send_at
                )
                VALUES ($1, $2, $3, $4, $5)
                """,
                guild_id,
                str(channel.id),
                str(interaction.user.id),
                message,
                target_date_jst,
            )

            # -------------------------------------------------
            # 完了メッセージ
            # -------------------------------------------------

            formatted_time = (
                target_date.strftime(
                    "%Y/%m/%d %H:%M:%S"
                )
            )

            await interaction.followup.send(
                f"✨ {channel.mention} へのメッセージ送信を "
                f"**{formatted_time}** に予約しました！",
                ephemeral=True,
            )

        except Exception as e:

            traceback.print_exc()

            await interaction.followup.send(
                f"❌ エラーが発生しました: `{e}`",
                ephemeral=True
            )

    # =====================================================
    # /schedule list
    # =====================================================

    @schedule_group.command(
        name="list",
        description="現在登録されている予約一覧を表示します"
    )
    @app_commands.checks.has_permissions(
        manage_messages=True
    )
    async def schedule_list(
        self,
        interaction: discord.Interaction
    ):

        await interaction.response.defer(
            ephemeral=True
        )

        try:

            guild_id = str(
                interaction.guild_id
            )

            pool = self.bot.pool

            if pool is None:

                await interaction.followup.send(
                    "❌ データベースに接続されていません。",
                    ephemeral=True
                )

                return

            res = await pool.fetch(
                """
                SELECT
                    id,
                    channel_id,
                    message_content,
                    send_at
                FROM scheduled_messages
                WHERE guild_id = $1
                ORDER BY send_at ASC
                """,
                guild_id,
            )

            if not res:

                await interaction.followup.send(
                    "📭 このサーバーに登録されている"
                    "予約メッセージはありません。",
                    ephemeral=True,
                )

                return

            list_text = (
                "📋 **現在の予約メッセージ一覧**\n"
            )

            for row in res:

                dt = row["send_at"]

                if dt.tzinfo is None:

                    dt = dt.replace(
                        tzinfo=JST
                    )

                date_str = dt.strftime(
                    "%Y/%m/%d %H:%M:%S"
                )

                content = (
                    row["message_content"]
                )

                preview = (
                    content[:20] + "..."
                    if len(content) > 20
                    else content
                )

                list_text += (
                    f"• **ID: {row['id']}** "
                    f"| チャンネル: <#{row['channel_id']}> "
                    f"| 予定: {date_str}\n"
                    f"  内容: `{preview}`\n"
                )

            await interaction.followup.send(
                list_text,
                ephemeral=True
            )

        except Exception as e:

            traceback.print_exc()

            await interaction.followup.send(
                f"❌ エラーが発生しました: `{e}`",
                ephemeral=True
            )

    # =====================================================
    # /schedule cancel
    # =====================================================

    @schedule_group.command(
        name="cancel",
        description="IDを指定して予約をキャンセルします"
    )
    @app_commands.describe(
        id="キャンセルする予約のID (listコマンドで確認できます)"
    )
    @app_commands.checks.has_permissions(
        manage_messages=True
    )
    async def schedule_cancel(
        self,
        interaction: discord.Interaction,
        id: int
    ):

        await interaction.response.defer(
            ephemeral=True
        )

        try:

            guild_id = str(
                interaction.guild_id
            )

            pool = self.bot.pool

            if pool is None:

                await interaction.followup.send(
                    "❌ データベースに接続されていません。",
                    ephemeral=True
                )

                return

            res = await pool.fetchrow(
                """
                DELETE FROM scheduled_messages
                WHERE id = $1
                  AND guild_id = $2
                RETURNING id
                """,
                id,
                guild_id,
            )

            if not res:

                await interaction.followup.send(
                    f"❌ ID `{id}` の予約が見つからないか、"
                    "このサーバーの予約ではありません。",
                    ephemeral=True,
                )

                return

            await interaction.followup.send(
                f"🗑️ ID `{id}` の予約をキャンセルしました。",
                ephemeral=True
            )

        except Exception as e:

            traceback.print_exc()

            await interaction.followup.send(
                f"❌ エラーが発生しました: `{e}`",
                ephemeral=True
            )


# =========================================================
# Setup
# =========================================================

async def setup(bot):

    await bot.add_cog(
        Schedule(bot)
    )
