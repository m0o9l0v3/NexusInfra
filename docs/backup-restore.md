# バックアップと隔離復元

## 主系: pgBackRest

日次ジョブは本repoの`systemd/nexus-db-backup.timer`と`scripts/backup-host.sh`からinfra Compose内の`nexus-backup`を呼ぶ。`nexus-mobile`側のhost wrapper/timerは同時に登録しない。導入前に既存ジョブの有無を照合し、このMRでは登録しない。

`nexus-mobile` の本番DBイメージに組み込まれた pgBackRest を主系とする。DB用外部volumeと暗号化リポジトリ用外部volumeを分けるが、どちらも同じVPS上にある。日次03:00 JSTのフル（日曜）・差分（その他）、継続WAL、フル5世代の保持は `nexus-db-backup.timer` と `nexus-backup` の契約に従う。Macへの取り出しは利用日に1日1回以上、重要な更新の前後に行い、直近3回の正常なコピーを保持する。取り出し時は `nexus-backup` のロックを使い、rawの `pgbackrest backup` / `expire` を並行実行しない。暗号鍵はMacコピーとは別保管する。

VPS内でリポジトリとWALが残れば、保持範囲の指定時点復旧が可能。VPS全損では最後にMacへ正常コピーした時点までが復旧範囲となる。月1回と公開前に、Macコピーと別保管の鍵だけを使い、元DBと同じメジャー版・CPUアーキテクチャの**別の空volume**へ復元する。詳細は `nexus-mobile/docs/database-backup-to-mac.md` に従う。本作業では実VPSの転送・復元を行わない。

## 補助: 手動pg_dump

`scripts/backup-postgres.sh` は、スキーマ・権限・データの論理確認や移行前の追加保全が必要な場合だけ使用する。定期timerは設けない。実行前にDBとバックアップのサイズ、空き容量、所要時間を見積もり、pgBackRestやMac取り出しと重ねない。root所有0700の `BACKUP_DIR` を用意し、`ALLOW_LOGICAL_EXPORT=1` を明示して実行する。custom-format dump、一覧検査、SHA-256を保存する。平文dumpをVPS外へ転送しない。論理dump単体にはWAL指定時点復旧も、ロール・権限の完全な再現もない。

論理復元が必要なら、オペレーターがDBイメージ・メジャー版を照合した**隔離Composeプロジェクトと新しい空volume**を用意する。SHA-256と`pg_restore -l`を確認し、レビュー済みのロール・schema・権限SQLを先に適用してから、新しいDBへ`pg_restore --exit-on-error`を実行する。本番DBコンテナ内に試験DBを作らない。移行履歴、代表レコード、`MapDataset`のversion/checksum、Studio schema、API動作、権限を照合し、所要時間を記録する。元volumeや試験volumeを自動削除しない。

## 容量と確認記録

導入前に実DBサイズ、pgBackRestリポジトリとWAL増分、Macコピーの実サイズ、手動dumpの実サイズを測る。DB・バックアップ・一時dump・転送中のWALが同じVPSのディスクを使うため、同時ピークを足して80%未満の余裕を確保する。90%で更新・追加dumpを止めて対処する。毎日、日次バックアップの最終成功が26時間以内、WALの未転送が10分以内、Macの最終コピー日時、空き容量を確認する。月次の隔離復元でRTOを測り、VPS全損のRPOは最終Macコピー時刻として記録する。通知経路が未設定の間は人が確認する。
