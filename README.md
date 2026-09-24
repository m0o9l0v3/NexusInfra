# nexus-infra

単一VPS上のNexus本番構成案。**Draft MR !1のコードは本番VPSへ未適用**で、マージや配置だけで運用を始めてはいけません。

2026-09-24に人間が確認した稼働中のDBはPostgreSQL 17.11（`postgres:17-alpine`、volume `nexus_postgres_data`、マウント先`/var/lib/postgresql/data`）。本repoの`compose.yaml`はPostgreSQL 18とpgBackRest派生イメージ、新しいDB/backup volume向けです。**既存17 volumeを18のComposeへ接続しないでください。** 現行VPSのバックアップと隔離復元を先に行い、18移行を別volumeで検証する設計です。

- [17→18の移行gate](docs/transition-postgres-17-to-18.md)：現行17の保全、Macコピー、隔離復元、18でのリハーサル、切替とrollback境界
- [構成](docs/architecture.md)・[導入](docs/deployment.md)・[DB migration](docs/migration.md)
- [18移行後のバックアップと復元](docs/backup-restore.md)・[災害復旧](docs/disaster-recovery.md)・[本番適用前チェック](docs/security-checklist.md)

本repoは本番Compose、外側のCaddy、運用手順を所有します。API/Studio/DB派生イメージのDockerfileとmigrationは各アプリrepoが所有します。CIは構文・ネットワーク・安全ガードを検証しますが、実VPSでの復元成功を証明しません。本番適用は差分と復元記録を人間が確認・承認した後です。
