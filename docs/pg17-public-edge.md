# PG17現行DBに触れないAPI公開レイヤー（Issue #5・#6・#7）

状態: 実装・確認用。**本番未適用。** 稼働中のPostgreSQL 17（`/srv/nexus`、`postgres:17-alpine`、volume `nexus_postgres_data`）を維持して `api.nexus-app.jp` を公開する独立レイヤー `nexus-edge-pg17`。§1〜7は人間のVPS事実確認、§8以降は実装構成・別承認後の導入・隔離検証手順。実装や静的検査の成功は、VPSの事実確認や適用承認の代わりにならない。

## 厳守事項

- **§1〜7のVPS確認は読み取りのみ。** DB変更、`docker compose up/down/restart/stop/rm`、`docker exec`での書込み、ポート開放、パケットフィルター変更、timer登録をこの確認で行わない。
- **値はGitに貼らない。** 下の記録表の「値」欄は空のままGitへ置き、実値はVPS私有メモ（保護された運用記録）に残す。Issue・PR・会話にも貼らない。
- `docker inspect`全文、`env`、`docker compose config`全文、接続文字列、パスワード、トークンを出力・保存しない。必ず`--format`等で必要項目だけを取り出す。
- 想定と異なる結果（image、volume、DBメジャーなど）が出たら**そこで止め**、後続作業へ進まない。
- PG18用の`nexus-db-backup.timer`は登録しない（[17→18 gate](transition-postgres-17-to-18.md)参照）。

記録表の列: 項目 / 値(私有メモ参照) / 確認日 / 確認者 / 合否。

人間が行う作業の順番・期待値・止める条件・Issueへの記録テンプレートは[pg17-operator-checklist.md](pg17-operator-checklist.md)にまとめた。

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

## 3. アプリDBのschema・role・権限

> 2026-10-01の確認結果: **NO-GO**。アプリDBは`nexus`（`nexus_admin`ではない）、roleは特権の`nexus`のみ、user table 0件。runtime用roleとschemaの構築は[pg17-baseline.md](pg17-baseline.md)で別承認とする。

目的: public-apiが必要とするschema/権限があるか。**この確認で実行するのは参照用のSELECT照会のみ。** 不足していてもrole作成・GRANTはここで行わず、別レビューにする。

DB名は未確定として扱う。**先に全DBを列挙して記録し**、アプリのDBを特定してから以降の`<APP_DB>`に使う（DB名は実在を確認するまで前提にしない（現行の確認結果は上記））。

```sh
# 1) DB一覧（名前・所有者のみ）
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -c "SELECT datname, pg_get_userbyid(datdba) AS owner FROM pg_database WHERE NOT datistemplate ORDER BY 1"
# 2) 特定した<APP_DB>のschema・role・表（行データは見ない）
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c '\dn'
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c '\du'
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2"
```

実効権限は、public-apiが使う（または使う予定の）role `<APP_ROLE>`と必要なschema/表/sequenceを当てて照会する。`has_*_privilege`は参照のみで状態を変えない。

```sh
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin FROM pg_roles WHERE rolname = '<APP_ROLE>'"
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c "SELECT has_database_privilege('<APP_ROLE>', current_database(), 'CONNECT') AS connect, has_schema_privilege('<APP_ROLE>', '<SCHEMA>', 'USAGE') AS schema_usage"
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c "SELECT c.relkind, c.relname, has_table_privilege('<APP_ROLE>', c.oid, 'SELECT') AS sel, has_table_privilege('<APP_ROLE>', c.oid, 'INSERT') AS ins, has_table_privilege('<APP_ROLE>', c.oid, 'UPDATE') AS upd, has_table_privilege('<APP_ROLE>', c.oid, 'DELETE') AS del FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '<SCHEMA>' AND c.relkind IN ('r','v','m','p') ORDER BY 2"
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c "SELECT c.relname, has_sequence_privilege('<APP_ROLE>', c.oid, 'USAGE') AS usage FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '<SCHEMA>' AND c.relkind = 'S' ORDER BY 1"
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c "SELECT pg_get_userbyid(defaclrole) AS grantor, defaclobjtype, defaclacl FROM pg_default_acl"
# visit_logsは列単位のSELECTなので、表単位のselは false が正しい。列ごとに見る
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c "SELECT a.attname, has_column_privilege('<APP_ROLE>', a.attrelid, a.attnum, 'SELECT') AS sel FROM pg_attribute a WHERE a.attrelid = '<SCHEMA>.visit_logs'::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
docker exec --user postgres <PG17_CONTAINER> psql -U <DB_USER> -d <APP_DB> -c "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '<SCHEMA>' AND c.relname IN ('spots','events')"
```

パスワード付きの`\du+`出力や行データ（個人情報）は記録しない。runtime role（`nexus_public`）の適否は、[pg17-baseline.md](pg17-baseline.md) §3の**契約B**との差で判断する: `spots`/`events`はSELECTのみでRLS有効、`visit_logs`はINSERTと列`chain_id, created_at, hash`のSELECTのみ（`session_id`等のSELECT、UPDATE、DELETEは無し）、`__EFMigrationsHistory`はSELECTのみ、他の表は権限なし、特権属性なし。`visit_logs`のINSERTは契約どおりで不合格にしない。差分の修正・付与は別レビュー。

| 項目 | 値 | 確認日 | 確認者 | 合否 |
| --- | --- | --- | --- | --- |
| 全DB一覧とアプリDB名（`<APP_DB>`） | | | | |
| public-apiが必要とするschema | | | | |
| 既存role（名前のみ） | | | | |
| `<APP_ROLE>`の実効権限（CONNECT/USAGE/表・列・sequence、default ACL、RLS）と契約Bとの差 | | | | |

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

> コードから確認済み（2026-10-18、NexusMobile `develop` `6459abd`。VPSの値ではない）:
> - 実行UID: `apps/public-api/Dockerfile`の最終段は`USER $APP_UID`（基底`mcr.microsoft.com/dotnet/aspnet:8.0`、既定1654）。採用digestで`docker image inspect --format '{{.Config.User}}'`を実測し、secretの所有者をその値に合わせる（Issue #7）。
> - healthcheck: `/app/healthcheck`が`curl`で`http://127.0.0.1:8080/health`を叩く。Composeの`healthcheck`はこれを使う。
> - registry経路の調査時点の実装: GitLab CIの`public-api-image`がGitLab Registryへcommit SHAタグでpushし、`image-digests/public-api.txt`に`@sha256:`を記録。DEC-2のGitHub ActionsからのGHCR発行は今回Mobile側に追加（調査時点では未実行・未発行。2026-10-19の状況は下記追記）。

**DEC-2決定（2026-10-18）:** [案B](pg17-edge-decisions.md)を採用し、Mobileの別PRでGitHub Actions → GHCRの経路を整備する。初期visibilityはprivate、CIの発行認証は`GITHUB_TOKEN`、VPSの取得資格はPAT（classic）の`read:packages`のみとする（[GitHub公式資料](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)）。提案名は`ghcr.io/m0o9l0v3/nexus-public-api`で、利用可否・発行権限は要確認。VPSのplatformはPart 0で確認し、レビュー済みcommit SHA・発行ジョブURL・digest・対応platformを照合してから採用する。digest固定は同じイメージの指定であり、再build結果の一致を保証するものではない。資格の値は記録せず、期限・更新・失効を管理する。workflowと発行ガードはMobileにローカル実装済み（未レビュー・未実行）。資格発行・イメージ発行・VPS操作・本番適用は未実施。

**2026-10-19追記:** 旧名`ghcr.io/m0o9l0v3/nexus-public-api`への[初回発行](https://github.com/m0o9l0v3/NexusMobile/actions/runs/39200756827)はpush後のprivate検査で失敗し、人間の画面確認でもPublic表示だった。ユーザー承認によりprivate方針を維持し、新名`ghcr.io/m0o9l0v3/nexus-public-api-pg17-private`へ切り替える。新名の利用可否・発行・private確認・証跡保存・VPSからの取得はまだ未完了。旧Public版の扱いは別途判断し、この変更で公開が解消したとは扱わない。初期決定時の未実施記録と区別する。

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

> コードから確認済み（2026-10-18、NexusMobile `develop` `6459abd`。VPSの値ではない）:
> - CORS: `Cors:AllowedOrigins`のoriginだけ許可（任意header/method、credentialsなし）。iOSアプリはCORS不要のため、許可すべきブラウザoriginを決めて設定する。
> - **DEC-3決定（2026-10-18）:** [案A](pg17-edge-decisions.md)を採用。初期の`Cors__AllowedOrigins__0`は`https://${PG17_EDGE_API_DOMAIN}`とし、別originのLPは許可しない。[NexusMobile #81](https://github.com/m0o9l0v3/NexusMobile/issues/81)には当日イベント一覧のAPI利用方針があるため、LPがAPIを使わないとは扱わない。LPのHTTPS originと接続構成を確認し、別originからの直接接続開始前に案Bを再判断する。LP同時公開が必要なら、この再判断も公開前に終える。現行ポリシーは任意methodを許可するため、その範囲も再確認する。Compose・validator・拒否テストはローカル実装済み。採用digestによるブラウザ検証・本番適用は未実施。
> - rate limit: クライアントIP単位のFixed Window。`POST /api/logs` 60回/60秒、`/api/logs/batch` 12回/60秒。GET系と`/health`は制限なし。
> - **プロキシ越しのクライアントIP:** `X-Forwarded-For`は`RateLimiting:KnownNetworks`/`KnownProxies`のproxyからだけ信頼する（未設定ならloopbackのみ、`ForwardLimit=1`）。Caddy経由では`RateLimiting__KnownNetworks__0`にedge subnetを渡さないと、全クライアントがCaddyのIPで1つの枠を共有する。NexusMobileの`deploy/database/compose.production.yml`はこれを必須にしているが、PG18の`compose.yaml`とIssue #6 T1の構成には無い → #6で`PG17_EDGE_SUBNET`を渡すこと。
> - **DEC-1決定（2026-10-18）:** [案A](pg17-edge-decisions.md#dec-1-caddy越しのクライアントipとrate-limit)を採用。初期edgeの参加はCaddyとpublic-apiのみとし、validatorで設定値とedge subnetの一致・サービス集合・接続networkを検査する。subnet全体を信頼するため、Studio等を追加する前に案Bまたはnetwork分離を含めて再判断する。運用時の実参加コンテナ確認と、採用Caddy版・digestによるクライアント別の制限枠・429・偽装ヘッダーの隔離検証が必要。Compose・validator・拒否テストはローカル実装済み。採用digestによるAPI隔離検証・本番適用は未実施。
> - ログ: Serilogをstdoutへ（`Microsoft`はWarning以上）。request logはmethod・path・status・時間で、クライアントIP・本文は既定で含まない。DB永続化失敗時は例外を`LogError`で出す（DBエラー本文が載り得る）。Caddyはglobal `log`のみでsite単位のaccess logを設定していないため、アクセスIPは記録しない。保持はjson-file `10m×3`。

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
- [ ] `nexus_public`などruntime roleの権限が[pg17-baseline.md](pg17-baseline.md)の契約どおりか、不足分は別レビューで承認済み

未達の項目がある間は本番適用へ進まず、現行PG17を維持する。ダミー値を使う実装・静的検査は本番操作から分離する。

## 8. 実装構成と環境変数

Internet → Caddy（host TCP 80/443のみ）→ public-api:8080 → 既存PG17。Caddyとpublic-apiだけがprivate `edge`（internal）に参加し、Caddyだけが外向き`public`、public-apiだけが既存`backend`へ参加する。Caddyをbackendに接続しない。DBサービス・DB volume・backup・migration・Studioは含めない。

| ファイル | 役割 |
| --- | --- |
| `compose.pg17-edge.yaml` | 単独実行する固定project `nexus-edge-pg17` |
| `Caddyfile.pg17-api` | APIだけを転送。HSTSはmax-ageのみ、trusted_proxiesや転送ヘッダーの独自上書きなし |
| `.env.pg17-edge.example` | 全実値が空欄の専用テンプレート |
| `scripts/validate-pg17-edge.py` | raw YAML・展開JSON・envの安全検査。Docker/DB操作なし |
| `tests/test_validate_pg17_edge.py` | subnet/CORS不一致・PG18合成・危険なサービス等の拒否検証 |
| `compose.pg17-edge-isolation.yml` | ローカル隔離用の使い捨てPG17。edgeとは別project |

| env | 確定する内容 |
| --- | --- |
| `PG17_EDGE_PUBLIC_API_IMAGE` | GHCRのレビュー済みpublic-api digest（DEC-2、発行はMobile別変更） |
| `PG17_EDGE_CADDY_IMAGE` | 対象platformに対応する公式Caddyのindex digest |
| `PG17_EDGE_API_DOMAIN` | APIのDNS名。初期CORSもこのHTTPS origin（DEC-3） |
| `PG17_EDGE_ACME_EMAIL` | 私有メモに保管する証明書連絡先 |
| `PG17_EDGE_BACKEND_NETWORK` | 読み取り確認で特定した既存PG17のnetwork実名 |
| `PG17_EDGE_SUBNET` | 既存network/経路と重複しないprivate IPv4 /24〜/28。KnownNetworksにも同じ値（DEC-1） |
| `PG17_EDGE_SECRET_DIR` | 秘密ファイルを保管する絶対パス |

validatorは初期構成の閉じた契約を検査する。追加のサービス・env override・command/entrypoint・network override・任意のCaddyディレクティブを許可しない。raw YAMLでは必須env参照、展開JSONではdigest形式・subnet・origin・マウントを検査する。Caddyfileのマウント元はComposeファイルのディレクトリ基準で解決し、`--caddyfile`（既定はrepo直下）の検査対象と同一の実ファイルであることを確認する。同名の別ファイルは許可しない。Caddy整形ではトークン間の空白・コメントだけを無視し、引用符内のヘッダー値や空白は保持して比較する。機能変更にはvalidatorも含めたレビューが必要。静的検査は実networkへの手動参加や秘密の内容・DB権限・イメージの正当性を検証しない。

## 9. 秘密ファイルと導入前提

配置例は`/srv/nexus-edge-pg17`（既存`/srv/nexus`とは別）、私有envは`/etc/nexus/pg17-edge.env`。PG18の`.env.example`や`/etc/nexus/production.env`を流用しない。

`public_connection`は既存network上の確認済みDB alias・確認済みDB名・`nexus_public`の接続文字列。envには接続文字列を直接渡さない。`PublicApiReadOnlyFile`というキー名でもruntime契約は[契約B](pg17-baseline.md#3-runtime-roleの実契約-bvisit_logsへのinsertのみ許可)で、ログINSERTを必要とする。特権roleや管理資格へフォールバックしない。schema/roleが未準備なら起動を強行せず、別承認のDB準備へ戻る。

`public_connection`と`audit_key`は、採用public-apiの実測UID所有0400。ディレクトリは私有0700とし、ホストの所有権・コンテナ内の読み取りを両方確認する。Compose secretのuid/mode指定で解決したことにしない。詳しくは[秘密の所有権](deployment.md)を参照。PATはroot用Docker認証設定で保護し、API secretに混ぜない。

## 10. 手動導入（本番の事前確認・レビュー・個別承認後のみ）

以下は手順であり、今回の実装作業ではVPS上で実行しない。§1〜7のGo/No-Go、PG17のバックアップ・VPS外コピー・別volume復元、schema/role契約B、GHCR発行証跡、VPS platform、80/443の衝突なし・DNS・ACME・SSH 22維持を確認する。未確認値を推測して埋めない。

1. 専用ディレクトリへレビュー済みファイルを配置し、私有envと秘密を準備する。API/Caddyの両参照は`@sha256:`。直前のPG17 container ID/image/mount/volumeと、新projectの前digest・env版を私有メモへ保存する。
2. rootの信頼できるシェルで私有envを読み、安全検査を実行する。展開JSONには機微な運用情報があるため一時私有ファイルにだけ保存し、全文をCI・Issue・チャットへ貼らない。

```sh
set -a
. /etc/nexus/pg17-edge.env
set +a
umask 077
python3 scripts/validate-pg17-edge.py --env
resolved=$(mktemp)
docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml config --format json > "$resolved"
python3 scripts/validate-pg17-edge.py --resolved "$resolved" --env
# 人間が私有環境で差分を確認する。共有出力しない。
rm "$resolved"
```

3. 人間がGHCR読み取り認証・platform・image digestを確認してから、新projectだけを取得・起動する。

```sh
docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml pull
docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml up -d --wait
```

4. 外部HTTPSの`/health`と代表GET、HTTP→HTTPS、セキュリティヘッダー、ログの機微情報、TCP 80/443以外のhost非公開を確認する。edgeの実参加コンテナが2つだけ、Caddyがbackend不参加であることを私有メモで確認する。PG17 container ID/image/mount/volumeが直前と一致することを確認する。iOSでのAPI疎通とログ送信、ディスク・メモリ・証明書期限の監視を確認する。LPの別origin利用はまだ許可しない。

途中失敗なら後続へ進まず、新projectのrollbackを判断する。PG17に対する停止・再作成・volume操作で解決しない。

## 11. Rollback

新規公開を中止する場合はedge projectだけを停止する。ACME volumeは保持する。

```sh
docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml stop
```

更新の失敗では私有envのイメージ参照をレビュー済みの前digestに戻し、§10の検査後に同じprojectで`docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml up -d --wait`を行う。DB migrationはこのprojectにない。別承認でDBを変えた場合の復元は[baselineのrollback境界](pg17-baseline.md)で判断する。

## 12. 隔離検証（Issue #7、実digest入手後）

最新の実施結果は[Issue #7 隔離検証記録](pg17-issue-7-verification.md)を参照。2026-10-20時点ではDB権限・採用イメージのGETまで成功し、ログPOSTの500をNexusMobile PR #99で修正中。

Mac等の専用ローカルDocker engineだけで行う。VPSや本番と同じengine、実dump・本番secretは使わない。先にcontextの接続先がlocal Unix socketであること、live container/volumeがないこと、host 80/443とproject `nexus-edge-pg17`が未使用であることを確認する。同projectがあれば停止せず別engineを用意する。以下はハーネスの手順で、実APIの検証は採用GHCR digest入手後に実施・記録する。

1. `NEXUS_MOBILE_DIR`とレビュー済みcommitから、[baseline §5](pg17-baseline.md#5-隔離baselineの構築と検証)と同じ方法で移行SQLを私有ファイルへ生成し、`pg17-baseline.py manifest`で移行ID2本・SQL hash・起動時migrationなしを確認する。旧Issue #6 T6のSELECT専用`public_readonly`や独自の簡略schemaは使わない。
2. 以下の隔離専用envを私有設定で指定する。project・network・volumeはすべて`nexus-pg17-edge-iso-`に一意のsuffixを付ける。`PG17_EDGE_ISO_DB_IMAGE`は確認済みPG17 digest、`PG17_EDGE_ISO_PASSWORD_FILE`は私有絶対パスのダミーパスワード。既存volumeがあれば再利用せず中止する。

```sh
python3 scripts/validate-pg17-edge.py --isolation --env
docker compose -p "$PG17_EDGE_ISO_PROJECT" -f compose.pg17-edge-isolation.yml config --quiet
docker volume create "$PG17_EDGE_ISO_VOLUME"
docker compose -p "$PG17_EDGE_ISO_PROJECT" -f compose.pg17-edge-isolation.yml up -d --wait
```

3. 起動したDBがPG17・DB名nexus・空schemaであることを確認する。新規のダミー`NEXUS_PUBLIC_PASSWORD`を私有シェル環境に用意し、shell tracingを無効にして次を実行する。SQLは既存のレビュー対象を再利用し、秘密は引数に直接渡さない。

```sh
docker compose -p "$PG17_EDGE_ISO_PROJECT" -f compose.pg17-edge-isolation.yml exec -T -e NEXUS_PUBLIC_PASSWORD postgres psql -X -v ON_ERROR_STOP=1 -U nexus -d nexus < sql/pg17-baseline/01-roles.sql
docker compose -p "$PG17_EDGE_ISO_PROJECT" -f compose.pg17-edge-isolation.yml exec -T postgres psql -X -v ON_ERROR_STOP=1 -U nexus -d nexus < "$PG17_BASELINE_MIGRATE_SQL"
docker compose -p "$PG17_EDGE_ISO_PROJECT" -f compose.pg17-edge-isolation.yml exec -T postgres psql -X -v ON_ERROR_STOP=1 -U nexus -d nexus < "$NEXUS_MOBILE_DIR/deploy/database/grant-runtime.sql"
```

ダミーの公開・非公開行と管理表の行をレビューしたfixtureとして入れ、[baseline §4](pg17-baseline.md#4-issue-7-の受け入れ条件正式契約b)の権限チェックを行う。投入fixtureと期待結果も#7の証跡に含める。

4. edge用私有設定を隔離環境専用に用意する。`PG17_EDGE_BACKEND_NETWORK`は上の`PG17_EDGE_ISO_NETWORK`、接続文字列は`Host=pg17-isolation-db;Database=nexus;Username=nexus_public`とダミーパスワード、APIドメインは`api.localhost`。audit keyもダミーにする。採用APIの実UIDをinspectし、そのUID所有0400の秘密ファイルを作る。Caddyはlocalhost向け内部CAを使うため、root証明書をMac全体へ信頼登録する必要はない。
5. §10と同じraw・resolved・env検査後、隔離engineでのみedgeを起動する。`curl -k --resolve api.localhost:443:127.0.0.1 https://api.localhost/health`と代表GETを確認する（`-k`はこの隔離確認専用）。ログPOST・hash連鎖・再接続を含めてbaseline §4の全項目を記録する。
6. クライアント別rate limitは、Caddyから見て異なる接続元IPになる2つのテストクライアントで確認する。クライアントをedgeには参加させず、隔離engineのpublic側へ接続する。一方の429が他方へ波及しないこと、偽装X-Forwarded-For/ProtoでIP・schemeや制限枠を変更できないことを確認する。共有NAT配下の同じIPは別クライアントとみなさない。CORSは未許可originにAllow-Originが付かないことをブラウザでも確認する。CORSをAPI認証やログ送信全般の防止策とは扱わない。
7. 終了時はedgeを先に停止・削除し、backendへの参加がなくなってから隔離DB projectを片付ける。`down -v`は使わない。volumeは残し、不要と確認した今回の隔離volumeだけを個別に削除する。本番名・別作業のvolumeは対象外。

```sh
docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml down
docker compose -p "$PG17_EDGE_ISO_PROJECT" -f compose.pg17-edge-isolation.yml down
# PG17_EDGE_ISO_VOLUMEを人間が再確認してから、その1個だけ削除する。
docker volume rm "$PG17_EDGE_ISO_VOLUME"
```

## 13. 禁止操作とStudio保留条件

PG18 `compose.yaml`との合成起動、`nexus_postgres_data`のマウント、既存PG17の停止・再作成・接続先変更、`down -v`・volume prune、backup/migratorやtimerの混入、タグによる本番起動は禁止。PG18側のrate limit問題は別Issueとし、今回の変更禁止ファイルを直さない。

Studioは[Issue #8](https://github.com/m0o9l0v3/NexusInfra/issues/8)のPG17 DB/auth準備と[Issue #9](https://github.com/m0o9l0v3/NexusInfra/issues/9)の結合検証まで非公開。同じCaddyへの追加前にDEC-1の信頼範囲を再判断する。LPの別origin接続前にはDEC-3を再判断する。HSTS `includeSubDomains`は関連サブドメインのHTTPS運用確認後に別途判断する。
