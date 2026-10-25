# Issue #17 I4: public-api Private再発行の準備（NexusMobile `63a3970`）

状態: **発行済み（2026-10-25、人間の承認後）。I4は同日に隔離環境で実施（5節）。本番未適用・VPS操作なし。**

発行結果: `ghcr.io/m0o9l0v3/nexus-public-api-pg17-private@sha256:9fa4f1fe42398e0badbdaa1cca28242b9e64889307350631d4984d87405b1600`（source `63a3970`、`linux/amd64`、Private、[run 40176478031](https://github.com/m0o9l0v3/NexusMobile/actions/runs/40176478031)）。[発行証跡](evidence/issue-17/2026-10-25/public-api-publication.json)・[照合結果](evidence/issue-17/2026-10-25/public-api-publication-checks.json)。workflowは起動時の`develop`先頭`6f1b079`で動いた（`63a3970`からの差分はdocsの1コミットで、発行workflowと`deploy/public-api-image.py`は不変）。OCI revision・architecture・実行UIDはI4でpullしたときに照合する。

I4（[pg17-baseline.md](pg17-baseline.md) 4.1節のedge確認）には`GET /api/v2/bundle`（NexusMobile #121）を含むpublic-apiの採用digestが必要。現在の採用digest（`sha256:847b6d0f…`、source `d3e20b1`、[Issue #7の記録](pg17-issue-7-digest-verification.md)）は#121を含まない。

## 1. レビュー対象

| 項目 | 値 |
| --- | --- |
| reviewed commit | NexusMobile `develop` `63a3970da019ee2bc67ff8f5c9b784e572ee4325`（PR #133のmerge） |
| 比較元 | 現採用 `d3e20b13847ff973783670fe9bb4d133cc168a5b` |
| `#121`（`4de2e6a`）を含むか | 含む（`git merge-base --is-ancestor`で確認） |
| `63a3970`のCI | `ci`（push）success |
| target platform | `linux/amd64`（前回と同じVPS確認済みの値） |
| 発行先 | `ghcr.io/m0o9l0v3/nexus-public-api-pg17-private`（Private、tagはcommit SHA） |

## 2. 変更点の確認（`d3e20b1..63a3970`、イメージに入る範囲）

`apps/public-api`・`apps/admin-api`・`deploy`・発行workflowの差分は13ファイル（+360/−771）。

| 区分 | 変更 | 判定 |
| --- | --- | --- |
| 追加（#121） | `BundleController`（`GET /api/v2/bundle`）、`Services/Delivery/*`（`delivery`のSELECTだけ。書き込みなし）、`appsettings.json`の`ParticipantBundle`（`RecheckAfterSeconds` 300、`UnavailableRetryAfterSeconds` 30。既定値あり、必須の環境変数は増えない） | I4の対象。DB権限は契約B 3.1節の範囲（Issue #17で隔離確認済み） |
| 撤去（#130、#21） | `NavigationController`・`NavigationDtos`・`NavigationDataStore`・`Data/phase1-navigation.json`（`/api/navigation/spots[/{id}]`・`/api/floors`・`/api/routes`）。2026-10-25の利用者決定による撤去 | Infraは撤去されたパスを参照していない（Caddyfile・検証スクリプト・Issue #7の代表GETは`/health`・`/api/spots/public`・`/api/events/today`）。影響なし |
| 撤去（#102） | admin-apiの`MapDatasetDraftsController`・DTO。public-apiのイメージにはadmin-apiのassemblyが入るが、public-apiはcontrollerを自assemblyに限定している | 影響なし |
| 注記のみ | admin-api `Program.cs`にコメント2行 | 影響なし |
| 不変 | `apps/public-api/Dockerfile`、`*.csproj`、`.github/workflows/public-api-image.yml`、`deploy/public-api-image.py` | 発行手順・ビルド条件は前回と同じ |

起動時の振る舞い（readinessのみ、migration・seedなし）を変える差分は無い。`DatabaseReadiness`は`delivery`を検査しないため、`delivery`が無いDBでもpublic-apiは起動し、`/api/v2/bundle`だけが503（`Retry-After`付き）になる。workflowの入力検査（`deploy/public-api-image.py inputs --commit 63a3970… --platform linux/amd64`）はローカルで合格した。

## 3. 発行手順（承認後に人間が実行）

1. 上の表と差分を確認し、`63a3970`を採用対象として承認する。
2. NexusMobileの`develop`上のworkflowを手動起動する。`public-api-image` environmentの承認者が承認するまでpublish jobは止まる。

   ```sh
   gh workflow run public-api-image.yml -R m0o9l0v3/NexusMobile --ref develop \
     -f reviewed_commit=63a3970da019ee2bc67ff8f5c9b784e572ee4325 -f target_platform=linux/amd64
   ```

   workflowが行うこと: commitが`develop`/`main`から到達可能かとrunnerのplatformを確認する → packageがPrivateであることを確認する → .NET 8で`AdminApi.Tests`・`PublicApi.Tests`を実行する → `apps/public-api/Dockerfile`だけをビルド・push（provenance `mode=min`、SBOM付き）→ push後に再びPrivateであることを確認する → digestのreceiptをartifact（90日）に保存する。
3. 停止条件: テストの失敗、Privateでない、platformの不一致、receiptが作られない。どれかに当たったらdigestを採用しない。
4. 成功後、Infraに記録する（Issue #7と同じ形）: `docs/evidence/issue-17/<日付>/public-api-publication.json`（source commit、image@digest、platform、visibility、run/job URL、provenance）。receipt artifactを取得して照合する。

## 4. I4の実施手順（発行後）

[Issue #7の隔離構成](pg17-issue-7-digest-verification.md)（`compose.pg17-edge-isolation.yml`、`api.localhost`、内部CA）を使い、APIを新digestに、Caddyを採用済みv2.11.4のdigestにする。DBは新規volumeの使い捨てPG17で、`pg17-baseline.py`と同じ順に`01`→移行SQL→`grant-runtime.sql`→（仮のStudio role）→`02`→検証用DDL→`03`を適用する。最後に、Studio実行時roleで`participant_bundles`へダミーbundleを1件入れ、`current_bundle`をそれに向ける。

- ダミーpayloadは512バイト以上のUTF-8 JSONにする（Caddyの`encode`は既定で512バイト未満を圧縮しないため、短いと接尾辞付きETagを確かめられない）。`sha256`・`byte_length`はpayloadから計算する（不一致だとpublic-apiは500を返す）。
- 確認項目は4.1節の5つ。`Accept-Encoding: gzip`と`zstd`でそれぞれ確かめ、`identity`（圧縮なし）で200と接尾辞の無いETagを確かめる。
- 304が成立しなければ、NexusMobile #23に`HEAD /api/v2/bundle`の追加を求める（4.1節）。
- 停止は`-p`明示・`down -v`不使用。volumeは確認後に個別削除する。

## 5. I4の結果（2026-10-25）

隔離engine（colima `nexus-db-verify`、linux/arm64。APIとCaddyはamd64の互換実行）で、4節の手順どおりに行った。VPSでのネイティブ実行を証明するものではない。

| 入力 | 値 |
| --- | --- |
| API | `ghcr.io/m0o9l0v3/nexus-public-api-pg17-private@sha256:9fa4f1fe42398e0badbdaa1cca28242b9e64889307350631d4984d87405b1600`（pull後のOCI revision `63a3970`、`linux/amd64`、UID 1654） |
| Caddy | `caddy@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0`（`caddy version` v2.11.4）、`Caddyfile.pg17-api`（`encode zstd gzip`） |
| PostgreSQL | `postgres@sha256:b0f9560a…`、隔離project `nexus-pg17-edge-iso-i17-i4-20261025`・新規volume |
| DB | `pg17-baseline.py`と同じ検証99項目（契約Bとdelivery）の後、Studio実行時role（仮名）でbundle 20（2,719バイト）・21（35,946バイト）・22（2,016バイト）を順に公開 |

結果（[HTTP](evidence/issue-17/2026-10-25/i4/http-checks.txt)、[DB](evidence/issue-17/2026-10-25/i4/db-and-bundles.txt)、[イメージ・secret・network](evidence/issue-17/2026-10-25/i4/image-and-secrets.json)、[停止](evidence/issue-17/2026-10-25/i4/teardown.txt)）:

- bundle 21・22のそれぞれで25項目すべて成功。接尾辞付きETag、`If-None-Match`での304、`Nexus-*`の保持、`Accept-Encoding`を変えたときの200、古いETagでの200、gzip展開後の本体とSHA-256の一致、圧縮なしでの本体・長さの一致を確かめた。**304が成立したので`HEAD /api/v2/bundle`は不要。**
- `Content-Length`: E2-5 §4.1.1（ソースの読解）は「削除する」としていたが、実地では**圧縮後の本体が小さいときに圧縮後の大きさの`Content-Length`が付いた**（bundle 22でgzip 297・zstd 263、HTTP/2とHTTP/1.1の両方）。bundle 21では付かなかった（HTTP/1.1はchunked）。Caddyは上流の値を消し、Goのサーバーが、圧縮後の本体を書き終えた時点でバッファに収まっていれば自分で付けている、と推定される（原因はソースで未確認）。値は圧縮後の大きさと一致し、非圧縮の大きさ（`Nexus-Bundle-Length`）と取り違える値にはならない。iOSが`Content-Length`を使わない限り影響はない（E2-5 §6 規則8）。
- zstdの本体は、手元のcurlがzstdを展開できないため、ヘッダーだけ確かめた（本体の照合はgzipと圧縮なしで行った）。
- 境界: APIの実行UID 1654、secret 2つは1654所有の0400。Caddyはbackendに参加せず、edgeの参加者はCaddyとAPIだけ。hostの公開portは80/443だけで、再起動0回。停止はedge→DBの順に`-p`を明示し、`down -v`・pruneは使っていない。volumeは、人間に名前を示して了承を得た後、同日18:16に1個ずつ削除した（下記）。
- 手順の途中で、`validate-pg17-edge.py --resolved`が日本語を含むrepoのパスで誤って拒否した（macOSがパスをNFDで返し、ComposeがNFCで出力するため）。同じファイルかを`os.path.samefile`で比べるよう直した（テストを追加）。

## 6. 後片付け（2026-10-25）

[削除記録](evidence/issue-17/2026-10-25/volume-removal.txt)のとおり、Issue #17で作った隔離volume 3個を、使用コンテナが0であることを確かめてから`docker volume rm`で1個ずつ削除した。私有secretのディレクトリ3つも削除し、検証VM（colima `nexus-db-verify`）は停止状態へ戻した。prune・`down -v`は使っていない。

- `nexus-pg17-baseline-i17-20261025-db`（初回の隔離検証、97項目）
- `nexus-pg17-baseline-i17-20261025b-db`（トリガー照合の修正後の再実行、98項目）
- `nexus-pg17-edge-iso-i17-i4-20261025-db`（I4）

edge projectのCaddy用volume（`nexus-edge-pg17_caddy_*`、検証用の内部CAだけ）と、別作業（2026-09-25のPG17/PG18検証、Studio）のvolumeは対象外として残した。
