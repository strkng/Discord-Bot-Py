import asyncio
from datetime import datetime

import discord
from discord.ext import commands, tasks

=========================================================

メモリ上のデータ

=========================================================

user_message_history = {}
user_last_warn_timestamp = {}

=========================================================

レベル計算

=========================================================

def get_level_info(count):
level = 0

while True:
    req = 10 + (level * level * 2)
    if count >= req:
        count -= req
        level += 1
    else:
        return {
            "level": level,
            "current": count,
            "required": req
        }

=========================================================

Events Cog

=========================================================

class Events(commands.Cog):

def __init__(self, bot):
    self.bot = bot
    self.scheduled_message_loop.start()
# =====================================================
# Unload
# =====================================================
def cog_unload(self):
    self.scheduled_message_loop.cancel()
# =====================================================
# 予約メッセージ送信ループ
# =====================================================
@tasks.loop(minutes=1)
async def scheduled_message_loop(self):
    pool = getattr(self.bot, "pool", None)
    if not pool:
        return
    try:
        now = datetime.now()
        res = await pool.fetch(
            """
            SELECT *
            FROM scheduled_messages
            WHERE send_at <= $1
            """,
            now
        )
        for row in res:
            channel = self.bot.get_channel(
                int(row["channel_id"])
            )
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(
                        int(row["channel_id"])
                    )
                except Exception:
                    channel = None
            if channel:
                try:
                    await channel.send(
                        row["message_content"]
                    )
                except Exception as e:
                    print(
                        "予約メッセージ送信エラー:",
                        e
                    )
            await pool.execute(
                """
                DELETE FROM scheduled_messages
                WHERE id = $1
                """,
                row["id"]
            )
    except Exception as e:
        print(
            "予約メッセージエラー:",
            e
        )
# =====================================================
# 予約メッセージループ開始前
# =====================================================
@scheduled_message_loop.before_loop
async def before_scheduled_message_loop(self):
    await self.bot.wait_until_ready()
# =====================================================
# Ready
# =====================================================
@commands.Cog.listener()
async def on_ready(self):
    print(
        f"{self.bot.user} でログインしました！"
    )
    guild_count = len(
        self.bot.guilds
    )
    await self.bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{guild_count} 個のサーバーで稼働",
        )
    )
# =====================================================
# メッセージイベント
# =====================================================
@commands.Cog.listener()
async def on_message(
    self,
    message: discord.Message
):
    # Bot・DMは除外
    if message.author.bot or not message.guild:
        return
    pool = getattr(
        self.bot,
        "pool",
        None
    )
    if not pool:
        return
    # =================================================
    # 荒らし対策
    # =================================================
    try:
        raid_check = await pool.fetchrow(
            """
            SELECT enabled
            FROM antiraid_settings
            WHERE guild_id = $1
            """,
            str(message.guild.id),
        )
        if raid_check and raid_check["enabled"]:
            # -----------------------------------------
            # 大量メンション
            # -----------------------------------------
            if (
                len(message.mentions) >= 5
                or len(message.role_mentions) >= 5
            ):
                try:
                    await message.delete()
                except Exception as err:
                    print(
                        "メッセージ削除エラー(メンション):",
                        err
                    )
                user_id = str(
                    message.author.id
                )
                now_ts = (
                    datetime.now().timestamp()
                    * 1000
                )
                last_warn = (
                    user_last_warn_timestamp.get(
                        user_id,
                        0
                    )
                )
                if (
                    now_ts - last_warn
                    > 3000
                ):
                    user_last_warn_timestamp[
                        user_id
                    ] = now_ts
                    warn = await message.channel.send(
                        f"🛡️ {message.author.mention}"
                        " さんのメッセージは荒らし対策"
                        "（大量メンション検知）により"
                        "削除されました。"
                    )
                    asyncio.create_task(
                        self._delete_later(
                            warn,
                            5
                        )
                    )
                return
            # -----------------------------------------
            # 短時間連投
            # 3秒以内に5回以上
            # -----------------------------------------
            user_id = str(
                message.author.id
            )
            now_ts = (
                datetime.now().timestamp()
                * 1000
            )
            if user_id not in user_message_history:
                user_message_history[
                    user_id
                ] = []
            history = user_message_history[
                user_id
            ]
            history.append(
                {
                    "timestamp": now_ts,
                    "messageId": str(
                        message.id
                    )
                }
            )
            history = [
                item
                for item in history
                if (
                    now_ts
                    - item["timestamp"]
                    <= 3000
                )
            ]
            user_message_history[
                user_id
            ] = history
            if len(history) >= 5:
                for item in history:
                    try:
                        msg_to_delete = (
                            await message.channel.fetch_message(
                                int(
                                    item["messageId"]
                                )
                            )
                        )
                        if msg_to_delete:
                            await msg_to_delete.delete()
                    except Exception as err:
                        print(
                            "連投メッセージ一括削除エラー:",
                            err
                        )
                last_warn = (
                    user_last_warn_timestamp.get(
                        user_id,
                        0
                    )
                )
                if (
                    now_ts - last_warn
                    > 3000
                ):
                    user_last_warn_timestamp[
                        user_id
                    ] = now_ts
                    warn = await message.channel.send(
                        f"🛡️ {message.author.mention}"
                        " さんのメッセージは荒らし対策"
                        "（短時間の連投検知）により"
                        "削除されました。"
                    )
                    asyncio.create_task(
                        self._delete_later(
                            warn,
                            5
                        )
                    )
                user_message_history[
                    user_id
                ] = []
                return
    except Exception as e:
        print(
            "荒らし対策エラー:",
            e
        )
    # =================================================
    # メッセージ数カウント
    # =================================================
    try:
        res = await pool.fetchrow(
            """
            INSERT INTO message_counts
                (user_id, guild_id, count)
            VALUES
                ($1, $2, 1)
            ON CONFLICT(user_id, guild_id)
            DO UPDATE SET
                count =
                    message_counts.count + 1
            RETURNING count;
            """,
            str(message.author.id),
            str(message.guild.id),
        )
        new_count = res["count"]
        # =================================================
        # レベルアップ
        # =================================================
        old_info = get_level_info(
            new_count - 1
        )
        new_info = get_level_info(
            new_count
        )
        if (
            new_info["level"]
            > old_info["level"]
        ):
            set_res = await pool.fetchrow(
                """
                SELECT level_channel_id
                FROM guild_settings
                WHERE guild_id = $1
                """,
                str(message.guild.id),
            )
            target_channel = (
                message.channel
            )
            if (
                set_res
                and set_res["level_channel_id"]
            ):
                ch = (
                    message.guild.get_channel(
                        int(
                            set_res[
                                "level_channel_id"
                            ]
                        )
                    )
                )
                if ch:
                    target_channel = ch
            try:
                await target_channel.send(
                    f"🎉 {message.author.mention}"
                    f" おめでとうございます！"
                    f"レベル **{new_info['level']}**"
                    f" にアップしました！"
                )
            except Exception as e:
                print(
                    "レベルアップ通知エラー:",
                    e
                )
        # =================================================
        # メッセージ数報酬
        # =================================================
        await self.check_message_rewards(
            message,
            new_count,
            pool
        )
    except Exception as e:
        print(
            "レベル・メッセージ数処理エラー:",
            e
        )
    # =================================================
    # スティッキーメッセージ
    # =================================================
    try:
        sticky_res = await pool.fetch(
            """
            SELECT *
            FROM sticky_messages
            WHERE channel_id = $1
            """,
            str(message.channel.id),
        )
        if sticky_res:
            sticky = sticky_res[0]
            if sticky["message_id"]:
                try:
                    old_msg = (
                        await message.channel.fetch_message(
                            int(
                                sticky["message_id"]
                            )
                        )
                    )
                    if old_msg:
                        await old_msg.delete()
                except Exception:
                    pass
            embed = discord.Embed(
                title=sticky["title"],
                description=sticky["description"],
                color=discord.Color.from_str(
                    "#3498db"
                ),
            )
            embed.timestamp = datetime.now()
            new_msg = await message.channel.send(
                embed=embed
            )
            await pool.execute(
                """
                UPDATE sticky_messages
                SET message_id = $1
                WHERE channel_id = $2
                """,
                str(new_msg.id),
                str(message.channel.id),
            )
    except Exception:
        pass
# =====================================================
# メッセージ数報酬チェック
# =====================================================
async def check_message_rewards(
    self,
    message: discord.Message,
    message_count: int,
    pool
):
    guild = message.guild
    member = message.author
    # =================================================
    # 達成記録テーブルを確実に用意
    # =================================================
    try:
        await pool.execute(
            """
            CREATE TABLE IF NOT EXISTS level_reward_claims (
                reward_guild_id TEXT NOT NULL,
                required_messages BIGINT NOT NULL,
                user_id TEXT NOT NULL,
                claimed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (
                    reward_guild_id,
                    required_messages,
                    user_id
                )
            )
            """
        )
    except Exception as e:
        print(
            "メッセージ報酬達成テーブル作成エラー:",
            e
        )
        return
    # =================================================
    # 報酬設定取得
    #
    # /level-role set が使用する
    # level_role_rewards に合わせる
    # =================================================
    try:
        rewards = await pool.fetch(
            """
            SELECT
                guild_id,
                required_messages,
                add_role_id,
                remove_role_id,
                notification_mode,
                notification_channel_id
            FROM level_role_rewards
            WHERE guild_id = $1
              AND required_messages <= $2
            ORDER BY required_messages ASC
            """,
            str(guild.id),
            message_count
        )
    except Exception as e:
        print(
            "メッセージ報酬設定取得エラー:",
            e
        )
        return
    if not rewards:
        return
    # =================================================
    # 各報酬を処理
    # =================================================
    for reward in rewards:
        required_count = int(
            reward["required_messages"]
        )
        # =================================================
        # 既に達成済みか確認
        # =================================================
        try:
            already_claimed = await pool.fetchval(
                """
                SELECT 1
                FROM level_reward_claims
                WHERE reward_guild_id = $1
                  AND required_messages = $2
                  AND user_id = $3
                LIMIT 1
                """,
                str(guild.id),
                required_count,
                str(member.id)
            )
        except Exception as e:
            print(
                "報酬達成状態確認エラー:",
                e
            )
            continue
        if already_claimed:
            continue
        # =================================================
        # 付与ロール取得
        # =================================================
        give_role = None
        if reward["add_role_id"]:
            try:
                give_role = guild.get_role(
                    int(
                        reward[
                            "add_role_id"
                        ]
                    )
                )
                if not give_role:
                    give_role = (
                        await guild.fetch_role(
                            int(
                                reward[
                                    "add_role_id"
                                ]
                            )
                        )
                    )
            except Exception as e:
                print(
                    "付与ロール取得エラー:",
                    e
                )
                give_role = None
        # =================================================
        # 付与ロールが存在しない場合
        # =================================================
        if not give_role:
            print(
                f"メッセージ報酬ロールが見つかりません: "
                f"guild={guild.id}, "
                f"messages={required_count}, "
                f"role={reward['add_role_id']}"
            )
            continue
        # =================================================
        # ロール付与
        # =================================================
        try:
            if give_role not in member.roles:
                await member.add_roles(
                    give_role,
                    reason=(
                        "メッセージ数報酬"
                        f" ({required_count} messages)"
                    )
                )
        except discord.Forbidden:
            print(
                "ロール付与権限がありません:",
                give_role.id
            )
            # 付与できていないので
            # 達成済みにはしない
            continue
        except Exception as e:
            print(
                "ロール付与エラー:",
                e
            )
            continue
        # =================================================
        # ロール削除
        #
        # remove_role_id が NULL の場合は
        # 何もしない
        # =================================================
        remove_role = None
        if reward["remove_role_id"]:
            try:
                remove_role = guild.get_role(
                    int(
                        reward[
                            "remove_role_id"
                        ]
                    )
                )
                if not remove_role:
                    try:
                        remove_role = (
                            await guild.fetch_role(
                                int(
                                    reward[
                                        "remove_role_id"
                                    ]
                                )
                            )
                        )
                    except Exception:
                        remove_role = None
            except Exception as e:
                print(
                    "削除ロール取得エラー:",
                    e
                )
                remove_role = None
            if remove_role:
                try:
                    if remove_role in member.roles:
                        await member.remove_roles(
                            remove_role,
                            reason=(
                                "メッセージ数報酬"
                                f" ({required_count} messages)"
                            )
                        )
                except discord.Forbidden:
                    print(
                        "ロール削除権限がありません:",
                        remove_role.id
                    )
                except Exception as e:
                    print(
                        "ロール削除エラー:",
                        e
                    )
        # =================================================
        # 達成記録
        #
        # ロール付与が成功した後に記録
        # =================================================
        try:
            await pool.execute(
                """
                INSERT INTO level_reward_claims
                    (
                        reward_guild_id,
                        required_messages,
                        user_id
                    )
                VALUES
                    ($1, $2, $3)
                ON CONFLICT
                    (
                        reward_guild_id,
                        required_messages,
                        user_id
                    )
                DO NOTHING
                """,
                str(guild.id),
                required_count,
                str(member.id)
            )
        except Exception as e:
            print(
                "報酬達成記録エラー:",
                e
            )
            # 記録できなかった場合でも
            # 今回の通知は行う
            # 次回以降は再判定される可能性がある
        # =================================================
        # 通知先
        # =================================================
        notification_channel = None
        notification_mode = (
            reward["notification_mode"]
            or "source"
        )
        # -------------------------------------------------
        # 達成したメッセージのチャンネル
        # -------------------------------------------------
        if notification_mode == "source":
            notification_channel = (
                message.channel
            )
        # -------------------------------------------------
        # 指定チャンネル
        # -------------------------------------------------
        elif notification_mode == "channel":
            if reward[
                "notification_channel_id"
            ]:
                try:
                    notification_channel = (
                        guild.get_channel(
                            int(
                                reward[
                                    "notification_channel_id"
                                ]
                            )
                        )
                    )
                    if not notification_channel:
                        notification_channel = (
                            await self.bot.fetch_channel(
                                int(
                                    reward[
                                        "notification_channel_id"
                                    ]
                                )
                            )
                        )
                except Exception as e:
                    print(
                        "メッセージ報酬通知先取得エラー:",
                        e
                    )
                    notification_channel = None
            # 指定チャンネルが取得できない場合
            # 達成したチャンネルへフォールバック
            if not notification_channel:
                notification_channel = (
                    message.channel
                )
        # -------------------------------------------------
        # 不明な設定の場合
        # -------------------------------------------------
        else:
            notification_channel = (
                message.channel
            )
        # =================================================
        # 通知
        # =================================================
        if notification_channel:
            try:
                role_text = (
                    f"\n🎁 付与されたロール："
                    f" {give_role.mention}"
                )
                if remove_role:
                    role_text += (
                        f"\n🔄 削除されたロール："
                        f" {remove_role.mention}"
                    )
                await notification_channel.send(
                    f"🎉 {member.mention} "
                    f"さんが **{required_count:,}メッセージ** "
                    f"を達成しました！"
                    f"{role_text}"
                )
            except Exception as e:
                print(
                    "メッセージ報酬通知エラー:",
                    e
                )
# =====================================================
# メッセージ削除
# =====================================================
async def _delete_later(
    self,
    message,
    seconds
):
    await asyncio.sleep(
        seconds
    )
    try:
        await message.delete()
    except Exception:
        pass
# =====================================================
# VC自己紹介
# =====================================================
@commands.Cog.listener()
async def on_voice_state_update(
    self,
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if before.channel == after.channel:
        return
    guild = member.guild
    pool = getattr(
        self.bot,
        "pool",
        None
    )
    if not pool:
        return
    try:
        setting_res = await pool.fetchrow(
            """
            SELECT
                source_channel_id,
                keyword
            FROM intro_channel_settings
            WHERE guild_id = $1
            """,
            str(guild.id),
        )
        if not setting_res:
            return
        source_channel_id = (
            setting_res[
                "source_channel_id"
            ]
        )
        keyword = (
            setting_res["keyword"]
        )
        source_channel = (
            guild.get_channel(
                int(source_channel_id)
            )
        )
        if not source_channel:
            return
        messages = [
            m
            async for m in source_channel.history(
                limit=100
            )
        ]
        async def update_vc_intro(
            channel
        ):
            if not channel:
                return
            members = [
                m
                for m in channel.members
                if not m.bot
            ]
            text = "参加メンバー\n\n"
            if not members:
                text += (
                    "現在、誰も参加していません。"
                )
            else:
                for m in members:
                    user_msg = next(
                        (
                            msg
                            for msg in messages
                            if (
                                msg.author.id
                                == m.id
                            )
                            and (
                                keyword in msg.content
                                if keyword
                                else
                                "名前：" in msg.content
                            )
                        ),
                        None,
                    )
                    if user_msg:
                        content = (
                            user_msg.content[:80]
                            + "..."
                            if len(
                                user_msg.content
                            ) > 80
                            else user_msg.content
                        )
                    else:
                        content = (
                            "（自己紹介がありません）"
                        )
                    text += (
                        f"• **{m.display_name}** :\n"
                        f"{content}\n\n"
                    )
            try:
                vc_msgs = [
                    m
                    async for m in channel.history(
                        limit=30
                    )
                ]
                existing = next(
                    (
                        m
                        for m in vc_msgs
                        if (
                            m.author.id
                            == self.bot.user.id
                        )
                        and m.content.startswith(
                            "参加メンバー"
                        )
                    ),
                    None,
                )
                if existing:
                    await existing.edit(
                        content=text
                    )
                else:
                    await channel.send(
                        text
                    )
            except Exception as err:
                print(
                    "VC更新エラー:",
                    err
                )
        if before.channel:
            await update_vc_intro(
                before.channel
            )
        if after.channel:
            await update_vc_intro(
                after.channel
            )
    except Exception as e:
        print(
            "VCイベントエラー:",
            e
        )

=========================================================

Setup

=========================================================

async def setup(bot):

await bot.add_cog(
    Events(bot)
)
