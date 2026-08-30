import discord
from discord import app_commands
from discord.ext import commands

# 実行を許可するユーザーのID
ALLOWED_USER_ID = [
  1458334854935744533,
  1526850798653276342,
]


class Massping(commands.Cog):

  def __init__(self, bot):
    self.bot = bot

  @app_commands.command(
      name="massping", description="指定したロールのメンバー全員にメンションを送信します"
  )
  @app_commands.describe(
      role="メンションを送りたいロールを選択してください",
      message="メンバーに送るメッセージ（任意）を入力してください",
  )
  async def massping(
      self,
      interaction: discord.Interaction,
      role: discord.Role,
      message: str = "一括メンション通知です。",
  ):
    # 権限チェック（指定されたID以外お断り）
    if interaction.user.id != ALLOWED_USER_ID:
      await interaction.response.send_message(
          "❌ このコマンドを実行する権限がありません。", ephemeral=True
      )
      return

    if not interaction.guild:
      await interaction.response.send_message(
          "❌ このコマンドはサーバー内でのみ使用できます。", ephemeral=True
      )
      return

    # 対象ロールを持っているメンバーを抽出（Botは除外）
    members = [m for m in role.members if not m.bot]

    if not members:
      await interaction.response.send_message(
          f"❌ ロール **{role.name}** に所属しているメンバー（Botを除く）がいません。",
          ephemeral=True,
      )
      return

    # 処理に時間がかかる場合があるため一時応答
    await interaction.response.defer(ephemeral=True)

    success_count = 0
    fail_count = 0

    # メンバーへ個別にDMまたはチャンネルへ送信する仕様など、元の仕様に合わせて調整できますが
    # ここでは安全にメッセージとロールメンバーのメンションを組み立てて送信する例にします
    # ※大量のメンションを一度に送るとDiscordの制限に引っかかる可能性があるため、
    # 　必要に応じてテキストや分割送信にアレンジしてください。
    
    # メンション文字列の作成（Discordの制限に配慮して人数が多い場合は注意）
    mentions_text = " ".join([m.mention for m in members])
    
    content = f"📢 **{role.name}** への一括メンション\n{message}\n\n{mentions_text}"

    try:
      # 文字数が長すぎる場合の分割処理（2000文字制限対策）
      if len(content) > 2000:
        await interaction.followup.send(
            f"❌ 対象メンバーが多すぎるため、メッセージの文字数がDiscordの制限（2000文字）を超えています。",
            ephemeral=True,
        )
        return

      await interaction.channel.send(content)
      await interaction.followup.send(
          f"✅ **{len(members)}名** に向けて一括メンションを送信しました！", ephemeral=True
      )

    except Exception as e:
      await interaction.followup.send(
          f"❌ 送信中にエラーが発生しました: {e}", ephemeral=True
      )


async def setup(bot):
  await bot.add_cog(Massping(bot))
