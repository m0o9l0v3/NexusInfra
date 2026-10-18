# PG17公開レイヤー: 人間が行う確認作業（Issue #5・#7）

状態: 作業手順。**本番未適用。** Claude・CIでは実施できない、人間の判断とVPS上の読み取り確認だけをまとめる。コマンドの背景と記録表は[pg17-public-edge.md](pg17-public-edge.md)、role契約は[pg17-baseline.md](pg17-baseline.md)を参照。

## 進め方と記録ルール

- 上から順に行う。各項目の「止める条件」に当たったら**そこで止め**、後続へ進まず相談する。
- 実値（IP、subnet、container ID、digest以外の識別子、メール、パス、接続元）は**VPS私有メモ**にだけ書く。Git・Issue・PR・会話には貼らない。
- Issueへは「確認日・確認者・項目ごとの合否・差異の有無」だけを書く（末尾のテンプレート）。
- public image digest（`@sha256:`）とMobile commit SHAは秘密ではないので、#7の記録に使ってよい。
- VPSでは読み取りのみ。`docker compose up/down/restart/stop/rm`、DBへの書込み、パケットフィルター変更、timer登録、`docker pull`はしない。

D1（digest経路）・D4（CORS）と、#6のrate limit設定は、[pg17-edge-decisions.md](pg17-edge-decisions.md)（DEC-1〜3）で別に決める。

所要の目安: Part 1は判断待ちを除き30分、Part 2は1時間程度。

## Part 1: VPSの外で決めること

| # | 決めること | 具体的な作業 | 記録 | 止める条件 |
| --- | --- | --- | --- | --- |
| D1 | public-apiのdigest発行経路 | NexusMobileのGitLab CIで、採用するcommit（現時点の候補は`develop` `6459abd`）の`public-api-image`ジョブが成功しているか確認し、成果物`image-digests/public-api.txt`の`registry/path@sha256:...`を取得する。GitLab CIが止まっている場合は (a) GitLab CIを再稼働する か (b) Mobile側にGitHub Actionsからのregistry pushを追加する（Mobileの別PR）かを決める | 採用commit SHA、digest、ジョブURL、選んだ経路 | digestが`@sha256:`でない、commitがAPI/DB変更を含むのに未レビュー |
| D2 | VPSからのpull認証 | D1のregistryに対する**読み取り専用**の資格（GitLabならscope `read_registry`のdeploy token）を発行するか決める。保存先はVPSのroot用docker設定に限る | 発行の有無・失効日（値は書かない） | 書込み権限付きの資格しか用意できない |
| D3 | Caddyのdigest | Macで`docker buildx imagetools inspect caddy:<採用する2.x版>`を実行し、index digestと、VPSのCPUアーキテクチャ（V4）のmanifestがあることを確認する | 版、digest | VPSのアーキテクチャのmanifestが無い |
| D4 | CORS origin | ブラウザから`api.nexus-app.jp`を呼ぶWebページがあるか決める。iOSアプリだけならCORSは使われない。ある場合はそのHTTPS origin（例 `https://<web>.nexus-app.jp`）を1つずつ列挙する | 許可origin一覧（または「ブラウザ利用なし」） | 「`*`」や`http://`のoriginが必要と言われる |
| D5 | ACMEと証明書失敗時の対応 | ACME連絡先メールを決める。HTTP-01が失敗したら`nexus-edge-pg17`だけを`-p`明示で`stop`し、DNS・80番の到達性を直してから再開する、と合意する（Let's Encryptは失敗が続くと一時的に発行を制限するため、何度も再試行しない） | メール（私有メモ）、合意日 | 80番を外部から開けられない |
| D6 | rate limitと監視 | 既定値（`POST /api/logs` 60回/60秒、`/batch` 12回/60秒、GETは制限なし）で良いか決める。死活（`/health`）、証明書期限、ディスクを何で監視するか決める | 採否、監視方法 | — |

## Part 2: VPSでの読み取り確認（Issue #5）

VPSにSSHし、`sudo`が必要な行以外は通常ユーザーで実行する。`<PG17_CONTAINER>`はV1で確認した名前に置き換える。

### V1. PG17コンテナ・volume（§1）

```sh
ls -l /srv/nexus
docker ps --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}}'
docker inspect --type container --format '{{.Config.Image}} {{.Image}} {{.State.Health.Status}}' <PG17_CONTAINER>
docker inspect --type container --format '{{range .Mounts}}{{.Type}} {{.Name}} {{.Destination}}{{"\n"}}{{end}}' <PG17_CONTAINER>
docker exec --user postgres <PG17_CONTAINER> postgres --version
docker image inspect --format '{{json .RepoDigests}}' postgres:17-alpine
```

- 期待値: image `postgres:17-alpine`、health `healthy`、volume `nexus_postgres_data`が`/var/lib/postgresql/data`、`PostgreSQL 17.x`。
- 最後の行の`postgres@sha256:...`は、#7の隔離検証で使うPG17 digest（`PG17_BASELINE_IMAGE`）として記録してよい。
- 止める条件: image・volume・mount先・メジャーのどれかが違う、healthyでない。

### V2. network・alias・subnet・project名（§2）

```sh
docker inspect --type container --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{$v.Aliases}}{{"\n"}}{{end}}' <PG17_CONTAINER>
docker inspect --type container --format '{{index .Config.Labels "com.docker.compose.project"}}' <PG17_CONTAINER>
for n in $(docker network ls -q); do docker network inspect "$n" --format '{{.Name}} internal={{.Internal}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'; done
ip -4 route
```

- 記録: DBのnetwork名（`PG17_EDGE_BACKEND_NETWORK`に使う）、DBのalias（`public_connection`の`Host=`に使う）、project名、全subnet。
- `PG17_EDGE_SUBNET`の候補を1つ決める: private IPv4の`/28`で、上の全subnet・`ip -4 route`のどれとも重ならないもの。
- 止める条件: DBがデフォルトの`bridge`にしか参加していない（aliasで名前解決できないため設計を見直す）、network名が`nexus-production_`で始まる（PG18 project名と衝突）。

### V3. DB接続元（§3の追加確認）

§3は2026-10-01に確認済み（NO-GO、baselineは別承認）。baseline適用の承認材料（[pg17-baseline.md](pg17-baseline.md) §7）として、現在`nexus`で誰が接続しているかを見る。

```sh
docker exec --user postgres <PG17_CONTAINER> psql -U nexus -d postgres -c "SELECT usename, datname, application_name, client_addr, state, count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1,2,3,4,5 ORDER BY 1,2"
docker exec --user postgres <PG17_CONTAINER> psql -U nexus -d nexus -c "SELECT count(*) AS user_tables FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"
```

- 期待値: 自分の`psql`以外の接続が無い、または用途を説明できる。user table 0件（前回と同じ）。
- 止める条件: 不明な接続元がある、user tableが増えている（baselineの前提が崩れる）。

### V4. CPUアーキテクチャ・registry到達（§4）

```sh
uname -m
docker version --format '{{.Server.Os}}/{{.Server.Arch}}'
sudo sh -c 'test -f /root/.docker/config.json && grep -o "\"[^\"]*\": *{" /root/.docker/config.json | cut -d\" -f2'   # 登録済みregistryのホスト名だけ
sudo docker manifest inspect <D1のdigest> >/dev/null && echo pull-auth-ok           # D1・D2確定後。取得はしない
```

- 期待値: `x86_64`（`linux/amd64`）または`aarch64`（`linux/arm64`）。D1・D3のdigestがそのアーキテクチャを含む。
- 止める条件: アーキテクチャのmanifestが無い、registryに到達・認証できない。

### V5. 公開境界・DNS（§5）

```sh
sudo ss -ltnpH '( sport = :80 or sport = :443 )'
ip -6 addr show scope global
dig +short A api.nexus-app.jp
dig +short AAAA api.nexus-app.jp
```

さくらのコントロールパネルでパケットフィルターを**見るだけ**（変更しない）。

- 期待値: 80/443をlistenしているプロセスが無い。パケットフィルターで22が許可され、80/443の許可状況を把握できる。AのIPがこのVPS。IPv6が無ければAAAAも無い（あれば同じVPSを指す）。
- 止める条件: 80/443を既に何かが使っている、AまたはAAAAが別ホストを指す、22の許可が不明。

### V6. メモリ・ディスク（§6）

```sh
free -m
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'
df -h / /var/lib/docker
```

- 記録: `available`、各コンテナの使用量、空き容量。
- 目安（提案値）: public-apiとCaddyの追加後も`available`が512MB以上残ること、`/var/lib/docker`の空きが5GB以上。下回る場合は止めて相談。

### V7. バックアップ・timer（§7）

```sh
systemctl list-timers --all --no-pager
systemctl list-unit-files 'nexus-*' --no-pager
sudo ls -l --time-style=long-iso <PG17_BACKUP_DIR>   # 既存のPG17バックアップ置き場があれば
```

- 期待値: `nexus-db-backup.timer`（PG18用）が**登録されていない**。PG17の最新バックアップ時刻を把握できる。
- 現在user table 0件でも、baseline適用前（[pg17-baseline.md](pg17-baseline.md) §6-4）には[17→18 gate](transition-postgres-17-to-18.md) Phase 1のバックアップ・Macコピー・別volume復元が必要。これは別承認の作業（`ALLOW_PG17_EXPORT=1`）で、この確認では実行しない。
- 止める条件: PG18用timerが登録済み、バックアップの所在が分からない。

## Part 3: #7の隔離検証に渡すもの

Part 1・2がそろったら、Mac上の隔離検証（Claudeが実行可能）に次を渡す。

- public-api digest（D1）と採用Mobile commit SHA
- Caddy digest（D3）、PG17 digest（V1）
- 秘密ファイルの所有UIDは実イメージから実測するので不要（想定は1654）

本番VPSの値（network名、subnet、alias）は隔離検証では使わない。

## Part 4: 承認が別に必要な操作（この確認では行わない）

1. PG17のバックアップ取得とVPS外コピー・別volume復元（Phase 1）
2. baselineのSQL適用（[pg17-baseline.md](pg17-baseline.md) §6の1〜3）
3. `nexus-edge-pg17`の本番導入（80/443開放を含む。#6の手順書）

## Issueへの記録テンプレート（値は書かない）

```md
#5 VPS確認 YYYY-MM-DD 確認者: <名前>
- D1 digest経路: 合/否（経路: GitLab CI / GitHub Actions）
- D2 pull認証: 合/否
- D3 Caddy digest: 合/否
- D4 CORS: 決定済み/未決
- D5 ACME・失敗時対応: 合意済み/未
- D6 rate limit・監視: 決定済み/未決
- V1〜V7: 各 合/否、差異: なし/あり（詳細は私有メモ）
- Go/No-Go: 
```
