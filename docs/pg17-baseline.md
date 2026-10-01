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

**判断: B。** 根拠: `LogPersistenceService`が前回hashを`visit_logs`から読み、`visit_logs`へINSERTする。`LogsController`の`POST /api/logs`・`/api/logs/batch`（匿名参加者ログ、レート制限付き、202を返し非同期で永続化）から呼ばれる。MobileのCIと`tools/database verify`も「ログINSERT成功・hash連鎖・`session_id`読み取り拒否・UPDATE/DELETE拒否」を要求している。A（INSERT廃止）にはAPI仕様・監査ログの変更が必要で、Mobile側の別判断になる。**Bの正式化は人間とNexusMobile側で確認すること。**

## 4. Issue #7 の受け入れ条件（修正案）

現行文「read-only roleでDB書き込みが拒否される」はBと矛盾する。次に置き換える案:

- [ ] `nexus_public`がDatabaseReadiness相当の判定を通る（非特権、`nexus_owner`非所属、`public`にCREATEなし）
- [ ] `spots`/`events`のSELECTは公開行のみ（RLS）。管理用表・将来追加表のSELECTは拒否
- [ ] `visit_logs`: INSERTと`chain_id, created_at, hash`のSELECTだけ成功。`session_id`等の読み取り、UPDATE、DELETE、TRUNCATEは拒否
- [ ] `spots`/`events`へのINSERT/UPDATE/DELETE、`__EFMigrationsHistory`の更新は拒否
- [ ] CREATE/ALTER/DROP、CREATE ROLE、`SET ROLE nexus_owner`、`COPY ... PROGRAM`は拒否
- [ ] 起動時にmigration/seedが走らない（public-apiはreadinessのみ）
- [ ] 他の項目（Caddy経由の`/health`、UID、再起動後の再接続、port、`-p`明示・`down -v`不使用）は変更なし

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
python3 scripts/pg17-baseline.py run        # 構築と47項目の検証。volumeは自動削除しない
```

`run`が確認すること: 空PG17.xの形が現行と一致／`sql/pg17-baseline/01-roles.sql`が1回だけ適用でき再実行は拒否／生成SQLが未知のtableを持つDBを拒否して行を保持／移行適用と冪等な再適用／`grant-runtime.sql`適用／履歴が2本と一致／`nexus`がreadinessに不合格で`nexus_public`が合格／上記3節の許可・拒否の全項目／誤パスワード拒否。結果は`manifest`のSQL hashと合わせて記録する。

2026-10-01にMacの使い捨てPG17.11で実行し47項目成功した（隔離のみ。本番適用の根拠にしない）。Mobile側の実.NETによる`DatabaseReadiness`（`tools/database verify`）は`nexus_verify`・loopbackを要求するため未実施で、本baselineはその述語をSQLで再現している。実イメージでのreadiness・HTTP確認はIssue #7で行う。

## 6. 本番適用前に人間が承認するSQL・操作（承認があるまで実行しない）

1. `sql/pg17-baseline/01-roles.sql`（初期化済みクラスタ用に`init-roles.sh`を改変）: `nexus_owner`/`nexus_admin_app`（NOLOGIN）/`nexus_public`（LOGIN、パスワードは環境変数経由）を作成、**DBの所有者を`nexus`から`nexus_owner`へ変更**、`PUBLIC`のDB権限・`public` schema権限を剥奪、`nexus_public`へCONNECT/USAGE。承認論点: DB所有者変更、`PUBLIC`のCONNECT剥奪（現行は`nexus`のみのため影響なしと確認する）、`nexus_migrator`を作らない代わりにsuperuser接続で移行する点、`nexus_admin_app`をNOLOGINで作る点。
2. `tools/database script`の生成SQL（`manifest`のsha256で固定）。直接`dotnet ef database update`はしない。対象DBは`nexus`、実行者は`SET ROLE nexus_owner`、影響は2表群の新規作成と履歴2行のみ（既存user tableが0件であることを実行直前に再確認）。
3. `deploy/database/grant-runtime.sql`（`SET ROLE nexus_owner`で実行）。
4. 同日に行う順序・停止条件: 直前にPG17のバックアップとVPS外コピーと別volume復元（[17→18 gate](transition-postgres-17-to-18.md) Phase 1）が成功していること → 1→2→3の順に適用 → readiness確認。途中失敗時は適用を止め、**down migrationは使わず**、バックアップからの復元（別volume）を判断する。

**rollback境界**: `AddInitialPostgreSqlSchema.Down`は`NotSupportedException`。baseline適用後に戻す場合の唯一の手段はバックアップの復元で、適用前のバックアップが無い状態でSQLを実行しない。適用前に失敗した場合は何も変わらない。

## 7. 未解決・要確認

- B（`visit_logs` INSERT）の正式化をNexusMobile側で確認（#7の条件変更もこれに依存）
- 現行PG17の他の書込元・接続元（`nexus` superuserの利用者）。DB所有者変更・`PUBLIC`権限剥奪の影響
- PG17イメージのdigest（現行は`postgres:17-alpine`のタグ）と、Mobile採用commit/digestの固定
