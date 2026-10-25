# PG17 baseline とpublic-api runtime role（隔離検証）

状態: 設計・隔離検証用。**本番未適用・VPS操作なし。** 現行PG17、`/srv/nexus`、`nexus_postgres_data`には何も実行していない。調査基準: NexusMobile `develop` `87446aa`。以降のSQL・手順は人間のレビューと承認後にだけ本番へ使う。

## 1. 現行PG17の事実と判定（Issue #5 Section 3）

人間確認済みの要点（実IP・秘密は記録しない）: PostgreSQL 17、`POSTGRES_USER=nexus`/`POSTGRES_DB=nexus`、DBは`nexus`と`postgres`、`nexus` DBのschemaは`public`のみでuser table 0件、roleは`nexus`（Superuser/Create role/Create DB/Replication/Bypass RLS）のみ。

**Section 3は NO-GO。** `nexus`は`DatabaseReadiness`が拒否する特権roleで、runtimeに使えない。public-apiが必要とするschema（`spots`/`events`/`visit_logs`と移行履歴）も未作成。`nexus_admin`というDBは存在しない（以前の想定名は誤り）。baselineの構築は下記の別承認事項。

## 2. NexusMobileの移行経路（コード確認結果）

| 項目 | 結果 |
| --- | --- |
| EFが発見する移行 | `[Migration]`+`[DbContext]`属性を持つ**2本のみ**: `20260905120000_AddMapDatasets`、`20260919120000_AddInitialPostgreSqlSchema` |
| 発見されない移行 | `InitialCreate`〜`AddVisitLogMetadata`、`AddPublicApiReadOnlyRole`は属性がなくEFの対象外。`AddInitialPostgreSqlSchema`のコメントどおり遡って有効化しない |
| 空PG17からの経路 | 空DB → `tools/database script`で生成した冪等SQL（DB接続なし、`SET ROLE nexus_owner`・advisory lock・既存schema拒否・未知履歴拒否を含む）→ `deploy/database/grant-runtime.sql` |
| `__EFMigrationsHistory` | 上記2本のIDだけが入る。`AddInitialPostgreSqlSchema`は`map_datasets`以外に表があると例外で停止し、履歴を偽装しない |
| `AddInitialPostgreSqlSchema`の中身 | `InitialPostgreSqlSchema.sql`（埋め込みリソース）で`map_datasets`以外の12表と索引を作る。`Down`は`NotSupportedException`（restore境界） |
| 起動時検査 | public-api/admin-apiは本番で`DatabaseReadiness.CheckAsync`のみ（読み取り）。`Migrate`/`EnsureCreated`/seedはpublic-apiに無い（`pg17-baseline.py manifest`が静的に確認） |
| readinessとの一致 | 履歴が2本と完全一致、runtime roleが`rolsuper/createdb/createrole/bypassrls`でなく`nexus_owner`のメンバーでなく`public`にCREATE権限が無い、`spots/events/visit_logs`が存在 |

生成SQLは実際に出力し、移行ID2本以外・`CREATE ROLE`・`GRANT`・`DROP`・`TRUNCATE`を含まないことを確認した。**`nexus_public_readonly`は現行の移行経路に含まれない。**

## 3. runtime roleの実契約: B（`visit_logs`へのINSERTのみ許可）

正本は`nexus_public_readonly`ではなく`nexus_public`（`DatabaseRuntimeConfiguration`が本番でこのuserを強制）。`nexus_public_readonly`の全表SELECT方式は、`docs/deploy-public-api-readonly-role.md`が新規本番に使わないと明記している旧方式。

`deploy/database/grant-runtime.sql`の`nexus_public`権限:

- `spots`/`events`: SELECTのみ。RLSで`is_published = true`の行だけ
- `visit_logs`: INSERTと、列`chain_id, created_at, hash`のSELECTのみ（`session_id`等の読み取り不可、UPDATE/DELETE不可）
- `__EFMigrationsHistory`: SELECT
- 他の管理用表・将来追加表: 権限なし（default privilegesなし）、DDL・所有者へのSET ROLE不可

**判断: B。** 根拠: `LogPersistenceService`が前回hashを`visit_logs`から読み、`visit_logs`へINSERTする。`LogsController`の`POST /api/logs`・`/api/logs/batch`（匿名参加者ログ、レート制限付き、202を返し非同期で永続化）から呼ばれる。MobileのCIと`tools/database verify`も「ログINSERT成功・hash連鎖・`session_id`読み取り拒否・UPDATE/DELETE拒否」を要求している。A（INSERT廃止）にはAPI仕様・監査ログの変更が必要で、Mobile側の別判断になる。

**2026-10-18にBで正式化（人間承認）。** NexusMobile `develop` `6459abd`でも`apps/public-api`・`apps/admin-api`・`tools`・`deploy`は`87446aa`から変更なし。Mobile側で契約を変える場合は、この節と4節を先に改訂する。

### 3.1 改訂（2026-10-25、Issue #17）: 配信用 `delivery` スキーマ

NexusMobile [E2-5](https://github.com/m0o9l0v3/NexusMobile/blob/develop/docs/decisions/E2-5-participant-data-delivery-v2.md) 案b（2026-10-21採用）に合わせ、契約Bに `delivery` を加える。`public`の権限（上記）は変えない。public-apiは`GET /api/v2/bundle`で`delivery.current_bundle`と`delivery.participant_bundles`をSELECTするだけで、書き込みの経路を持たない（NexusMobile #121、マージ済み）。

- **配置**: `delivery`は、public-apiが接続し、Studioの`studio`スキーマがある**同じDB**（第1段階は現行PG17の`nexus`、NexusMobile `production-database.md` §0）に置く。ReleaseとbundleをStudioが同じトランザクションで書くため（E2-5 §3.1、M1-2）。Studioの配置は#8と整合させる。
- **作る役割（決定）**: `sql/pg17-baseline/02-delivery-schema.sql`がsuperuserで**スキーマを先に作り、所有をStudioのmigratorに渡す**。migratorにはDBのCREATE権限を与えない（作れるのは自分が所有する`studio`と`delivery`の中だけ）。表とトリガーはStudioのレビュー済み移行（NexusStudio#11）がmigratorで作る。もう一方の案（migratorにDBのCREATEを与える）はmigratorが任意のスキーマを作れるようになるため採らない。オーナーの意向は「どちらでもよい」（Studio判断記録29 M1-D3）。
- **権限**（`sql/pg17-baseline/03-grant-delivery.sql`、Studioの移行のたびに再実行）:

  | role | `delivery.participant_bundles` | `delivery.current_bundle` |
  |---|---|---|
  | Studioの実行時role | SELECT, INSERT | SELECT, INSERT, UPDATE |
  | `nexus_public` | SELECT | SELECT |
  | その他 | なし | なし |

  - `delivery`のUSAGEはStudioの実行時roleと`nexus_public`だけ（`PUBLIC`から剥奪）。default privilegesは設けず、`delivery`に表が増えると`03`は適用を拒否する（契約を先に改訂する）。
  - `participant_bundles`のUPDATE・DELETE・TRUNCATE、`current_bundle`のDELETE・TRUNCATEは誰にも与えない。所有者（migrator）の暗黙の権限は残り、E2-5 §3.5の将来の削除（レビュー済みの手動手順）にだけ使う。
  - `current_bundle.bundle_version`を減らすUPDATEはBEFORE UPDATEの行トリガーで拒否する（所有者にも効く）。`03`は有効なトリガーが無ければ適用を拒否する。初回の行はStudioが`INSERT … ON CONFLICT (singleton) DO UPDATE`で作る。
  - `nexus_public`は引き続き`studio`スキーマを読めない。public-apiのreadiness述語（上記）は変わらない。
- **Studioのrole名**: 実名は#8で確定する。`02`/`03`は`-v studio_migrator=… -v studio_app=…`で受け取り、隔離検証では仮名`nexus_studio_migrator`/`nexus_studio_app`（NexusStudio `deploy/database`の名前）を使った。
- **監視**: `sql/pg17-baseline/monitor-delivery-size.sql`を`nexus_public`で日次に実行し、配信表の合計サイズ（TOAST・索引込み）と行数を記録する。`review_required = t`（合計1 GiB超または500行超）なら削除方式の見直しを起票する（E2-5 §3.5）。[architecture.md](architecture.md)の日次確認に含めた。

## 4. Issue #7 の受け入れ条件（正式、契約B）

2026-10-18正式化。旧文「read-only roleでDB書き込みが拒否される」は契約Bと矛盾するため、次の条件で置き換える。すべて使い捨てPG17コンテナ・新規volume・ダミーデータで確認し、実本番DB・dump・secretは使わない。**隔離での成功を本番適用済みと記載しない。**

DB権限（`nexus_public`で接続）:

- [ ] `nexus_public`がDatabaseReadiness相当の判定を通る（非特権、`nexus_owner`非所属、`public`にCREATEなし、移行履歴が2本と一致）
- [ ] `spots`/`events`のSELECTは公開行（`is_published = true`）のみ。管理用表・将来追加表のSELECTは拒否
- [ ] `visit_logs`: INSERTと`chain_id, created_at, hash`のSELECTだけ成功。`session_id`等の読み取り、UPDATE、DELETE、TRUNCATEは拒否
- [ ] `spots`/`events`へのINSERT/UPDATE/DELETE、`__EFMigrationsHistory`の更新は拒否
- [ ] CREATE/ALTER/DROP、CREATE ROLE、`SET ROLE nexus_owner`、`COPY ... PROGRAM`は拒否

実イメージ（採用digest）とCaddy:

- [ ] 起動時にmigration/seedが走らない（public-apiはreadinessのみ）。特権role（`nexus`）の接続文字列ではreadinessで起動が失敗する
- [ ] Caddy（`api.localhost`、内部CA）経由で`/health`と代表GET（`/api/spots/public`、`/api/events/today`）が200
- [ ] Caddy経由の`POST /api/logs`が202を返し、`visit_logs`に行が増えてhash連鎖が続く
- [ ] `RateLimiting__KnownNetworks__0`にedge subnetを渡し、rate limitがCaddy経由のクライアントIP単位で働く（全クライアントがCaddyのIPで1つの枠を共有しない）
- [ ] [DEC-1案A（決定済み）](pg17-edge-decisions.md#dec-1-caddy越しのクライアントipとrate-limit): 設定値とedge subnetが一致し、edgeの実参加コンテナがCaddyとpublic-apiだけであることを確認する。Studio等の追加は信頼範囲の再判断後に行う
- [ ] 異なる接続元IPを持つ2クライアントで、一方が制限超過して429になっても他方の制限枠が残ることを確認する。同じIPを共有するクライアントは区別しない
- [ ] 採用Caddy版・digestで、クライアントが偽装した`X-Forwarded-For`・`X-Forwarded-Proto`を送っても、接続元IP・schemeの判定が偽装されず、rate limit枠を切り替えられないことを確認する
- [ ] `docker image inspect --format '{{.Config.User}}'`の実行UIDと、そのUID所有0400のsecret（`public_connection`、`audit_key`）が読めること
- [ ] public-api再起動後にDBへ再接続し、上記GETが再び成功する
- [ ] Caddyがbackend networkに参加していない。host portは80/443のみ
- [ ] 停止は`-p`明示、`down -v`不使用（volumeは手動で個別に削除）

記録（Issue #7へ）: public-api/Caddy/PG17のdigest、Mobile commit、`pg17-baseline.py manifest`のSQL sha256、各項目の合否と失敗条件。

2026-10-20の[新digest検証結果](pg17-issue-7-digest-verification.md): PR #99を統合した実API/Caddyの採用digestで、上記条件を確認した（隔離volumeは人間の再確認後に個別削除）。本番未適用。初回の不合格から再発行までの経緯は[初回検証記録](pg17-issue-7-verification.md)に残す。

### 4.1 Issue #17 の受け入れ条件（契約Bの改訂、`delivery`）

上記と同じく使い捨てPG17・新規volume・ダミーデータで確認する。**隔離での成功を本番適用済みと記載しない。**

DB権限（`pg17-baseline.py run`の`delivery`部分）:

- [x] `02`がスキーマを1回だけ作り所有をmigratorに渡す（再実行・role名未指定は拒否）。migratorは`CREATE SCHEMA`を拒否される（DBのCREATEなし）
- [x] `03`が表の欠けた`delivery`、無効化したトリガーを拒否し、正しい形では冪等に再適用できる。権限の一覧が上の表と完全に一致し、所有者以外に`participant_bundles`のUPDATE/DELETE/TRUNCATE、`current_bundle`のDELETE/TRUNCATEを持つroleが無い
- [x] `nexus_public`: 2表のSELECTだけ成功し、readiness述語にも引き続き合格。2表へのINSERT/UPDATE/DELETE/TRUNCATE、ポインタの後退、`delivery`へのCREATE、`studio`の読み取り、`SET ROLE`は拒否
- [x] Studioの実行時role: `participant_bundles`のINSERT、`ON CONFLICT`による初回の行の作成とポインタの前進、UPDATEによる前進が成功。ポインタの後退（トリガー）、2行目（CHECK）、存在しない版（外部キー）、`participant_bundles`のUPDATE/DELETE/TRUNCATE、`current_bundle`のDELETE/TRUNCATE、DDL・トリガー無効化、`public`の読み取り、migratorへの`SET ROLE`は拒否
- [x] 所有者でもポインタの後退はトリガーで拒否。権限の無いroleは2表を読めない
- [x] `monitor-delivery-size.sql`が`nexus_public`で動き、閾値超過を`review_required`で示す

2026-10-25の結果: [証跡](evidence/issue-17/2026-10-25/pg17-delivery-rehearsal.txt)（NexusMobile `63a3970`、PG17 digest `b0f9560a…`、97項目成功）。表とトリガーはE2-5 §3.1を写した検証専用のDDL（`sql/pg17-baseline/verification/delivery-reference-ddl.sql`）で作った。**Studioの実移行（NexusStudio#11）と#8の実role名で再確認するまで、本番に使う根拠にしない。**

edge（Caddy）経由（E2-5 O-4b、I4。#121を含むpublic-apiの採用digestとCaddy v2.11.4の採用digestで行う。未実施）:

- [ ] 200で接尾辞付きのETagが返る
- [ ] それを`If-None-Match`で送り返すと304になる（public-apiはETagを完全一致で比べるため、Caddyが接尾辞を外さずに転送すると304にならない）
- [ ] 200と304で`Nexus-Bundle-Sha256`・`Nexus-Bundle-Version`・`Nexus-Recheck-After`、200で`Nexus-Bundle-Schema`・`Nexus-Bundle-Length`が保たれる
- [ ] `Content-Length`が無い
- [ ] `Accept-Encoding`を変えると200になる

304が成立しない場合は、NexusMobile #23に`HEAD /api/v2/bundle`の追加を求める。NexusMobile #23はこの結果を待ってCloseする。

## 5. 隔離baselineの構築と検証

`compose.pg17-baseline.yml`と`scripts/pg17-baseline.py`は、**現行と同じ形（`nexus`/`nexus`、PG17）の使い捨てコンテナ**を新しい空volumeに作る。`nexus_postgres_data`や既存project名は拒否し、同じエンジンにlive container/volumeがあれば中止する。ポート公開なし。

```sh
# 1) Mobileの生成SQL（DB接続なし）。.NET 8 SDKが必要。
dotnet run --project tools/database/Nexus.Database.csproj -- script "$PG17_BASELINE_MIGRATE_SQL"
# 2) 隔離環境（Mac等）のみ。値は私有設定から読み込む。
export NEXUS_MOBILE_DIR=... PG17_BASELINE_MIGRATE_SQL=... PG17_BASELINE_PROJECT=nexus-pg17-baseline-<suffix> \
  PG17_BASELINE_VOLUME=nexus-pg17-baseline-<suffix>-db PG17_BASELINE_IMAGE=postgres@sha256:<digest> \
  PG17_BASELINE_SECRET_DIR=<新規の絶対パス>
python3 scripts/pg17-baseline.py preflight
python3 scripts/pg17-baseline.py manifest   # レビュー用manifest（commit・移行ID・SQLのsha256）
python3 scripts/pg17-baseline.py run        # 構築と97項目の検証。volumeは自動削除しない
```

`run`が確認すること: 空PG17.xの形が現行と一致／`sql/pg17-baseline/01-roles.sql`が1回だけ適用でき再実行は拒否／生成SQLが未知のtableを持つDBを拒否して行を保持／移行適用と冪等な再適用／`grant-runtime.sql`適用／履歴が2本と一致／`nexus`がreadinessに不合格で`nexus_public`が合格／上記3節の許可・拒否の全項目／誤パスワード拒否／3.1節の`delivery`（仮のStudio roleと検証専用DDLで、4.1節の全項目）。結果は`manifest`のSQL hashと合わせて記録する。

colima等のVMでは、secretの置き場所（`PG17_BASELINE_SECRET_DIR`）をVMが共有するディレクトリ（既定はホームの下）にする。`/tmp`等は共有されずコンテナ起動に失敗する。

2026-10-01にMacの使い捨てPG17.11で実行し47項目成功した（隔離のみ。本番適用の根拠にしない）。Mobile側の実.NETによる`DatabaseReadiness`（`tools/database verify`）は`nexus_verify`・loopbackを要求するため未実施で、本baselineはその述語をSQLで再現している。実イメージでのreadiness・HTTP確認はIssue #7で行う。

## 6. 本番適用前に人間が承認するSQL・操作（承認があるまで実行しない）

1. `sql/pg17-baseline/01-roles.sql`（初期化済みクラスタ用に`init-roles.sh`を改変）: `nexus_owner`/`nexus_admin_app`（NOLOGIN）/`nexus_public`（LOGIN、パスワードは環境変数経由）を作成、**DBの所有者を`nexus`から`nexus_owner`へ変更**、`PUBLIC`のDB権限・`public` schema権限を剥奪、`nexus_public`へCONNECT/USAGE。承認論点: DB所有者変更、`PUBLIC`のCONNECT剥奪（現行は`nexus`のみのため影響なしと確認する）、`nexus_migrator`を作らない代わりにsuperuser接続で移行する点、`nexus_admin_app`をNOLOGINで作る点。
2. `tools/database script`の生成SQL（`manifest`のsha256で固定）。直接`dotnet ef database update`はしない。対象DBは`nexus`、実行者は`SET ROLE nexus_owner`、影響は2表群の新規作成と履歴2行のみ（既存user tableが0件であることを実行直前に再確認）。
3. `deploy/database/grant-runtime.sql`（`SET ROLE nexus_owner`で実行）。
4. （Issue #17、Studioの導入時）#8のStudio role・`studio`スキーマの作成後に`sql/pg17-baseline/02-delivery-schema.sql`、Studioのレビュー済み移行（`delivery`の表とトリガー）の後に`sql/pg17-baseline/03-grant-delivery.sql`。どちらもsuperuserで、#8で確定したrole名を`-v`で渡す。影響は空の`delivery`スキーマの新規作成と、その2表への権限だけ（`public`・`studio`は変えない）。
5. 同日に行う順序・停止条件: 直前にPG17のバックアップとVPS外コピーと別volume復元（[17→18 gate](transition-postgres-17-to-18.md) Phase 1）が成功していること → 1→2→3の順に適用 → readiness確認。途中失敗時は適用を止め、**down migrationは使わず**、バックアップからの復元（別volume）を判断する。

**rollback境界**: `AddInitialPostgreSqlSchema.Down`は`NotSupportedException`。baseline適用後に戻す場合の唯一の手段はバックアップの復元で、適用前のバックアップが無い状態でSQLを実行しない。適用前に失敗した場合は何も変わらない。

## 7. 未解決・要確認

- 現行PG17の他の書込元・接続元（`nexus` superuserの利用者）。DB所有者変更・`PUBLIC`権限剥奪の影響
- PG17イメージのdigest（現行は`postgres:17-alpine`のタグ）と、Mobile採用commit/digestの固定
- Studioの実行時role・migratorの実名（#8）。確定後に`delivery`の隔離検証を実名とStudioの実移行（NexusStudio#11）で再確認する
- `delivery`のedge確認（4.1節、#121を含むpublic-apiの採用digestが必要）
