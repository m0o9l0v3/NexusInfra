# 2026-11-15 本番導入の記録（公開サイト・公開API・PG17）

状態: **本番適用済み（2026-11-15、JST）**。作業はオーナーがClaudeセッションの外（ChatGPTの補助）で行った。元の作業記録にはVPSのIPアドレスや私的なリンクが含まれるため、この文書には**公開してよい範囲だけ**を載せる。事実関係はNexusInfra #6・#8・#17、NexusMobile #131・#69のコメントと照合済み。

関連: [PG17公開レイヤー](../pg17-public-edge.md)・[PG17本番適用手順](../pg17-issue-8-production-apply.md)・[最初のbundle公開](../first-bundle-publish.md)

## 1. 結果の要約

既存VPSのPostgreSQL 17.11を維持したまま、Caddyで公開サイト（`www.nexus-app.jp`）と公開API（`api.nexus-app.jp`）のHTTPS配信を開始した。サーバー状況ページに「応答あり」が出ることを外部ブラウザで確認した（13:13ごろ）。配信bundleは**未公開**で、`/api/v2/bundle`は仕様どおり404を返す。

## 2. タイムライン

| 順 | 作業 |
| --- | --- |
| 1 | `www.nexus-app.jp`のAレコードを、既存の`api`と同じVPSに向けて追加（TTL 300秒）。ネームサーバーは変更していない。名前解決の反映後にHTTPSで到達を確認 |
| 2 | NexusMobile `f42d9da`のサイトを`VITE_API_BASE_URL=https://api.nexus-app.jp`で型チェック・本番ビルドし、4ページ分のHTMLを`/srv/nexus-web/releases/f42d9da`に配置。`/srv/nexus-web/current`を相対シンボリックリンクにした。Caddyは親の`/srv/nexus-web`を読み取り専用でマウント。証明書取得用のComposeにサイト設定を足して先行公開 |
| 3 | 公開APIイメージを発行（GitHub Actionsの環境承認後、Private GHCR、`linux/amd64`）。VPSでdigest指定で取得し、プラットフォーム・実行UID・OCI revisionの一致を確認 |
| 4 | 承認A（バックアップと隔離復元）、承認B（公開API向け初期構築）、承認C（Studio・配信スキーマ）を適用 |
| 5 | 公開ポートを持たないAPIを先に起動し、内部で`/health`とCORSの許可・拒否を確認。続いて既存の証明書ボリュームを引き継いだままCaddyを「サイト＋API」構成へ切り替え |
| 6 | 外部ブラウザから公開URLを確認（13:13ごろ） |

## 3. 採用した成果物

| 項目 | 採用値 |
| --- | --- |
| 公開APIイメージ | `ghcr.io/m0o9l0v3/nexus-public-api-pg17-private@sha256:1e1879ba46045ad450b68544af95f8bcc4138c0d30262c35856a5d8e6199f4db` |
| PostgreSQLイメージ（17.11） | `postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24` |
| Caddyイメージ | `caddy@sha256:685ae3d7c34d9b5f8caeddc5d14051db7bbd88ff1f5a2fa0c16bd8ca328fb7af` |
| PG17公開レイヤー | Composeプロジェクト`nexus-edge-pg17`、配置先`/srv/nexus-edge-pg17` |
| 静的サイト | NexusMobile `f42d9da`、配信ルート`/srv/nexus-web/current` |
| Studio移行SQL | SHA-256 `c6df200b5aa7e3338bc7c558c88ca51d24230747caf59e83ec8f058d2a037533`（NexusStudio CI run 43469460316）。適用後の最終移行IDは`20261112071332_RemoveCampusGroundEntityKind` |

### 使ったコミット

- 公開APIイメージは、発行ラン（NexusMobile Actions run 43669099689）が**`f42d9da`**をビルドした結果である。`f42d9da`にはCORS修正（NexusMobile #175）と監査ログ修正（#198）を含む`c391095`が含まれる。作業メモには「`c391095`からビルド」とあったが、正しくは「`f42d9da`（`c391095`を含む）」。
- 静的サイトも同じ`f42d9da`。
- 構成（Compose・Caddy）はNexusInfra PR #26、構成テスト95件とCI run 43663924349が成功。

## 4. 承認A・B・C

| 承認 | 内容 | 結果 |
| --- | --- | --- |
| A | globals・cluster・アプリDBのダンプを取得。VPS上と、暗号化して転送した手元側のコピーの両方でSHA-256を照合。別の隔離PG17.11（同じdigest）に復元し、構成が期待どおりであることを確認 | `BACKUP_OK`・`MAC_COPY_OK`・`RESTORE_OK` |
| B | `nexus_owner`・`nexus_admin_app`・`nexus_public`を作成。DBを`nexus_owner`所有にし、`PUBLIC`の既定権限を剥奪。移行履歴2件、表14個（履歴表を含む）、RLS有効2表。公開API用ロールは非特権 | `B_OK` |
| C | `nexus_studio_migrator`・`nexus_studio_app`と`studio`・`delivery`スキーマを作成。Studio移行履歴16件、配信表`participant_bundles`・`current_bundle`を確認。`nexus_public`は配信2表を読め、`studio`へのアクセスは拒否される。配信レコードは0件 | `C_OK` |

本番事前確認: PostgreSQL 17.11は`healthy`、認証はSCRAM、アプリ用オブジェクトと他クライアントの接続は0件、空き容量は十分。手順書作成時よりStudio移行が2件増えていたため、最新CIのSQLを採用した（既存14件のSQLに差分なし）。

## 5. 発生した問題と対応

| 事象 | 対応 |
| --- | --- |
| 初回サイト配信用Composeが、必須の環境変数2つ（ACME用メールアドレスとAPIドメイン）の不足で停止 | スクリプトを修正し、稼働中のCaddyから既存の設定値と採用イメージ参照を引き継ぐようにした |
| VPSでのGHCR取得が`denied` | レジストリ認証をやり直し、固定digestの取得を確認。**フォローアップ**: GHCRの取得認証はclassic PATのみが対応する（[pg17-operator-checklist.md](../pg17-operator-checklist.md) D2）。fine-grainedトークンには置き換えず、読み取り専用（`read:packages`のみ）で有効期限を付けたclassic PATへ入れ替える（§7） |
| 手元の復元中に`docker compose`が`unknown shorthand flag: 'f'`を返した | `docker-compose`を直接呼び出し、専用のランタイムに固定して復元を完了 |
| Studio移行件数が手順書（14件）と異なった | 最新の16件版へ差し替え、SQLのSHA-256と同じPG17イメージでのCIを照合 |

切り替え後の検証が失敗した場合に、旧Caddy構成を再起動し追加したAPIを停止する手順を配備スクリプトに組み込み、模擬環境で確認した（本番では使っていない）。

## 6. 検証結果

| 対象 | 結果 |
| --- | --- |
| Caddy | `Healthy` |
| 公開サイト | 4ページ（トップ・開発状況・サーバー状況・技術説明）がHTTPSで応答（`WEBSITE_OK=4`） |
| `GET /health` | HTTP 200（`API_HEALTH=200`）。サーバー状況ページで「応答あり」 |
| CORS | `https://www.nexus-app.jp`からの`GET /health`にだけ許可ヘッダー。別オリジンからの`/health`、公開サイトからのbundle取得には付かない（`CORS_OK`） |
| `GET /api/v2/bundle` | 404、`urn:nexus:problem:bundle-not-published`（未公開時の仕様） |
| リダイレクト（2026-11-16にハブが追加確認） | `/development` → 308で`/development/`、`/?code=TEST` → 200、http → https 308 |

## 7. 未了・フォローアップ

- [x] **最初のbundle公開**: 手順は[first-bundle-publish.md](../first-bundle-publish.md)（#27）。2026-11-16 12:16 JST に Release #1（版 1）を公開し、`/api/v2/bundle`の200を確認した（同文書 §8、#17）。
- [ ] **Studioの本番公開**: #9。
- [ ] **監視をHTTP 200の判定へ切り替える**。
- [x] **iOS実機からの取得確認**（bundle公開後）。2026-11-16、Debugビルドで版 1 を取得・保存し、機内モードでも表示した（[first-bundle-publish.md](../first-bundle-publish.md) §8）。
- [ ] **スマホSafariでのライト・ダーク表示の確認**。
- [x] **PATの入れ替え**: VPSのGHCR取得用資格を、読み取り専用（`read:packages`のみ）で有効期限を付けたclassic PATへ入れ替える（GHCRはclassic PATのみ対応のため、fine-grainedにはしない）。2026-11-16 にオーナーが確認：VPSは`read:packages`のみのclassic PAT（期限 2027-11-15）を使っており、それより広い権限のトークンは残っていない。

## 8. この文書に載せないもの

VPSのIPアドレス、DNS・レジストラのアカウント情報、認証情報、ChatGPTの私的なリンク、手元の作業ディレクトリは意図的に省いた。必要なときはオーナーが保管する元記録を参照する。
