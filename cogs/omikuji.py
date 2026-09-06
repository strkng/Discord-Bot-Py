from datetime import datetime
import random

import discord
from discord import app_commands
from discord.ext import commands
from zoneinfo import ZoneInfo


omikuji_results = [
    "大吉 ",
    "中吉 ",
    "中吉 ",
    "小吉 ",
    "小吉 ",
    "吉 ",
    "吉 ",
    "吉 ",
    "凶 ",
    "凶 ",
]


class Omikuji(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="omikuji",
        description="今日のおみくじを引きます（1日1回限定）"
    )
    async def omikuji(self, interaction: discord.Interaction):

        print(
            f"[Omikuji] /omikuji 実行 "
            f"user={interaction.user.id} "
            f"guild={interaction.guild_id}",
            flush=True
        )

        # ----------------------------------------
        # Discordへの初回応答
        # ----------------------------------------
        try:
            print(
                "[Omikuji] interaction.response.defer() 開始",
                flush=True
            )

            await interaction.response.defer()

            print(
                "[Omikuji] interaction.response.defer() 成功",
                flush=True
            )

        except discord.HTTPException as e:

            print(
                f"[Omikuji] defer() HTTP error "
                f"status={e.status} "
                f"error={e}",
                flush=True
            )

            if e.status == 429:
                print(
                    "[Omikuji] Discord API 429 "
                    "初回応答に失敗しました。",
                    flush=True
                )

            return

        except Exception as e:

            print(
                f"[Omikuji] defer() unexpected error: "
                f"{type(e).__name__}: {e}",
                flush=True
            )

            return

        # ----------------------------------------
        # Guild / User
        # ----------------------------------------
        guild_id = (
            str(interaction.guild_id)
            if interaction.guild
            else None
        )

        user_id = str(interaction.user.id)

        print(
            f"[Omikuji] User/Guild取得完了 "
            f"user={user_id} guild={guild_id}",
            flush=True
        )

        # ----------------------------------------
        # 日本時間
        # ----------------------------------------
        today_str = datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).strftime("%Y/%m/%d")

        print(
            f"[Omikuji] 今日の日付: {today_str}",
            flush=True
        )

        # ----------------------------------------
        # PostgreSQL
        # ----------------------------------------
        pool = self.bot.db_pool

        if pool is None:
            print(
                "[Omikuji] ERROR: db_pool が None",
                flush=True
            )

            try:
                await interaction.followup.send(
                    "❌ データベースに接続できていません。"
                )
            except Exception as e:
                print(
                    f"[Omikuji] DBエラー通知にも失敗: {e}",
                    flush=True
                )

            return

        print(
            "[Omikuji] PostgreSQL接続確認OK",
            flush=True
        )

        try:

            # ----------------------------------------
            # 今日すでに引いたか確認
            # ----------------------------------------
            print(
                "[Omikuji] DB検索開始",
                flush=True
            )

            cooldown_res = await pool.fetchrow(
                """
                SELECT last_date
                FROM omikuji_cooldowns
                WHERE user_id = $1
                  AND guild_id = $2
                """,
                user_id,
                guild_id,
            )

            print(
                f"[Omikuji] DB検索完了 result={cooldown_res}",
                flush=True
            )

            # ----------------------------------------
            # 1日1回制限
            # ----------------------------------------
            if (
                cooldown_res
                and cooldown_res["last_date"] == today_str
            ):
                print(
                    "[Omikuji] 本日は既におみくじ済み",
                    flush=True
                )

                embed_error = discord.Embed(
                    title="❌ おみくじは1日1回まで",
                    description=(
                        "今日のおみくじは既に引いています！\n"
                        "また明日引いてね！"
                    ),
                    color=discord.Color.from_str("#ff4757"),
                )

                embed_error.timestamp = datetime.now(
                    ZoneInfo("Asia/Tokyo")
                )

                await interaction.followup.send(
                    embed=embed_error
                )

                print(
                    "[Omikuji] 再抽選防止メッセージ送信完了",
                    flush=True
                )

                return

            # ----------------------------------------
            # おみくじ決定
            # ----------------------------------------
            fortune = random.choice(omikuji_results)

            print(
                f"[Omikuji] おみくじ結果決定: {fortune}",
                flush=True
            )

            # ----------------------------------------
            # DB更新
            # ----------------------------------------
            print(
                "[Omikuji] DB更新開始",
                flush=True
            )

            await pool.execute(
                """
                INSERT INTO omikuji_cooldowns
                    (user_id, guild_id, last_date)
                VALUES
                    ($1, $2, $3)
                ON CONFLICT(user_id, guild_id)
                DO UPDATE SET
                    last_date = $3
                """,
                user_id,
                guild_id,
                today_str,
            )

            print(
                "[Omikuji] DB更新完了",
                flush=True
            )

        except Exception as e:

            print(
                f"[Omikuji] PostgreSQL error: "
                f"{type(e).__name__}: {e}",
                flush=True
            )

            try:
                await interaction.followup.send(
                    "❌ おみくじの処理中にエラーが発生しました。"
                )
            except Exception as send_error:
                print(
                    f"[Omikuji] エラー通知送信失敗: "
                    f"{type(send_error).__name__}: {send_error}",
                    flush=True
                )

            return

        # ----------------------------------------
        # 結果Embed
        # ----------------------------------------
        embed = discord.Embed(
            title="🎋 おみくじ結果",
            description=(
                f"<@{interaction.user.id}> さんの"
                "今日の運勢は..."
            ),
            color=discord.Color.from_str("#ff4757"),
        )

        embed.add_field(
            name="【運勢】",
            value=f"**{fortune}**",
            inline=False,
        )

        embed.timestamp = datetime.now(
            ZoneInfo("Asia/Tokyo")
        )

        # ----------------------------------------
        # 結果送信
        # ----------------------------------------
        try:

            print(
                "[Omikuji] 結果送信開始",
                flush=True
            )

            await interaction.followup.send(
                embed=embed
            )

            print(
                "[Omikuji] 結果送信成功 🎉",
                flush=True
            )

        except discord.HTTPException as e:

            print(
                f"[Omikuji] followup.send() HTTP error "
                f"status={e.status}: {e}",
                flush=True
            )

            if e.status == 429:
                print(
                    "[Omikuji] 結果送信時にDiscord API 429",
                    flush=True
                )

        except Exception as e:

            print(
                f"[Omikuji] followup.send() error: "
                f"{type(e).__name__}: {e}",
                flush=True
            )


async def setup(bot):
    await bot.add_cog(Omikuji(bot))
