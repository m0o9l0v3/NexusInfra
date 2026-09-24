# 初回導入と更新（人間の承認後）

## 適用前の事実確認

VPSへ人間が接続し、`docker ps`、`docker inspect`、`docker volume ls`、DBのメジャー版・データ配置・既存データ・現行Compose・バックアップ状態を記録する。この文書のテンプレートを既存volumeへ直結する前に、`nexus-mobile/codex/production-db-foundation`との差分をレビューする。特にpostgres image、volumeマウント先、pgBackRest用volume、初期ロール、secret名を合わせる。別構成なら起動しない。

DNS、API/Studioドメイン、ACMEメール、イメージdigest、CORS、データベース接続先と権限を確定する。アプリの別MRをマージして本番イメージとhealthcheckを検証する。既存のデータとバックアップを独立した場所に保全し、復元試験を済ませる。起動時にDBをseedまたはmigrationしないことを確認する。

## 私有ファイル

`/srv/nexus`にレビュー済みcommitを配置し、rootが所有する`/etc/nexus/production.env`に`.env.example`の値を設定する。`NEXUS_SECRET_DIR`はroot所有・0700のディレクトリ。各秘密ファイルは0600。ファイル名: postgres_password、public_connection、admin_connection、studio_connection、admin_login、signing_key、audit_key、one_time_key、qr_key。Compose secretsは暗号化ストレージではなくファイルマウントなので、VPSへのアクセス権も制限する。SSH秘密鍵、DBパスワード、接続文字列、APIキー、TLS秘密鍵をGitLab repo・CIログ・Issueへ貼らない。

GitLab protected variablesはイメージ参照、ドメイン、承認フラグなどのCI専用値を対象とし、秘密値はmasked/file変数で扱う。ただし現行CIは本番デプロイを実行しないため、秘密をrunnerへ渡す必要はない。秘密のVPS投入経路は人間が決定する。

環境ファイルを読み込んだ上で`docker compose config --quiet`、`caddy validate`を実施。例えば`set -a; . /etc/nexus/production.env; set +a`としてから実行する。ファイルはroot管理の信頼できるシェル構文とする。次にメンテナンス窓で`docker compose pull`、`docker compose up -d`を人間が実行する。Composeは`down -v`を使わない。公開ポートを`docker compose ps`とホスト`ss -lnt`で確認。DB、API、Studio、Caddy、TLS、iOSのAPI利用、ログインと管理機能を確認する。認証済みの管理操作は外部からの権限も試す。

更新時は事前バックアップとMacコピーの成功を確認し、適用前の全image digestと環境ファイル版を保存する。API/StudioのCIがlint/test→build→image build→registry pushを担当。本repoのMRでdigestを更新し、検証CIとdiffを確認後、protected production environmentの手動承認を経て人間が適用する。失敗時は前のdigestへ戻して`docker compose up -d`。スキーマ変更後のロールバックは別判断とする。
