# 既存repo調査と実装境界（2026-09-24）

- GitLab `11h27m/nexus-mobile`: iOS、public-api、admin-api、既存の開発用Compose、両APIのDockerfileを保有。MR !22でPostgreSQL 18.6 / pgBackRestの本番案、暗号化バックアップ、Macコピー、移行と権限分離の試験が`develop`へ統合済み。現行VPSの17へは未適用。Issue #67、#70、#71、#73がTLS、secrets、backup、deployを追跡している。
- GitLab `11h27m/nexusstudio`: studio-api、studio-web、開発用PostgreSQL Composeを保有。Webは同一オリジンの`/api/*`、APIは`/health`を提供。MR !6でStudio API/Webの本番Dockerfileは`develop`へ統合済み。現行VPSへは未適用。Step 2の作業中のローカル変更は触らない。
- GitLab `11h27m/Nexus`: 空リポジトリで本番構成を含まない。
- `11h27m/nexus-infra`: 本番のCompose、Caddy、運用手順、インフラ検証CIを所有。既存アプリの開発用Composeを移動・編集しない。

## 別MRが必要な項目

1. `nexus-mobile`: 両APIとDB派生イメージのbuild/registry pushはMR !22で実装済み。ただしadmin-apiの`/app/healthcheck`が呼ぶ`/health` routeは未実装。別MR !24で修正中で、CIと実イメージ起動の両方で確認する。本番DB移行はレビュー済みSQLと専用roleで実施し、通常起動では行わない。
2. `nexusstudio`: Studio API/Web本番イメージとsecret file読込はMR !6で実装済み。Studio専用migration/runtime role、権限SQL、隔離DBでのmigration適用は未完成で、別MRが必要。
3. 旧admin-apiは外部公開しない。Caddyには`/admin/*`の経路を置かず、内部ネットワークに限る。利用機能・運用経路は本番適用前に再確認する。
4. 現行VPSのPostgreSQL 17から18への移行は[専用gate](transition-postgres-17-to-18.md)に従う。現行volumeを18のComposeへ直接接続しない。

各イメージはRegistryへ出力されているが、現行17との互換性、管理APIの`/health`、Studio専用DB role、Macコピーからの復元は未検証のため、現在のComposeを**本番起動可能とは判定しない**。インフラCIはアプリイメージのビルド、registry push、本番deployを代行しない。GitLab production environmentの保護、protected/masked/file variables、承認者、runner権限はGitLab管理画面で人間が設定する。
