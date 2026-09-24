# PostgreSQLの論理バックアップと復元試験

既存の`nexus-mobile/codex/production-db-foundation`は暗号化pgBackRest、WAL、Macコピー、PITRを設計し、ローカルで復旧を検証済み。本repoのpg_dumpは要件に応じた追加の論理バックアップで、VPS全損時の独立性を単独では持たない。導入前に二重の容量・実行時間を測り、主要な復旧手段をどちらにするか承認する。既存の`nexus-backup`と同時刻に実行しない。`systemd/nexus-pgdump.service`と`.timer`は05:30 JSTの案で、既存03:00の物理バックアップと重ねない。systemdへ登録するのは人間が本番構成を承認した後とする。

`scripts/backup-postgres.sh`はroot所有0700の`BACKUP_DIR`へUTC時刻付きcustom-format dumpを生成し、終了後に一覧検査とSHA-256を記録する。失敗時の`.partial`は消去し、成功世代を残す。スクリプトにはパスワードがなく、DBコンテナの`/run/secrets/postgres_password`を使う。root所有のsystemd timerで毎日一回を想定し、成功ログとdumpの最終時刻を26時間以内に確認する。少なくとも7世代を保持し、古い世代はVPS外コピー・復元確認後に人間が整理する。ディスク使用率80%を超えたら世代数とDB増分を再計算する。

VPS外候補:

| 方法 | 費用 | 復旧可能範囲・注意 |
| --- | --- | --- |
| 既存Macへ暗号化コピー | 新規契約なし | Macが稼働し転送した最終時点まで。既存pgBackRest案と整合する。Mac自体の故障にも注意 |
| 手持ちの別媒体へ追加コピー | 新規契約なし | 媒体の紛失・暗号鍵の別保管が課題 |
| オブジェクトストレージ | 継続費用あり | 自動化しやすいが費用・鍵管理・復元試験が必要 |

現時点では追加契約を避け、既存Macへの暗号化コピーを推奨する。ただし保存先を本repoが自動設定しない。**平文dumpをMacへ転送しない。** pg_dumpを外へ持ち出すなら別途暗号化手順をレビューする。pgBackRestのMacコピーを主要な全損復旧手段とするのが既存方針に沿う。

復元試験: SHA-256検査後、`ALLOW_RESTORE_TEST=1 scripts/restore-postgres.sh /absolute/path/file.dump nexus_restore_YYYYMMDD`を実行する。スクリプトは既存DB名への上書きを拒否し、新しいDBだけを作る。件数、MapDatasetの版・checksum、権限、API動作を照合し、実施日時・所要時間を記録する。試験DBの削除は自動化しない。全損復旧時には新規VPS・新規volumeに復元し、productionへ切り替える前にデータを照合する。pg_dumpはWALの指定時点復旧を提供しない。
