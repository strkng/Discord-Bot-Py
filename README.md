# Update

## v1.0.1
* OAuth2 state を secrets.token_urlsafe() でランダム生成
* callbackでstateを照合
* 元のguild_id_role_idはFlask Sessionに保存
* CLIENT_SECRET未設定を検出
* DISCORD_TOKEN未設定を検出
* Discord APIを/api/v10に統一
* requestsにtimeout=10
* Discord APIのHTTPステータスを確認
* JSONが壊れていた場合も処理
* Cog読み込みをon_ready()からsetup_hook()へ移動
* Slash Command同期をsetup_hook()へ移動
* PostgreSQL接続もsetup_hook()へ移動
* ロール付与失敗時にユーザーにも失敗を表示
