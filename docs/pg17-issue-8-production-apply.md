# Issue #8 本番PG17へのStudio DB手順の適用（レビュー資料・運用者手順）

状態: **レビュー用。本番未適用・VPS操作なし。** この文書を書くためにClaudeが行ったのは、Macの使い捨てPG17での確認と、固定する入力のSHA-256の計算だけ。本番のDB・VPSには接続していない。ここに書いたSQL・操作は、0節の承認を受けてから運用者が実行する。

対象Issue: m0o9l0v3/NexusInfra#8の条件「SQL全文・対象cluster/DB/schema・既存データ影響・ロック・rollback境界のレビューと別承認」に答える。設計の正本はNexusStudio [`deploy/database/pg17/README.md`](https://github.com/m0o9l0v3/NexusStudio/blob/877ee90ca840ebe9f842aed4e92b72f50cc90d3c/deploy/database/pg17/README.md)と判断記録30、baseline側は[pg17-baseline.md](pg17-baseline.md) §3.2・§6。本書はそれらを1本の順序に並べ、各段のロックとrollback境界を実測して書き直したもの。

決まっていること（2026-11-02、利用者の回答）:

- public-apiのbaseline（B1〜B3）とStudioのDB手順（C1〜C5）は**同じ日に続けて**適用する。バックアップ（A）は1回
- 初期管理者の追加（NexusStudio README §2の7、`admin add`）は**今回の承認に含めない**。本番用Studio API imageのdigestと発行経路が無いため、#9側でimageとcompose定義が決まってから別の承認項目にする。初期管理者は利用者本人の1名（値は私有メモだけ）
- `setup-studio-roles.sh`はVPSのホストではなく、**稼働中PG17と同じimageの一時clientコンテナ**で実行する（`.sh`の事前検査をそのまま効かせるため）

この適用でStudioのサイト・サービスは有効にならない。composeの変更、Studioコンテナの起動、edge（Caddy）への追加は行わない（#8完了まで有効化しない。公開ルートと`TrustedNetwork`は#9）。

## 0. 何を承認すれば何が実行されるか

| 承認 | 実行されること | 本番への変化 | 戻し方 |
|---|---|---|---|
| **P**（承認不要・読み取りのみ） | 4節の事前確認。`system_identifier`等の値は私有メモへ | なし | — |
| **A** バックアップ | [17→18 gate](transition-postgres-17-to-18.md) Phase 1：`backup-pg17-source.sh`（`ALLOW_PG17_EXPORT=1`）、VPS外への暗号化コピー、Macの隔離PG17・新規volumeへの復元 | なし（読み取りのみのdump。元volumeは触らない） | — |
| **B** public-api baseline | B1 `01-roles.sql`、B2 Mobileの移行SQL、B3 `grant-runtime.sql`、B4 確認 | role 3つ、DB `nexus`の所有者変更と`PUBLIC`権限の剥奪、`public`に13表と移行履歴、権限とRLS | Aの復元だけ（`AddInitialPostgreSqlSchema.Down`は`NotSupportedException`） |
| **C** Studio DB | C1 `setup-studio-roles.sh`（`ALLOW_PRODUCTION_STUDIO_PG17_ROLE_SETUP=1`）、C2 `02-delivery-schema.sql`、C3 Studioの移行SQL、C4 `grant-studio-runtime.sql`、C5 `03-grant-delivery.sql`、C6 確認 | role 2つ、`studio`と`delivery`のschemaとその中の表・トリガー・権限。`public`の表と`nexus_public`の`public`の権限は変えない | 最初のbundle公開前ならR1、公開後はAの復元 |
| **R1**（条件付き） | Cが途中で失敗し、Studio側をやり直すと判断したときだけ、NexusStudio README §4の削除SQL（5.5節） | `studio`・`delivery`・Studio role 2つの削除。`public`は残る | — |
| **R2**（別計画） | Aの復元先volumeへの切り替え | PG17コンテナの停止と付け替え | 本書の範囲外。必要になったら別に計画して承認する |
| D（今回は対象外） | 初期管理者の追加（`admin add`） | `studio`のIdentity表に1行 | — |

承認はA・B・Cを別々に出す。同じ日に続ける場合も、Aの合格（復元の成功）を確かめてからBへ、B4の合格を確かめてからCへ進む。

## 1. 対象の固定と入力

### 1.1 対象

| 項目 | 値 |
|---|---|
| クラスタ | 現行PG17（`postgres:17-alpine`、volume `nexus_postgres_data`）。4節P2で読み取った`system_identifier`（値は私有メモ）をC1の`NEXUS_STUDIO_EXPECTED_SYSTEM_ID`に渡す |
| DB | `nexus`（`postgres` DBには何もしない） |
| schema | Bは`public`、Cは`studio`と`delivery`（どちらも新規） |
| 実行者 | superuser `nexus`。B2はSQLの先頭で`SET ROLE nexus_owner`、B3は`SET ROLE nexus_owner`を前置、C3は`nexus_studio_migrator` |
| 接続方法 | B・C2〜C5は稼働中PG17コンテナへの`docker exec -i --user postgres … psql`（[運用者チェックリスト](pg17-operator-checklist.md) V3と同じ形。コンテナ内のUnixソケット）。C1だけ一時clientコンテナからDBのnetwork alias経由（5.3節） |

### 1.2 固定する入力（SHA-256）

2026-11-02に`scripts/pg17-baseline.py manifest`で計算した（[manifest](evidence/issue-8/2026-11-02/production-apply-manifest.json)）。2026-11-01の隔離検証（[証跡](evidence/issue-8/2026-11-01/pg17-studio-rehearsal-rename-migration.txt)、104項目成功）のmanifestと**全ファイルで同じ値**。違いはNexusStudioのcommitだけで、`36ef517..877ee90`の間に`apps/studio-api/Migrations`と`deploy/database`の変更は無い。

| 段 | ファイル | 出所 | SHA-256 |
|---|---|---|---|
| B1 | `sql/pg17-baseline/01-roles.sql` | NexusInfra `a98c838` | `0ec65dce204f27fd01db974cc9508f2e36594cfe1708b0c84af0aca80fd4d65b` |
| B2 | Mobileの移行SQL（`tools/database script`の出力） | NexusMobile `6f1b079`で生成（DB接続なし） | `7cf58c307611ebec07e617add3f18d7fa65eee30c6de16dc2d989d5e4a9da763` |
| B3 | `deploy/database/grant-runtime.sql` | NexusMobile `6f1b079` | `456bbf9836b5fede04fa0d914beaca743456ee84452c63db13479667c8e9c134` |
| C1 | `deploy/database/pg17/setup-studio-roles.sh` | NexusStudio `877ee90` | `951f09c85e58df6d8e013451044b6ef8b747c1041ea618b275799fb39afbf62f` |
| C1 | `deploy/database/pg17/setup-studio-roles.sql` | NexusStudio `877ee90` | `769fd80e26624598d7e5f42b2db674175ac7cf4a9ba731f94d4eeb1490b4a53f` |
| C2 | `sql/pg17-baseline/02-delivery-schema.sql` | NexusInfra `a98c838` | `b71231e47693c3957a04b48fe339fbf769778789fa6fd02068edbe8b3cd65b93` |
| C3 | Studioの移行SQL（`migrate.sql`、14移行） | NexusStudio CI `studio-migration-script`（[run 41391080926](https://github.com/m0o9l0v3/NexusStudio/actions/runs/41391080926)、`877ee90`） | `a1eb2bbc314144487fd5ba48e89841ec2c9e2960190eb1e8c83bd193fe60c835` |
| C4 | `deploy/database/grant-studio-runtime.sql` | NexusStudio `877ee90` | `d662f59548e90615d16b416a208ce8b5813eeb529960cad75fd4b1ef3050ce4a` |
| C5 | `sql/pg17-baseline/03-grant-delivery.sql` | NexusInfra `a98c838` | `f7d516a8ac9488bfa0c3dc1d674b836d54363cff966e3376ae14faceadc590a2` |

C3: CIのartifactに記録された`migrate.sql.sha256`、ダウンロードしたファイルの実測、ローカル生成（2026-11-01）の3つが`a1eb2bbc…`で一致した。artifactの保持期間は7日なので、適用日にはCIの再実行かローカル生成で取り直し、同じSHA-256であることを確かめてから使う（SQLは`.gitignore`の対象なのでGitには入れない）。B2も同様に適用日に生成し直して照合する。

**SQL全文のレビュー**: B1・C2・C5・B3・C1・C4は上のcommitのファイルそのもの。B2とC3は上の手順で生成したファイルを読む。`pg17-baseline.py manifest`が機械的に確かめていること: B2は移行ID 2本だけ・`SET ROLE nexus_owner`で始まる・ROLE/GRANT/REVOKE/DROP/TRUNCATE/COPYを含まない・既存schemaの拒否とadvisory lockを含む。C3は移行IDがソースの14本と一致・配信の2移行を含む・ROLE/SET ROLE/GRANT/REVOKE/TRUNCATE/COPY/`public.`を含まない・`studio`/`delivery`以外に表や索引を作らない。Studio APIもpublic-apiも起動時に移行しない。

**入力が変わったら止める**: 適用日に、NexusStudio `develop`の`877ee90`以降に`apps/studio-api/Migrations`か`deploy/database`の変更がある、NexusMobile `develop`の`6f1b079`以降に`apps/admin-api/Migrations`・`deploy/database`・`tools/database`の変更がある、またはNexusInfraの`sql/pg17-baseline`が変わっている場合は、適用せず、隔離検証（[pg17-baseline.md](pg17-baseline.md) §5）とこの表を更新して再レビューする。

## 2. 順序表

ロックの列は2026-11-02の使い捨てPG17.11（採用digest `b0f9560a…`）での実測（3節）。全段で`PGOPTIONS='-c lock_timeout=5s'`を付け、ロック待ちが起きたら待たずに失敗させる。

| # | 実行者 | 入力 | 既存データへの影響 | ロック（実測） | トランザクション | 止める条件 | 失敗時 |
|---|---|---|---|---|---|---|---|
| A | root（VPS）→ Mac | `backup-pg17-source.sh` | なし | dumpの読み取りのみ | — | 復元・照合のどれかが失敗、コピーのSHA-256不一致 | Bへ進まない |
| B1 | `nexus` | `01-roles.sql` | DB `nexus`の所有者が`nexus`→`nexus_owner`。DBの`PUBLIC`権限（CONNECT・TEMPORARY）と`public` schemaの`PUBLIC`権限を剥奪。role 3つを新規作成 | `pg_authid`・`pg_auth_members`・`pg_database`の行（RowExclusive）と新roleのオブジェクトロックだけ。表のロックなし。DBのオブジェクトロックも取らない | 1つ | psqlが0以外で終わる、B1後の確認（5.2節）が期待値と違う | 何も残らない（1トランザクション）。原因を直して再レビュー |
| B2 | `nexus`（SQL内で`SET ROLE nexus_owner`） | Mobileの移行SQL | `public`に13表と`__EFMigrationsHistory`（2行）を新規作成。既存の表があれば先頭の検査で拒否 | セッションのadvisory lock（`hashtext('nexus-schema-migration')`）。新しく作る表だけにAccessExclusive等 | 履歴表の作成は外、移行ごとに1つ（2つ） | psqlが0以外 | 3.2節。前半の移行は残り得る。同じSQLは冪等なので原因を直して再実行するか、Aの復元を判断 |
| B3 | `nexus`（`SET ROLE nexus_owner`を前置） | `grant-runtime.sql` | `public`の表の権限、`spots`・`events`のRLSとpolicy | **`public.spots`・`public.events`にAccessExclusive**（COMMITまで） | 1つ | psqlが0以外、B4が不合格 | 何も残らない。public-apiは未導入なので読み手はいない |
| B4 | `nexus` | 5.2節の確認SQL | なし（読み取り） | — | — | 期待値と違う | Cへ進まない |
| C1 | `nexus`（一時clientコンテナ） | `setup-studio-roles.sh` + `.sql` | role 2つ、DB `nexus`へのCONNECT、空の`studio`を新規作成 | `pg_authid`・`pg_auth_members`の行と新roleのオブジェクトロックだけ | 1つ | `.sh`が事前検査で止まる、psqlが0以外 | 何も残らない |
| C2 | `nexus` | `02-delivery-schema.sql`（`-v`でrole名） | 空の`delivery`を新規作成し所有をmigratorへ | 表のロックなし | 1つ | psqlが0以外 | 何も残らない |
| C3 | `nexus_studio_migrator` | Studioの移行SQL | `studio`と`delivery`の表・索引・トリガー・関数を新規作成。`public`には触れない | `studio`・`delivery`の中の自分が作る/変える対象だけ。`public`のロックなし | 先頭の`studio`と履歴表の作成は外、移行ごとに1つ（14） | psqlが0以外 | 3.3節。前半の移行は残る。履歴を記録し、再実行かR1を判断 |
| C4 | `nexus` | `grant-studio-runtime.sql` | `studio`の表の権限だけ | 表のロックなし | 1つ | psqlが0以外 | 何も残らない |
| C5 | `nexus` | `03-grant-delivery.sql`（`-v`でrole名） | `delivery`の2表の権限だけ。形（2表・所有者・トリガー）が違えば拒否 | 表のロックなし | 1つ | psqlが0以外 | 何も残らない |
| C6 | `nexus` | 5.4節の確認SQL | なし | — | — | 期待値と違う | Studioを使わない。R1かそのまま保留かを判断 |

## 3. 影響・ロック・rollback境界の確認結果

### 3.1 ロックの実測（2026-11-02、使い捨て）

[証跡](evidence/issue-8/2026-11-02/pg17-lock-probe.txt)・[スクリプト](evidence/issue-8/2026-11-02/pg17-lock-probe.sh)。PG17.11（`postgres@sha256:b0f9560a…`）をtmpfs・ネットワークなし・`--rm`で起動し（volumeは作らない）、1.2節と同じファイルを2節の順で適用した。

- 1トランザクションの段（B1・B3・C1・C2・C4・C5）は、最後の`COMMIT`を「自分のセッションが持つロックの一覧＋`ROLLBACK`」に置き換えた写しを先に流し、その後に本物を適用した。複数トランザクションの段（B2・C3）は、各`COMMIT`の直前にロックの一覧を挟んで本物として適用した
- C1〜C5の間、別のセッションが`nexus_public`で`spots`・`events`・`visit_logs`を読んだままトランザクションを開け続けた（public-apiの読み手の代わり）。C1〜C5はすべて`lock_timeout=5s`で成功し、その読み手も最後まで動いていた。C1〜C5のロック一覧に`public`の表は1つも現れなかった（書き込み側とも衝突しない）
- B3だけが既存の`public.spots`・`public.events`にAccessExclusiveを取る（`ALTER TABLE … ENABLE ROW LEVEL SECURITY`とpolicyの作成）。今回はpublic-apiが未導入なので影響はないが、**public-apiの稼働後に`grant-runtime.sql`を再実行すると、その間`spots`・`events`の読み取りが止まる**。後の再実行は別に計画する
- C3は`ALTER TABLE studio.… DROP CONSTRAINT`と`DROP INDEX studio.ix_operation_logs_started_at`を含むが、対象はすべて同じSQLの前半で作った`studio`の中のもの
- C3のCI artifactは先頭にUTF-8のBOMがある。BOMを外した写しで適用した後、BOM付きのartifactをそのまま2回目として流し、psql 17が受け付けて成功することを確かめた（本番ではBOMを外さずに流す。外すとSHA-256が変わる）
- C3の2回目の適用は何もせずに成功した（冪等）
- 5節のコマンド（`SHA256SUMS`の照合、P2・P3・P5、B1〜B4、C1の一時clientコンテナ、C2〜C6、5.5節の履歴の問い合わせ）を、別の使い捨てPG17（DBのaliasを持つinternal network付き）に対して書いたとおりに流し、各段の期待値がそのまま出ることを確かめた（[スクリプト](evidence/issue-8/2026-11-02/pg17-operator-commands-check.sh)、出力は上の証跡の3節）。C1は誤った`system_identifier`で終了コード2で拒否され、B1は空のパスワードで終了コード0のまま何も作らなかった

### 3.2 B（baseline）のrollback境界

- B1・B3は1トランザクション。失敗すれば何も残らない
- **B1の注意**: `NEXUS_PUBLIC_PASSWORD`が空だと、`01-roles.sql`はメッセージを出して`\quit`し、**何もせずに終了コード0で終わる**。終了コードだけで合格にせず、5.2節の確認で`nexus_public`があることを確かめる
- B2はEFの形式で、履歴表の作成はトランザクションの外、2つの移行はそれぞれ`START TRANSACTION … COMMIT`。2本目で失敗すると1本目（`map_datasets`）と履歴1行が残る。同じSQLは冪等なので原因を直して再実行できる。`Down`は無い
- Bを適用した後に「適用前」へ戻す手段はAの復元（R2）だけ。B1の所有者変更と`PUBLIC`剥奪を手で戻すSQLは用意しない

### 3.3 C（Studio）のrollback境界

- C1・C2・C4・C5は1トランザクション。失敗すれば何も残らない。C1は2回目を拒否し、C2も既存の`delivery`を拒否する
- C3は**移行1件ごとにコミットされる**（14個の`START TRANSACTION … COMMIT`。CI artifactで数を確認）。先頭の`studio`（C1で作成済みなので何もしない）と`studio."__EFMigrationsHistory"`の作成はトランザクションの外。後半で失敗すると、前半の移行の表と履歴は残る。**失敗後のDBを適用前と見なさない**
  - 止めて、5.5節の問い合わせで`studio."__EFMigrationsHistory"`を読み、1.2節の14本と照らしてどこまで入ったかを記録する
  - 原因を直した同じSQL（冪等）の再実行で残りを入れるか、R1で取り除いてC1からやり直すかを、記録を添えて判断する。C4・C5はC3が最後まで終わってからにする
- Studioが`delivery`へ最初のbundleを書く前なら、R1でCの全部を取り除ける。使い捨て環境でC5の後にR1を流し、Studio roleが0件、`studio`/`delivery`が0件、`public`の表14件（13表＋履歴）が残ることを確かめた
- 最初のbundleを公開した後はR1を使わない（参加者向け配信が依存する）。戻すならAの復元を判断する。今回の適用ではStudioを起動しないので、bundleは書かれない

## 4. 事前の読み取り確認（運用者・VPS）

[運用者チェックリスト](pg17-operator-checklist.md)の記録ルールに従う: 実値（`system_identifier`、IP、subnet、container ID、接続元、パス、パスワード）は**VPS私有メモだけ**に書き、Git・Issue・PR・会話に貼らない。VPSでは読み取りだけ。`<PG17_CONTAINER>`はチェックリストV1で確認した名前。

適用日の当日、Aの前に行う（A・B・Cの承認の材料にもなるので、前日までに一度行い、当日にもう一度行う）。

### P1. image・版・digest

```sh
docker inspect --type container --format '{{.Config.Image}} {{.Image}} {{.State.Health.Status}}' <PG17_CONTAINER>
docker exec --user postgres <PG17_CONTAINER> postgres --version
docker image inspect --format '{{json .RepoDigests}}' "$(docker inspect --type container --format '{{.Image}}' <PG17_CONTAINER>)"
```

- 期待値: `postgres:17-alpine`、`healthy`、`PostgreSQL 17.x`。最後の行の`postgres@sha256:…`が隔離検証の`postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`（17.11）と同じ
- digestは秘密ではないのでIssueに書いてよい
- 止める条件: 上のどれかが違う。digestが違う（マイナー版が違う）ときは、その本番digestを`PG17_BASELINE_IMAGE`にしてMacで`pg17-baseline.py run`をやり直し、成功してから再レビューする。`[]`でdigestを特定できないときも止める

### P2. 対象の識別子と設定

```sh
docker exec --user postgres <PG17_CONTAINER> psql -X -At -U nexus -d nexus -c "SELECT current_database(), current_user, current_setting('server_version_num'), current_setting('password_encryption'), system_identifier FROM pg_control_system()"
```

- 期待値: `nexus|nexus|170xxx|scram-sha-256|<数字>`。最後の数字を私有メモに`system_identifier`として書く（C1で使う）。`server_version_num`はP1の版と一致
- 止める条件: `password_encryption`が`scram-sha-256`でない（C1が拒否する）、パスワードを求められる（ソケット接続の前提が違う）

### P3. 既存のrole・schema・表が無いこと

```sh
docker exec --user postgres <PG17_CONTAINER> psql -X -At -U nexus -d nexus -c "SELECT 'roles', string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles WHERE rolname NOT LIKE 'pg\_%' UNION ALL SELECT 'schemas', string_agg(nspname, ',' ORDER BY nspname) FROM pg_namespace WHERE nspname NOT LIKE 'pg\_%' AND nspname <> 'information_schema' UNION ALL SELECT 'user_tables', count(*)::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','p','v','m','f','S') AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema' UNION ALL SELECT 'databases', string_agg(datname, ',' ORDER BY datname) FROM pg_database WHERE NOT datistemplate UNION ALL SELECT 'nexus_owner', datdba::regrole::text FROM pg_database WHERE datname = 'nexus' UNION ALL SELECT 'extensions', string_agg(extname, ',' ORDER BY extname) FROM pg_extension"
```

- 期待値: `roles|nexus`、`schemas|public`、`user_tables|0`、`databases|nexus,postgres`、`nexus_owner|nexus`、`extensions|plpgsql`
- この行は秘密を含まないので、合否と「差異なし」をIssueに書いてよい（値そのものは書かなくてよい）
- 止める条件: 1つでも違う（baselineの前提が崩れる。`nexus_owner`・`nexus_public`・Studio role・`studio`・`delivery`のどれかが既にある場合も含む）

### P4. 接続元と他の書き込み元（pg17-baseline.md §7）

```sh
docker exec --user postgres <PG17_CONTAINER> psql -X -U nexus -d postgres -c "SELECT usename, datname, application_name, client_addr, state, count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1,2,3,4,5 ORDER BY 1,2"
docker inspect --type container --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{$v.Aliases}}{{"\n"}}{{end}}' <PG17_CONTAINER>
for n in $(docker inspect --type container --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' <PG17_CONTAINER>); do docker network inspect "$n" --format '{{.Name}}: {{range .Containers}}{{.Name}} {{end}}'; done
```

- 期待値: 接続は自分の`psql`だけ（または用途を説明できる）。DBのnetworkに参加しているコンテナがPG17だけ（または用途を説明できる）
- 記録: DBのnetwork名とalias（C1で使う。私有メモ）、他の参加コンテナの有無と用途
- 判断: B1は`nexus`以外のroleのDB `nexus`への新しい接続を閉じる。`nexus` superuserで接続しているものは所有者変更・`PUBLIC`剥奪の影響を受けない（superuserは権限検査を通る）。`postgres` DBは変えない。したがって、P4で見つかった接続元が`nexus`で接続しているなら影響なし、別のroleなら止める
- 止める条件: 説明できない接続元・参加コンテナがある、`nexus`以外のroleで接続しているものがある

### P5. 認証設定（C1の接続経路）

```sh
docker exec <PG17_CONTAINER> sh -c 'grep -Ev "^[[:space:]]*(#|$)" "$PGDATA/pg_hba.conf"'
```

- 期待値（隔離検証のimageの既定と同じ形）: `local all all trust`の行と、`host all all all scram-sha-256`の行がある
- 判断: B・C2〜C5はコンテナ内のソケット（`local`）で接続する。C1の一時clientコンテナはnetwork経由（`host … scram-sha-256`）で`nexus`のパスワードを使う
- 止める条件: `local`が`trust`でない、またはnetwork経由の`nexus`が`scram-sha-256`で許可されていない（5.3節の接続が成立しないので、手順を見直して再レビュー）

### P6. 容量とバックアップの置き場所

チェックリストV6・V7と同じ（`df -h / /var/lib/docker`、`PG17_BACKUP_DIR`がroot所有0700で存在すること）。

- 止める条件: 空きが少なく判断できない、バックアップの置き場所が決まっていない

## 5. 当日の手順（運用者）

### 5.0 準備（VPSの外とVPS）

1. 1.2節の「入力が変わったら止める」を確認する
2. Macで、B2（`tools/database script`）とC3（CI artifactの取得またはローカル生成）を作り、1.2節のSHA-256と一致することを確かめる
3. 1.2節の9ファイルをVPSのroot所有0700の作業ディレクトリ（例: `/root/nexus-issue8-<日付>/`。パスは私有メモ）へ送る。ファイル名は次の`SHA256SUMS`のとおりにする。`deploy/database/pg17/`の2ファイルは`studio-pg17/`の下に置く（C1で読み取り専用でマウントする）

   ```text
   0ec65dce204f27fd01db974cc9508f2e36594cfe1708b0c84af0aca80fd4d65b  01-roles.sql
   7cf58c307611ebec07e617add3f18d7fa65eee30c6de16dc2d989d5e4a9da763  mobile-migrate.sql
   456bbf9836b5fede04fa0d914beaca743456ee84452c63db13479667c8e9c134  grant-runtime.sql
   951f09c85e58df6d8e013451044b6ef8b747c1041ea618b275799fb39afbf62f  studio-pg17/setup-studio-roles.sh
   769fd80e26624598d7e5f42b2db674175ac7cf4a9ba731f94d4eeb1490b4a53f  studio-pg17/setup-studio-roles.sql
   b71231e47693c3957a04b48fe339fbf769778789fa6fd02068edbe8b3cd65b93  02-delivery-schema.sql
   a1eb2bbc314144487fd5ba48e89841ec2c9e2960190eb1e8c83bd193fe60c835  studio-migrate.sql
   d662f59548e90615d16b416a208ce8b5813eeb529960cad75fd4b1ef3050ce4a  grant-studio-runtime.sql
   f7d516a8ac9488bfa0c3dc1d674b836d54363cff966e3376ae14faceadc590a2  03-grant-delivery.sql
   ```

   VPSで`cd <作業ディレクトリ> && sha256sum -c SHA256SUMS`がすべて`OK`になること。1つでも違えば止める
4. パスワードを3つ作り、root所有0600のファイルに置く（`nexus_public`、`nexus_studio_migrator`、`nexus_studio_app`。各32文字以上で互いに異なる。画面・履歴・Gitに出さない）。例: `sudo sh -c 'umask 077; openssl rand -hex 24 > <私有ディレクトリ>/nexus_public_password'`（48文字）。`nexus_public`のパスワードは後でpublic-apiの`public_connection`に、`nexus_studio_app`は#9でStudioの接続に使う
5. C1用に、`nexus`の接続情報をroot所有0600の`pgpass`ファイルにする（`<DB alias>:5432:nexus:nexus:<nexusのパスワード>`。私有メモの値。コンテナ内でもroot所有0600のまま使う）
6. 4節P1〜P6を行い、すべて合格であること

以降のコマンドは、作業ディレクトリでrootのシェルから実行する。共通の形:

```sh
C=<PG17_CONTAINER>
# 引数はpsqlに渡す（-v など）。NEXUS_PUBLIC_PASSWORDはシェルでexportされているときだけコンテナへ渡る
psql_su() { docker exec -i -e PGOPTIONS='-c lock_timeout=5s' -e NEXUS_PUBLIC_PASSWORD --user postgres "$C" psql -X -v ON_ERROR_STOP=1 -U nexus -d nexus "$@"; }
```

### 5.1 A: バックアップ（承認A）

[17→18 gate](transition-postgres-17-to-18.md) Phase 1のとおり。`PG17_CONTAINER=<PG17_CONTAINER> PG17_DB_USER=nexus PG17_DB_NAME=nexus PG17_BACKUP_DIR=<root所有0700> ALLOW_PG17_EXPORT=1 scripts/backup-pg17-source.sh`、`SHA256SUMS`の照合、承認した暗号化方式でVPS外（Mac）へコピー、Macの隔離PG17（`compose.pg17-restore.yml`、P1と同じdigest、新規の空volume）への`pg_restore`。

- 合格: Macへのコピーの保存完了時刻・サイズ・SHA-256を記録し、復元したDBでP3と同じ問い合わせが同じ結果になる（今回は空のDBの形を確かめる）
- 止める条件: どれか1つでも失敗。**Aが合格するまでBを実行しない**（Bを戻す唯一の手段がこの復元）
- 注: dumpは`--no-role-passwords`・`--no-owner --no-acl`。復元で戻るのは「空の`nexus` DB」の形で、`nexus`のパスワードやACLは戻らない。R2を計画するときはこの点を含める

### 5.2 B: baseline（承認B）

```sh
# B1（NEXUS_PUBLIC_PASSWORDは私有ファイルから。値を表示しない）
export NEXUS_PUBLIC_PASSWORD="$(cat <私有ディレクトリ>/nexus_public_password)"
psql_su < 01-roles.sql
unset NEXUS_PUBLIC_PASSWORD
# B2
psql_su < mobile-migrate.sql
# B3（nexus_ownerとして実行）
{ echo 'SET ROLE nexus_owner;'; cat grant-runtime.sql; } | psql_su
# B4 確認（読み取り）
psql_su -A -t <<'SQL'
SELECT 'roles', string_agg(rolname, ',' ORDER BY rolname) FROM pg_roles WHERE rolname NOT LIKE 'pg\_%';
SELECT 'db_owner', datdba::regrole FROM pg_database WHERE datname = 'nexus';
SELECT 'public_acl_on_db', count(*) FROM pg_database d, aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a WHERE d.datname = 'nexus' AND a.grantee = 0;
SELECT 'history', string_agg("MigrationId", ',' ORDER BY "MigrationId") FROM public."__EFMigrationsHistory";
SELECT 'public_tables', count(*) FROM pg_tables WHERE schemaname = 'public';
SET ROLE nexus_public;
SELECT 'readiness_unfit', r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls OR pg_has_role(current_user, 'nexus_owner', 'MEMBER') OR has_schema_privilege(current_user, 'public', 'CREATE') FROM pg_roles r WHERE r.rolname = current_user;
RESET ROLE;
SQL
```

- B1〜B3: 各コマンドが終了コード0で、`ERROR`を出さない
- B4の期待値: `roles|nexus,nexus_admin_app,nexus_owner,nexus_public`、`db_owner|nexus_owner`、`public_acl_on_db|0`、`history|20260905120000_AddMapDatasets,20260919120000_AddInitialPostgreSqlSchema`、`public_tables|14`、`readiness_unfit|f`
- 止める条件: どれかが違う。B1で`NEXUS_PUBLIC_PASSWORD is empty`が出た（終了コード0でも何も作られていない。3.2節）
- 失敗時: 2節の表と3.2節。B2の途中失敗は5.5節と同じ要領で`public."__EFMigrationsHistory"`を読んで記録する

### 5.3 C1: Studioのroleと`studio`（承認C）

稼働中PG17と同じimage（P1のimage ID。pullしない）の一時clientコンテナを、DBのnetworkに参加させて`.sh`をそのまま実行する。パスワードはファイルをマウントし、コンテナの中で環境変数にする（`docker run -e`に値を書かない）。

```sh
IMG="$(docker inspect --type container --format '{{.Image}}' "$C")"
docker run --rm -i --network <DBのnetwork名> --user root \
  -v "$PWD/studio-pg17:/w:ro" -v <私有ディレクトリ>:/run/s:ro \
  -e PGOPTIONS='-c lock_timeout=5s' -e PGPASSFILE=/run/s/pgpass \
  -e NEXUS_STUDIO_TARGET_ENV=production -e ALLOW_PRODUCTION_STUDIO_PG17_ROLE_SETUP=1 \
  -e NEXUS_STUDIO_TARGET_HOST=<DBのalias> -e NEXUS_STUDIO_TARGET_PORT=5432 \
  -e NEXUS_STUDIO_EXPECTED_SYSTEM_ID=<P2のsystem_identifier> \
  --entrypoint sh "$IMG" -c 'NEXUS_STUDIO_MIGRATOR_PASSWORD="$(cat /run/s/nexus_studio_migrator_password)" NEXUS_STUDIO_APP_PASSWORD="$(cat /run/s/nexus_studio_app_password)" exec sh /w/setup-studio-roles.sh'
```

- `.sh`がDDLの前に、DB名・実行者・superuser・17.x・`system_identifier`・パスワード長と相違・承認フラグを確かめる。SQL側でもbaseline（`nexus_owner`が所有者、`PUBLIC`権限なし）、既存のStudio role・`studio`・`delivery`が無いこと、`scram-sha-256`を確かめる
- `ALLOW_PRODUCTION_STUDIO_PG17_ROLE_SETUP=1`は承認Cを受けたときだけ付ける。承認の代わりではない
- 止める条件: `.sh`が`PostgreSQL target does not match…`等で終了コード2、psqlが0以外

### 5.4 C2〜C6（承認C）

```sh
# C2
psql_su -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app < 02-delivery-schema.sql
# C3（migratorとして。移行1件ごとにコミットされる。SQLにON_ERROR_STOPが無いので -v ON_ERROR_STOP=1 を必ず付ける）
docker exec -i -e PGOPTIONS='-c lock_timeout=5s' --user postgres "$C" psql -X -v ON_ERROR_STOP=1 -U nexus_studio_migrator -d nexus < studio-migrate.sql
# C4
psql_su < grant-studio-runtime.sql
# C5
psql_su -v studio_migrator=nexus_studio_migrator -v studio_app=nexus_studio_app < 03-grant-delivery.sql
# C6 確認（読み取り）
psql_su -A -t <<'SQL'
SELECT 'studio_roles', string_agg(rolname || ':' || (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls OR rolreplication)::text, ',' ORDER BY rolname) FROM pg_roles WHERE rolname LIKE 'nexus\_studio\_%';
SELECT 'schemas', string_agg(nspname || ':' || nspowner::regrole::text, ',' ORDER BY nspname) FROM pg_namespace WHERE nspname IN ('studio', 'delivery');
SELECT 'studio_history', count(*), max("MigrationId") FROM studio."__EFMigrationsHistory";
SELECT 'delivery_tables', string_agg(tablename || ':' || tableowner, ',' ORDER BY tablename) FROM pg_tables WHERE schemaname = 'delivery';
SELECT 'public_cannot_use_studio', NOT has_schema_privilege('nexus_public', 'studio', 'USAGE');
SELECT 'app_cannot_write_history', NOT has_table_privilege('nexus_studio_app', 'studio."__EFMigrationsHistory"', 'INSERT');
SELECT 'migrator_no_db_create', NOT has_database_privilege('nexus_studio_migrator', 'nexus', 'CREATE');
SELECT 'public_tables_unchanged', count(*) FROM pg_tables WHERE schemaname = 'public';
SELECT 'delivery_rows', (SELECT count(*) FROM delivery.participant_bundles) + (SELECT count(*) FROM delivery.current_bundle);
SQL
```

- C2〜C5: 各コマンドが終了コード0で`ERROR`を出さない
- C3の`ON_ERROR_STOP`: StudioのSQL（EFの出力）は`\set ON_ERROR_STOP on`を含まない。付けずに流すと、失敗した移行のトランザクションが取り消された後も次の移行が続けて流れ、履歴に穴ができ得る。必ず`-v ON_ERROR_STOP=1`で流す
- C6の期待値: `studio_roles|nexus_studio_app:false,nexus_studio_migrator:false`、`schemas|delivery:nexus_studio_migrator,studio:nexus_studio_migrator`、`studio_history|14|20261101135153_AddRenameRevisionSource`、`delivery_tables|current_bundle:nexus_studio_migrator,participant_bundles:nexus_studio_migrator`、`public_cannot_use_studio|t`、`app_cannot_write_history|t`、`migrator_no_db_create|t`、`public_tables_unchanged|14`、`delivery_rows|0`
- 止める条件: どれかが違う
- 権限の許可・拒否の網羅的な確認は隔離検証（104項目）で行った。本番ではデータを書き込む確認はしない（bundleの行を作るとR1が使えなくなるため）

### 5.5 C3が途中で失敗したとき・R1

```sh
docker exec -i --user postgres "$C" psql -X -At -U nexus_studio_migrator -d nexus -c 'SELECT "MigrationId" FROM studio."__EFMigrationsHistory" ORDER BY 1'
```

出力を1.2節の14本と照らし、どこまで入ったか・最初に失敗した移行・エラーの文（秘密を含まない部分）をIssueに記録する。そこで止め、次のどちらにするかを利用者が決める。

- 原因を直し、同じ`studio-migrate.sql`（SHA-256同じ）を再実行して残りを入れる。その後C4〜C6
- R1（別承認）で取り除き、C1からやり直す。R1のSQL（NexusStudio README §4。superuserで1回だけ。`delivery`に行が無いことをC6の`delivery_rows`で確かめてから）:

  ```sql
  BEGIN;
  DROP SCHEMA IF EXISTS delivery CASCADE;
  DROP SCHEMA studio CASCADE;
  REVOKE ALL ON DATABASE nexus FROM nexus_studio_migrator, nexus_studio_app;
  DROP ROLE nexus_studio_app;
  DROP ROLE nexus_studio_migrator;
  COMMIT;
  ```

  影響: `studio`・`delivery`とその中身、Studio role 2つが消える。`public`の表、`nexus_public`、baselineは残る（3.3節で確認）。public-apiは`delivery`が無いと`/api/v2/bundle`で配信できないだけ

## 6. Issueへの記録様式（値は書かない）

```md
#8 本番適用 YYYY-MM-DD 実行者: <名前>  承認: A <日付/承認者>、B <…>、C <…>
- 入力: NexusInfra a98c838 / NexusMobile 6f1b079 / NexusStudio 877ee90、SHA256SUMS 9件 OK/NG
- P1 image・版: 合/否（digest: postgres@sha256:…）
- P2 識別子・設定: 合/否（値は私有メモ）
- P3 既存role・schema・表なし: 合/否、差異: なし/あり
- P4 接続元・参加コンテナ: 合/否、差異: なし/あり（詳細は私有メモ）
- P5 認証設定: 合/否
- P6 容量・置き場所: 合/否
- A バックアップ: dump 合/否、VPS外コピー 合/否（時刻・サイズ・SHA-256は私有メモ）、別volume復元 合/否
- B1 / B2 / B3: 各 合/否、B4: 合/否
- C1 / C2 / C3 / C4 / C5: 各 合/否、C6: 合/否
- C3の履歴: 14/14（途中失敗なら 入った件数と最初に失敗した移行ID）
- 中断・R1・R2: なし/あり（理由）
- Studioのサービス: 未有効（#9で扱う）
```

隔離検証の成功を本番の結果として書かない。本番の結果は上の様式で、実行した段だけを書く。

## 7. 未確認・残り

- 本番PG17のdigest・マイナー版（P1）。隔離検証は17.11の`b0f9560a…`。違えば再検証
- 現行PG17の他の接続元・書き込み元（P4）。[pg17-baseline.md](pg17-baseline.md) §7の1つ目に答える材料
- `pg_hba.conf`の形（P5）。隔離検証のimageの既定と違えば、5.3節の接続を見直す
- R2（復元先volumeへの切り替え）の手順は未作成。B以降で失敗して復元を選ぶときに別に作る
- 初期管理者の追加（D）: Studio API imageの発行経路とdigest、compose定義が#9側で決まってから。利用者本人の1名で行う
- `grant-runtime.sql`の再実行（public-apiの稼働後）は`spots`・`events`を一時的に止める（3.1節）。将来のMobile移行の手順で扱う
