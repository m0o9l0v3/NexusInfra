# セキュリティと本番適用前チェック

- [ ] [17→18の移行gate](transition-postgres-17-to-18.md)を満たす。現行`nexus_postgres_data`を18のComposeに指定しない
- [ ] 既存VPSでSSH公開鍵認証、root SSHログイン無効、パスワード認証無効、OS更新、failed units 0を再確認
- [ ] さくらパケットフィルターとホストで22/80/443のみ許可。5432、API、Studio、Docker daemon/APIは非公開
- [ ] Docker socketをコンテナへ渡さず、docker groupへの所属を最小限にする
- [ ] DB接続ユーザーをadmin/public/studio/migratorで分離し、公開APIは管理接続へフォールバックしない
- [ ] 全秘密ファイルはGit外、ディレクトリはroot所有0700。DB entrypoint用はroot所有0600、API用は採用イメージの実行UID所有0400とし、コンテナ内で読み取り確認。共用secretの利用者UIDが異なる場合はファイルとCompose定義を分ける。GitLab protected variables/environmentと承認者を設定
- [ ] Caddyの実ドメイン、DNS、ACME、HTTP→HTTPS、証明書更新、HSTS対象サブドメインを確認
- [ ] Caddy、API、Studioのaccess/errorログを収集し、個人情報・secretが出力されないことを確認
- [ ] 非root実行、不要capability除去、read-only filesystem、volume権限を各イメージで検証。Caddy/DBの書込先は維持
- [ ] 各イメージをdigest固定。依存ライブラリとOS修正版を月次確認し、検証後に更新
- [ ] アプリの`/app/healthcheck`実装とStudio秘密ファイル読み込みが別MRで完了
- [ ] バックアップ成功・失敗の通知/日次確認、Macコピー、暗号鍵の別保管、月次restore試験を確認
- [ ] 4GB VPSでDB、4アプリ、Caddy、backup同時実行時のRAM/swap/CPU/diskを測る
- [ ] CI runnerの本番SSH権限なし、production protected、manual承認、protected/masked/file variablesを確認
- [ ] 破壊的SQL、DB volume削除、`down -v`、SSH/firewall変更はCIに存在しないことを確認
- [ ] 本番適用前のバックアップ、image digest、rollback方法、移行SQL、復旧時間測定記録をレビュー
