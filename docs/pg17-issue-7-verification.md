# Issue #7 隔離検証記録

2026-10-20 JST。**DB権限の隔離検証は成功。採用Privateイメージの取得は403で停止し、実API/Caddyの受け入れ条件は未確認。Issue #7は未完了。本番未適用。**

## 対象と証跡

- [Issue #7](https://github.com/m0o9l0v3/NexusInfra/issues/7) の契約B。[正式な受け入れ条件](pg17-baseline.md#4-issue-7-の受け入れ条件正式契約b)は変更しない。
- NexusMobileソース: `528f89fdf0318e5111d067d1e8760704d0ea2d89`。`git archive`で取り出したソースから移行SQLを生成した。実DB・dump・本番secretは使っていない。
- 採用候補API: `ghcr.io/m0o9l0v3/nexus-public-api-pg17-private@sha256:2f8e1b9e074a268769147ddf6551a6e4edbae5bd21f20f42ddb06894314d797f`。
- [発行ジョブ・attempt 2](https://github.com/m0o9l0v3/NexusMobile/actions/runs/39204302096/job/117905253595)は成功。発行前後のPrivate検査と証跡保存も成功。[ダウンロードした発行証跡](evidence/issue-7/2026-10-20/public-api-publication.json)に `linux/amd64`・`private`・ソースcommitが記録されている。これは発行時点の証跡であり、今回の取得成功や現在のpackage設定の確認ではない。
- 使用PG17: `postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`、実行時 `PostgreSQL 17.11`。ローカルengineは `linux/arm64`。本番VPSのイメージdigestを再現したという意味ではない。
- Caddy候補: `caddy@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0`、`linux/amd64`、`v2.11.4`。version確認のみで、APIとの接続検証は未実施。
- [baseline manifest](evidence/issue-7/2026-10-20/baseline-manifest.json): 移行SQL・role SQL・grant SQLのSHA256。archiveには`.git`がないため、manifestのcommit欄はarchive元の上記SHAを明示して補った。元のbaseline実行出力のcommit欄は空だった。

## DB権限: 成功

ローカルの検証専用engine `colima-nexus-db-verify` に、新規project `nexus-pg17-baseline-issue7-20261020c` と新規volume `nexus-pg17-baseline-issue7-20261020c-db` を作成した。起動前にlive container `nexus-postgres-1` とvolume `nexus_postgres_data` がないことを確認した。他の検証用container/volumeは使用・変更していない。

`scripts/pg17-baseline.py run` の[47項目が成功](evidence/issue-7/2026-10-20/baseline-checks.txt)。さらに[17項目が成功](evidence/issue-7/2026-10-20/additional-db-checks.txt)。

| 条件 | 結果・確認方法 |
| --- | --- |
| readiness相当の権限判定、必要3表、移行履歴2本 | 成功。SQL述語による検査。採用.NETイメージのreadiness実行は未確認 |
| `spots` / `events` の公開行だけSELECT | 成功。公開・非公開を各1行投入し、それぞれ1行だけ見える |
| 管理表・将来追加表SELECT拒否 | 成功。管理用10表と所有者が追加した表でSQLSTATE `42501` |
| `visit_logs` INSERTと限定3列SELECT | 成功。全14列を個別検査し、`chain_id, created_at, hash` だけ成功。残り11列は `42501` |
| `visit_logs` UPDATE / DELETE / TRUNCATE拒否 | 成功、`42501` |
| `spots` / `events` INSERT / UPDATE / DELETE拒否 | 成功、`42501` |
| 移行履歴INSERT / UPDATE / DELETE / TRUNCATE拒否 | 成功、`42501` |
| CREATE / ALTER / DROP、CREATE ROLE、所有者へのSET ROLE、COPY PROGRAM拒否 | 成功、`42501` |
| bootstrap用superuserのruntime不適合 | SQL述語で確認。実イメージでの起動拒否は未確認 |

追加検査は、同じ使い捨てvolumeのDBを再起動し、`information_schema.columns`から得た`visit_logs`の各列について `nexus_public` で `SELECT "<列名>" FROM visit_logs LIMIT 1` を実行した。3列以外は終了コード非0かつ`42501`を要求した。さらに次の2文が同様に拒否されることを確認した。

```sql
UPDATE "__EFMigrationsHistory" SET "ProductVersion" = 'forbidden';
TRUNCATE "__EFMigrationsHistory";
```

最後の追加項目はDB再起動後の新規psql接続であり、**public-api再起動後の再接続の証拠ではない**。API経由のログ永続化・hash計算もDB単独の成功からは判定しない。

## 再現上の注意

移行SQL生成は `.NET SDK 10.0.400`、`DOTNET_ROLL_FORWARD=Major` で実施。脆弱性情報キャッシュへの書込み権限に関する `NU1900` と既存の `CS0436` 警告があったが、生成は終了コード0で完了し、manifest検査が通った。採用APIのビルドは別途、上記発行ジョブの.NET 8で実施されている。

このMacには`docker compose`プラグインがなく、`docker-compose`が存在する。baselineのfallbackは`DOCKER`内のcontext指定を引き継がないため、今回は両方を同一のローカルsocketへ明示した。

```sh
export DOCKER='docker --context colima-nexus-db-verify'
export DOCKER_HOST=unix:///Users/m09/.colima/nexus-db-verify/docker.sock
```

secretのbind元はVMから参照できるGit無視対象の`secrets/`配下とした。最初の2回はComposeの接続先・VMから見えないbind元の問題でDB起動前に失敗した。それぞれ別名の新規volumeを確保し、使い回していない。3回目で上記64項目が成功した。

各停止はproject指定の`down --remove-orphans`。`down -v`は使っていない。volumeは残しており、この作業で削除していない。既存VPS・本番PG17・PG18構成には操作していない。

## 実API/Caddy: 認証設定待ち

採用候補digestの匿名pullは`unauthorized`。現在のGitHub CLI認証を一時Docker設定で利用するとloginは成功したが、対象digestのpullは`403 Forbidden`。packageメタデータのAPI照会も `You need at least read:packages scope to get a package.` で403だった。認証データは出力・Git保存していない。一時Docker認証設定は削除済み。

人間がGHCR用の`read:packages`認証を設定したら、同じdigestのpullから再開する。取得後に確認する条件:

1. OCI revision・実行UID・platformの一致、UID所有0400のダミーsecret読み取り。
2. 起動時migration/seedなしと非特権roleでのreadiness成功、特権roleでの起動拒否。
3. `api.localhost`のCaddy内部CA経由でhealth・代表GETが200、ログPOSTが202、DB増分とhash連鎖。
4. 異なる接続元IPの2クライアントで独立した制限枠、偽装XFF/XFPによる回避不可。
5. 未許可originのCORS応答をブラウザで確認。
6. public-api再起動後の再接続、Caddyのnetwork分離・host port 80/443のみ、project指定での停止。

実行手順は[隔離ハーネスの手順](pg17-public-edge.md#12-隔離検証issue-7実digest入手後)を使用する。候補APIは`linux/amd64`、Mac engineは`linux/arm64`なので、互換実行での成功とVPSでのネイティブ実行は区別して記録する。
