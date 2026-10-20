# Issue #7 隔離検証記録

2026-10-20 JST。**DB権限は成功。採用Privateイメージの取得・起動とCaddy経由GETは成功したが、正常なログPOSTが500となり採用digestは不合格。NexusMobile PR #99で修正し、レビュー・統合・再発行後の新digest検証が必要。Issue #7は未完了。本番未適用。**

## 対象と証跡

- [Issue #7](https://github.com/m0o9l0v3/NexusInfra/issues/7) の契約B。[正式な受け入れ条件](pg17-baseline.md#4-issue-7-の受け入れ条件正式契約b)は変更しない。
- NexusMobileソース: `528f89fdf0318e5111d067d1e8760704d0ea2d89`。`git archive`で取り出したソースから移行SQLを生成した。実DB・dump・本番secretは使っていない。
- 採用候補API: `ghcr.io/m0o9l0v3/nexus-public-api-pg17-private@sha256:2f8e1b9e074a268769147ddf6551a6e4edbae5bd21f20f42ddb06894314d797f`。
- [発行ジョブ・attempt 2](https://github.com/m0o9l0v3/NexusMobile/actions/runs/39204302096/job/117905253595)は成功。発行前後のPrivate検査と証跡保存も成功。[ダウンロードした発行証跡](evidence/issue-7/2026-10-20/public-api-publication.json)に `linux/amd64`・`private`・ソースcommitが記録されている。これは発行時点の証跡。今回、人間の認証設定後に同じdigestのpullが成功し、imageのOCI revisionも上記ソースSHAと一致した。現在のpackage可視性の再確認は行っていない。
- 使用PG17: `postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`、実行時 `PostgreSQL 17.11`。ローカルengineは `linux/arm64`。本番VPSのイメージdigestを再現したという意味ではない。
- Caddy候補: `caddy@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0`、`linux/amd64`、`v2.11.4`。採用APIとの内部TLS・代表GET・network分離を確認した。
- [baseline manifest](evidence/issue-7/2026-10-20/baseline-manifest.json): 移行SQL・role SQL・grant SQLのSHA256。archiveには`.git`がないため、manifestのcommit欄はarchive元の上記SHAを明示して補った。元のbaseline実行出力のcommit欄は空だった。

## DB権限: 成功

ローカルの検証専用engine `colima-nexus-db-verify` に、新規project `nexus-pg17-baseline-issue7-20261020c` と新規volume `nexus-pg17-baseline-issue7-20261020c-db` を作成した。起動前にlive container `nexus-postgres-1` とvolume `nexus_postgres_data` がないことを確認した。他の検証用container/volumeは使用・変更していない。

`scripts/pg17-baseline.py run` の[47項目が成功](evidence/issue-7/2026-10-20/baseline-checks.txt)。さらに[17項目が成功](evidence/issue-7/2026-10-20/additional-db-checks.txt)。

| 条件 | 結果・確認方法 |
| --- | --- |
| readiness相当の権限判定、必要3表、移行履歴2本 | 成功。SQL述語による検査。採用イメージはProductionでこのroleのreadinessを通り起動した |
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

## 採用イメージの検証結果

採用候補digestの匿名pullは`unauthorized`。現在のGitHub CLI認証を一時Docker設定で利用するとloginは成功したが、対象digestのpullは`403 Forbidden`。packageメタデータのAPI照会も `You need at least read:packages scope to get a package.` で403だった。認証データは出力・Git保存していない。一時Docker認証設定は削除済み。

その後、人間のGHCR認証設定により同じdigestのpullが成功した。OCI revisionは採用ソースSHAと一致、実行UIDは1654、ダミーsecretはUID所有0400とした。新規の隔離Compose project・volume `nexus-pg17-edge-iso-issue7-20261020` / `nexus-pg17-edge-iso-issue7-20261020-db` でbaseline47項目を再実行し成功した。

- [起動時チェック](evidence/issue-7/2026-10-20/adopted-image-startup-checks.txt): raw/resolved/env validatorが成功。Production起動前後でpublic表名・移行履歴・fixture行数が不変。
- [HTTPチェック](evidence/issue-7/2026-10-20/adopted-image-http-checks.txt): Caddy内部TLSでhealth・spots・eventsが200、公開fixtureが各1件。Caddyはbackend非参加、edge参加者はCaddyとAPIのみ、host公開portは80/443。異なるpublic-network IPの2クライアントを作成した。
- 偽装XFPでHTTPS応答・HTTPの308リダイレクトが変わらず、未許可OriginにAllow-Originが付かないことをHTTPで確認。ブラウザによるCORS確認は未実施。
- **[正常ログPOSTが500](evidence/issue-7/2026-10-20/adopted-image-log-failure.txt)**。DTOのrecord引数に対応するRequired属性がpropertyに付いており、MVCの検証処理で例外となる。POST 202・永続化・hash連鎖は不合格。
- amd64互換実行ではrate-limit連続試験にtimeout/502が混ざり、特権role起動拒否も期待した例外で確定できなかった。これらは成功扱いにしない。public-api再起動後の再接続も、新digestで再検証する。

## 修正と再検証の条件

[NexusMobile PR #99](https://github.com/m0o9l0v3/NexusMobile/pull/99)、commit `a765aa9` は、2個のRequired属性をconstructor parameterに指定する修正と、単件・一括のHTTP回帰テスト6ケースを追加する。EF InMemoryを使って実MVC・実バックグラウンドワーカーを通し、202・永続化・hash連鎖、必須項目欠落時の400・未保存を確認する。これはPostgreSQL権限の証拠とは区別する。

.NET SDK 8.0.425 / arm64コンテナで、修正前は追加6ケースが500応答により失敗し、修正後はpublic-api全27ケースが成功した。Macの.NET 10ロールフォワード実行は、TestHostのPipeWriter互換性エラーがあるためテスト合否の根拠に使わない。

PR #99のレビュー・統合とPrivateイメージ再発行後、発行証跡の新commit・digestを固定し、[隔離ハーネスの手順](pg17-public-edge.md#12-隔離検証issue-7実digest入手後)で全受け入れ条件を再検証する。現在の不合格digestの結果を、ソース修正だけで合格へ変更しない。採用イメージはlinux/amd64、今回のMac engineはlinux/arm64なので、互換実行とVPSでのネイティブ実行も区別する。

### ローカル修正版のPG17接続確認（採用digestの代替にはしない）

commit `a765aa9` を `git archive` で取り出し、既存のDockerfileでlinux/arm64イメージを構築した。image IDは `sha256:ce69166c67dec0061390b8454ba11588b32e17882d2f09b33fd3881a9c32ef28`。レジストリへ発行していないため、これは採用可能なGHCR digestではない。

Caddyも同じv2.11.4のARM版 `caddy@sha256:0c994536bddb66445885237f1a5dcc1916bccea922661c76b4e9fc24061f9b52` を使用した。amd64版の結果と混同しない。

- [ローカル修正版のHTTP検査12項目](evidence/issue-7/2026-10-20/local-repair-http-checks.txt)が成功。Caddy経由POSTが2回とも202、PG17に2行増加し、同じ日次chainで2行目のprev_hashが1行目のhashと一致した（HMAC-SHA256、hash長64）。
- 既定の60回/分の枠を、正常2回と不正入力58回で使い切り、以降429。偽装XFFを3通り変えても429のまま、別IPのクライアントは400となり独立した枠を持つ。クライアントはpublic側だけに接続した。
- GET・XFP・HTTPでのCORS応答・network分離・再起動後の再接続も成功。
- [起動拒否など3項目](evidence/issue-7/2026-10-20/local-repair-startup-rejection.txt)が成功。ダミーDB内だけで一時的にnexus_publicをSUPERUSERにし、実readinessの `Runtime database role has deployment privileges.` で終了することを確認し、必ずNOSUPERUSERへ戻した。別のnexus接続文字列はreadinessより前の専用login検査で拒否された。

ブラウザによるCORS検査と、新しいPrivate採用digestによる全項目の検証は残る。Issue #7のチェックを完了へ変更していない。

検証終了後は今回のクライアント・SDK補助コンテナを削除し、edge、隔離DBの順でprojectを指定してdownした。両projectのコンテナが残っていないことを再確認した。down -vは使わず、volumeは残している。
