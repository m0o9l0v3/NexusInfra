# PG17現行DBに触れないAPI公開レイヤー: VPS事実確認（Issue #5）

状態: 設計・確認用。**本番未適用。** 目的は、稼働中のPostgreSQL 17（`/srv/nexus`、`postgres:17-alpine`、volume `nexus_postgres_data`）に触れずに `api.nexus-app.jp` を公開する独立レイヤー `nexus-edge-pg17` の前提事実を、人間がVPSで確認・記録すること。

## 厳守事項

- **読み取りのみ。** DB変更、`docker compose up/down/restart/stop/rm`、`docker exec`での書込み、ポート開放、パケットフィルター変更、timer登録をこの確認で行わない。
- **値はGitに貼らない。** 下の記録表の「値」欄は空のままGitへ置き、実値はVPS私有メモ（保護された運用記録）に残す。Issue・PR・会話にも貼らない。
- `docker inspect`全文、`env`、`docker compose config`全文、接続文字列、パスワード、トークンを出力・保存しない。必ず`--format`等で必要項目だけを取り出す。
- 想定と異なる結果（image、volume、DBメジャーなど）が出たら**そこで止め**、後続作業へ進まない。
- PG18用の`nexus-db-backup.timer`は登録しない（[17→18 gate](transition-postgres-17-to-18.md)参照）。

記録表の列: 項目 / 値(私有メモ参照) / 確認日 / 確認者 / 合否。

## 1. 現行Compose・PG17コンテナ・volume

目的: 公開レイヤーが触れてはならない対象を特定する。

```sh
ls -l /srv/nexus
docker ps --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}}'
docker inspect --type container --format '{{.Id}} {{.Config.Image}} {{.Image}}' <PG17_CONTAINER>
docker inspect --type container --format '{{range .Mounts}}{{.Type}} {{.Name}} {{.Destination}}{{"\n"}}{{end}}' <PG17_CONTAINER>
docker volume ls --format '{{.Name}}'
docker exec --user postgres <PG17_CONTAINER> postgres --version
```

| 項目 | 値 | 確認日 | 確認者 | 合否 |
| --- | --- | --- | --- | --- |
| 現行Composeファイルの場所・名称 | | | | |
| PG17 container ID / image / image ID | | | | |
| DBメジャーバージョン（17.x） | | | | |
| mount先（`/var/lib/postgresql/data`） | | | | |
| volume名（`nexus_postgres_data`） | | | | |

## 2. network・alias・subnet・project名

目的: 公開レイヤーをPG17側のnetworkへどう接続するか（既存networkへ参加するか）を決める材料。

```sh
docker network ls --format '{{.ID}} {{.Name}} {{.Driver}}'
docker network inspect <NETWORK> --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}'
docker inspect --type container --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{$v.Aliases}}{{"\n"}}{{end}}' <PG17_CONTAINER>
docker inspect --type container --format '{{index .Config.Labels "com.docker.compose.project"}}' <PG17_CONTAINER>
```

| 項目 | 値 | 確認日 | 確認者 | 合否 |
| --- | --- | --- | --- | --- |
| `nexus_backend` の実名 | | | | |
| DBコンテナのnetwork alias | | | | |
| 既存networkのsubnet（新規subnetとの非衝突） | | | | |
| Compose project名 | | | | |

## 3. `nexus_admin` のschema・role・権限

目的: public-apiが必要とするschema/権限があるか。**SELECTのみ。** 不足していてもrole作成・GRANTはここで行わず、別レビューにする。

```sh
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d nexus_admin -c '\dn'
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d nexus_admin -c '\du'
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d nexus_admin -c "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2"
```

パスワード付きの`\du+`出力や行データ（個人情報）は記録しない。

| 項目 | 値 | 確認日 | 確認者 | 合否 |
| --- | --- | --- | --- | --- |
| public-apiが必要とするschema | | | | |
| 既存role（名前のみ） | | | | |
| 必要な権限の有無（read-only role必要か） | | | | |

## 4. イメージdigest・registry・実行UID・CPUアーキテクチャ

```sh
uname -m
docker version --format '{{.Server.Os}}/{{.Server.Arch}}'
docker buildx imagetools inspect <IMAGE_REF> | head -n 5   # digest確認のみ
```

| 項目 | 値 | 確認日 | 確認者 | 合否 |
| --- | --- | --- | --- | --- |
| Mobile public-api の immutable digest（`@sha256:`） | | | | |
| Caddy の immutable digest | | | | |
| registry取得経路と認証方式（資格情報は記録しない） | | | | |
| コンテナ実行UID | | | | |
| VPS CPUアーキテクチャ（digestが対応manifestを持つか） | | | | |

## 5. ネットワーク境界・DNS・ACME

```sh
dig +short A api.nexus-app.jp
dig +short AAAA api.nexus-app.jp
ip -6 addr show scope global
ss -ltnH
```

さくらパケットフィルターはコントロールパネルで確認し、**変更しない**。

| 項目 | 値 | 確認日 | 確認者 | 合否 |
| --- | --- | --- | --- | --- |
| パケットフィルター: 22維持、80/443の許可範囲 | | | | |
| IPv6の有無 | | | | |
| DNS A / AAAA（IPv6なしならAAAAが無い/一致） | | | | |
| ACME連絡先メール | | | | |
| 証明書取得失敗時の対応（rate limit、HTTP-01不達、戻し方） | | | | |
| 現在80/443をlistenするプロセス（衝突の有無） | | | | |

## 6. CORS・rate limit・ログ・監視・メモリ

```sh
free -m
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'
df -h /var/lib/docker
```

| 項目 | 値 | 確認日 | 確認者 | 合否 |
| --- | --- | --- | --- | --- |
| 許可するCORS origin | | | | |
| rate limitの方針と値 | | | | |
| ログに個人情報（IP、トークン、本文）が残るか・保持期間 | | | | |
| 監視方法（死活、証明書期限、ディスク） | | | | |
| 4GB VPSのメモリ余裕（追加コンテナ分） | | | | |

## 7. PG17バックアップ・復元検証・既存timer

```sh
systemctl list-timers --all
systemctl list-unit-files 'nexus-*'
ls -l --time-style=long-iso <PG17_BACKUP_DIR>
```

バックアップ作成自体は[17→18 gate](transition-postgres-17-to-18.md)のPhase 1に従い、この確認では行わない。

| 項目 | 値 | 確認日 | 確認者 | 合否 |
| --- | --- | --- | --- | --- |
| PG17バックアップの最新成功時刻 | | | | |
| Macコピーの保存完了・ハッシュ照合 | | | | |
| 別volumeへの復元検証の結果 | | | | |
| 既存timerの有無（`nexus-db-backup.timer`はPG18用、登録しない） | | | | |

## Go/No-Go

- [ ] 1〜7の全項目が私有メモに記録され、想定外の差異がない
- [ ] PG17の最新バックアップとVPS外コピー、別volume復元が確認済み
- [ ] public-api/Caddyのdigest・registry経路・アーキテクチャが整合
- [ ] 80/443・DNS・ACMEの前提が確認済み、22が維持される
- [ ] `nexus_admin`に必要な権限があるか、不足分は別レビューで承認済み

未達の項目がある間は`nexus-edge-pg17`の実装・本番適用へ進まず、現行PG17を維持する。
