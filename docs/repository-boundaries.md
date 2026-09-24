# 既存repo調査と実装境界（2026-09-24）

- GitLab `11h27m/nexus-mobile`: iOS、public-api、admin-api、既存の開発用Compose、両APIのDockerfileを保有。`codex/production-db-foundation`にはPostgreSQL 18.6 / pgBackRestの本番案、暗号化バックアップ、Macコピー、移行と権限分離の試験がある。未統合なのでこのrepoで上書きしない。Issue #67、#70、#71、#73がTLS、secrets、backup、deployを追跡している。
- GitLab `11h27m/nexusstudio`: studio-api、studio-web、開発用PostgreSQL Composeを保有。Webは同一オリジンの`/api/*`、APIは`/health`を提供。Studio Web本番Dockerfileは未実装。Step 2の作業中のローカル変更は触らない。
- GitLab `11h27m/Nexus`: 空リポジトリで本番構成を含まない。
- `11h27m/nexus-infra`: 本番のCompose、Caddy、運用手順、インフラ検証CIを所有。既存アプリの開発用Composeを移動・編集しない。

## 別MRが必要な項目

1. `nexus-mobile`: public/admin APIの本番イメージに`/app/healthcheck`を追加し、`/health`をlocalhostで照合する。ビルドとregistry pushのCI、SHAタグとdigest記録を追加する。既存の本番DBブランチの構成と公開APIの権限分離を先に統合・照合する。マイグレーションは専用のレビュー済みSQLを人間が承認して実行し、通常起動やインフラCIでは行わない。
2. `nexusstudio`: studio-apiの本番Dockerfileとhealthcheck、`ConnectionStrings__StudioDatabaseFile`の秘密ファイル読み込み、studio-webの静的配信用本番Dockerfileとhealthcheckを追加。マイグレーション・管理者作成は手動の別手順。静的アセットはWebイメージ内に固定し、同一オリジンの`/api/*`をCaddyがstudio-apiへ中継する。Vite開発サーバーを本番へ出さない。
3. Caddyの`/admin/*`が既存admin-apiのルートと一致するか、公開範囲が妥当かを実アプリで確認。不要なら公開前に閉じる。

これらのイメージはまだ存在しないため、現在のComposeは**本番起動可能とは判定しない**。インフラCIはアプリイメージのビルド、registry push、本番deployを代行しない。GitLab production environmentの保護、protected/masked/file variables、承認者、runner権限はGitLab管理画面で人間が設定する。
