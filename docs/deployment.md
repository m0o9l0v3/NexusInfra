# 初回導入と更新（人間の承認後）

## 適用前の事実確認

VPSへ人間が接続し、`docker ps`、`docker inspect`、`docker volume ls`、DBのメジャー版・データ配置・既存データ・現行Compose・バックアップ状態を記録する。この文書のテンプレートを既存volumeへ直結する前に、`nexus-mobile`の本番DB MRとの差分をレビューする。特にpgBackRest組み込みDBイメージのdigest、PostgreSQL 18の`/var/lib/postgresql`、DB用と`/backup`用の**異なる**外部volume、初期ロール、secret名を照合する。既存VPSが異なる構成なら起動しない。検査結果を`.env.example`の空欄に転記せず、VPS上の私有設定だけに記録する。

DNS、API/Studioドメイン、ACMEメール、イメージdigest、CORS、データベース接続先と権限を確定する。アプリの別MRをマージして本番イメージとhealthcheckを検証する。既存のデータとバックアップを独立した場所に保全し、復元試験を済ませる。起動時にDBをseedまたはmigrationしないことを確認する。

## 私有ファイル

本番採用時は`NEXUS_REPO_DIR`をレビュー済みinfra checkoutの絶対パスへ設定する。systemdへ登録するのは**本repoの**`systemd/nexus-db-backup.service`/`.timer`と`scripts/backup-host.sh`だけとし、`nexus-mobile`側のhost wrapper/timerを並行設置しない。登録前にroot所有のスクリプトがinfra Composeを参照することと、03:00 JSTの既存ジョブとの重複がないことを人間が照合する。このMRでは登録しない。

`/srv/nexus`にレビュー済みcommitを配置し、rootが所有する`/etc/nexus/production.env`に`.env.example`の値を設定する。`NEXUS_SECRET_DIR`はroot所有・0700のディレクトリ。各秘密ファイルは0600。ファイル名: postgres_password、admin_password、public_password、migrator_password、backup_cipher、public_connection、admin_connection、studio_connection、admin_login、signing_key、audit_key、one_time_key、qr_key。Studio接続先は同じ`nexus_admin` DBの`studio` schemaで、Studio専用ロールの作成と権限は別途レビューする。Compose secretsはファイルマウントなので、VPSへのアクセス権も制限する。SSH秘密鍵、DBパスワード、接続文字列、APIキー、TLS秘密鍵をGitLab repo・CIログ・Issueへ貼らない。

GitLab protected variablesはイメージ参照、ドメイン、承認フラグなどのCI専用値を対象とし、秘密値はmasked/file変数で扱う。ただし現行CIは本番デプロイを実行しないため、秘密をrunnerへ渡す必要はない。秘密のVPS投入経路は人間が決定する。

実行前に`python3 scripts/validate-release.py`で全イメージのdigest形式とDB・バックアップvolume名の分離を確認する。これは実VPSのvolume内容を照合する代わりにはならない。

環境ファイルを読み込んだ上で`docker compose config --quiet`、`caddy validate`を実施。例えば`set -a; . /etc/nexus/production.env; set +a`としてから実行する。ファイルはroot管理の信頼できるシェル構文とする。旧`nexus-mobile`本番Composeと本Composeを同時起動しない。人間がDBとバックアップvolume、両APIとStudioのイメージdigest、role・schema・権限、復旧経路を照合してからメンテナンス窓で適用する。Composeは`down -v`を使わない。公開ポートはCaddyの80/443だけとし、旧管理APIは内部ネットワーク専用。DB、API、Studio、Caddy、TLS、iOSのAPI利用、ログインを確認する。

更新時は事前pgBackRestバックアップとMacコピーの成功、別volumeへの復元条件を確認し、適用前の全image digestと環境ファイル版を保存する。API/StudioのCIがlint/test→build→image build→registry pushを担当し、digest成果物を照合する。本repoのMRでdigestを更新し、検証CIとdiffを確認後、protected production environmentの手動承認を経て人間が適用する。失敗時は前のdigestへ戻して`docker compose up -d`。スキーマ変更後のロールバックは別判断とする。DB移行は[移行手順](migration.md)に従い、通常起動やCI deployでは実行しない。
