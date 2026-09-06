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

        # ----------------------------------------
        # Discordへの初回応答
        # ----------------------------------------
        try:
            await interaction.response.defer()

        except discord.HTTPException as e:

            # Discord Global Rate Limit
            if e.status == 429:
                print(
                    "[Omikuji] Discord API Global Rate Limit (429) "
                    "のため処理を中止しました。"
                )
                return

            # その他のHTTPエラー
            print(
                f"[Omikuji] interaction.response.defer() "
                f"でHTTPエラー: {e}"
            )
            return

        except Exception as e:
            print(
                f"[Omikuji] interaction.response.defer() "
                f"で予期しないエラー: {e}"
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

        # ----------------------------------------
        # 日本時間
        # ----------------------------------------
        today_str = datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).strftime("%Y/%m/%d")

        # ----------------------------------------
        # PostgreSQL
        # ----------------------------------------
        pool = self.bot.db_pool

        # DBプールが存在しない場合
        if pool is None:
            await interaction.followup.send(
                "❌ データベースに接続できていません。"
            )
            return

        try:

            # ----------------------------------------
            # 今日すでに引いたか確認
            # ----------------------------------------
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

            if (
                cooldown_res
                and cooldown_res["last_date"] == today_str
            ):
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
                return

            # ----------------------------------------
            # おみくじ決定
            # ----------------------------------------
            fortune = random.choice(omikuji_results)

            # ----------------------------------------
            # DB更新
            # ----------------------------------------
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

        except Exception as e:

            print(
                f"[Omikuji] PostgreSQL error: {e}"
            )

            try:
                await interaction.followup.send(
                    "❌ おみくじの処理中にエラーが発生しました。"
                )
            except discord.HTTPException:
                pass

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
            await interaction.followup.send(
                embed=embed
            )

        except discord.HTTPException as e:

            if e.status == 429:
                print(
                    "[Omikuji] 結果送信時にDiscord API "
                    "429が発生しました。"
                )
                return

            print(
                f"[Omikuji] followup.send() HTTP error: {e}"
            )

        except Exception as e:
            print(
                f"[Omikuji] followup.send() error: {e}"
            )


async def setup(bot):
    await bot.add_cog(Omikuji(bot))
