# PG17公開レイヤー: 未決の判断3件（AI相談用）

この文書は、Codex・ChatGPTなど別のAIと3件の判断を相談するための**自己完結した資料**である。この文書だけで判断できるように、事実・制約・選択肢・決定の記録欄をまとめた。決まったら各判断の「決定」欄を埋め、「決定後に更新する箇所」を直す。

```yaml
document: pg17-edge-decisions
status: open            # 3件とも決まったら decided にする
created: 2026-10-18
repositories:
  infra: m0o9l0v3/NexusInfra (main 1f55578)
  mobile: m0o9l0v3/NexusMobile (develop 6459abd; API/DB code unchanged since 87446aa)
related_issues: [NexusInfra#5, NexusInfra#6, NexusInfra#7, NexusMobile#81]
decisions:
  - id: DEC-1
    topic: public-apiのrate limitで、Caddy越しのクライアントIPを正しく扱う
    status: open
    blocks: [NexusInfra#6 T1, NexusInfra#7]
  - id: DEC-2
    topic: public-apiのimmutable digestをどこで発行し、VPSがどう取得するか
    status: open
    blocks: [NexusInfra#5 §4, NexusInfra#7]
  - id: DEC-3
    topic: public-apiのCORS許可origin
    status: open
    blocks: [NexusInfra#6 T1]
```

## 0. 背景（全判断に共通）

- 稼働中のPostgreSQL 17（VPSの`/srv/nexus`、volume `nexus_postgres_data`）には触れずに、`api.nexus-app.jp`を公開する独立Compose project `nexus-edge-pg17`を作る（Issue #6）。
- 構成は Internet → Caddy（80/443のみhost公開）→ `public-api:8080` → PG17。Caddyとpublic-apiは`internal: true`のedge network（private IPv4の/24〜/28、env `PG17_EDGE_SUBNET`）でつながる。Caddyはbackend networkに入らない。
- public-apiは.NET 8で、ソースはNexusMobile（`apps/public-api`）。参加者向けアプリはiOSネイティブ（`apps/nexus-ios`）。`web/`はイベント告知LPに再構成中（NexusMobile #81）。
- **全判断に共通の制約（MUST）**
  - 本番未適用。決定は文書化とレビューを経てから適用する。
  - 秘密・実IP・実subnetをGitに入れない（ダミーは`172.30.251.0/28`、`*.example.invalid`）。
  - PG18用の`compose.yaml`、`Caddyfile`、`scripts/validate-release.py`などは変更しない（Issue #6の変更禁止リスト）。
  - image参照は`@sha256:`のdigestで固定する。タグ参照で本番を動かさない。

## DEC-1: Caddy越しのクライアントIPとrate limit

### 問い

`nexus-edge-pg17`のpublic-apiに、どのproxyを信頼させるか。`RateLimiting__KnownNetworks__0`にedge subnetを渡すか、`KnownProxies`にCaddyの固定IPを渡すか、ほかの方法をとるか。

### 事実（出典つき）

- rate limitはクライアントIP単位のFixed Window。キーは`context.Connection.RemoteIpAddress`（Mobile `apps/admin-api/RateLimiting/RateLimitingExtensions.cs:67`、public-apiと共通）。
- 対象は`POST /api/logs` 60回/60秒、`POST /api/logs/batch` 12回/60秒（`apps/admin-api/Options/RateLimitOptions.cs:27-28`）。GETと`/health`には制限なし。
- `X-Forwarded-For`と`X-Forwarded-Proto`を処理し、`ForwardLimit = 1`（同ファイル:33）。`KnownProxies`と`KnownNetworks`が両方空なら、ASP.NET Coreの既定（loopbackだけを信頼）のまま（:36）。public-apiは`app.UseForwardedHeaders()`を呼ぶ（`apps/public-api/Program.cs:83`）。
- そのため、何も設定しないとCaddyからの`X-Forwarded-For`は無視される。`RemoteIpAddress`は常にCaddyのedge IPになり、**全クライアントが1つのrate limit枠を共有する**（誰か1人が1分に60回ログを送ると、全員のログ送信が429になる）。
- NexusMobile自身の本番テンプレートは`RateLimiting__KnownNetworks__0: ${NEXUS_TRUSTED_PROXY_NETWORK:?...}`を必須にしている（`deploy/database/compose.production.yml:52`）。
- NexusInfraのPG18用`compose.yaml`（23〜41行のpublic-api）とIssue #6 T1の設計には、この環境変数が無い。
- Caddyの`reverse_proxy`は、上流へ`X-Forwarded-For`を付ける。`trusted_proxies`を設定しない限り、クライアントが送ってきた`X-Forwarded-For`は信頼せず、接続元IPで置き換える（Caddy v2.5以降の既定動作。採用するCaddy版で確認する）。

### 選択肢

| 案 | 内容 | 長所 | 短所・リスク |
| --- | --- | --- | --- |
| A | `RateLimiting__KnownNetworks__0: ${PG17_EDGE_SUBNET}`を渡す | Mobileのテンプレートと同じ形。既存のenvを再利用できる。edgeは`internal`で、Caddyとpublic-apiしか参加しない | 将来edgeに別サービスを追加すると、そのサービスも信頼される |
| B | Caddyにedge上の固定IP（`ipv4_address`）を与え、`RateLimiting__KnownProxies__0`にそのIPを渡す | 信頼範囲が最小 | envとvalidatorの検査項目が増える。IPの重複・変更に気を付ける必要がある |
| C | 設定しない（現状） | 変更なし | 上記の全員共有問題がそのまま残る。**不可** |

### 判断に必要な追加確認

- edge networkにCaddyとpublic-api以外を参加させない方針を、Issue #6 T4のvalidator設計（`set(services) == {"public-api","caddy"}`）で担保されるか。担保されれば案Aのリスクは小さい。
- Studio（Issue #9）を同じCaddyに追加したとき、`studio-api`も同じedgeに入り、`ReverseProxy__TrustedNetwork`に同じsubnetを使う予定である点。

### 決定

```yaml
id: DEC-1
status: open        # decided にする
choice:             # A / B
decided_on:
decided_by:         # 人間の名前（AIの提案は根拠欄に書く）
rationale:
follow_up:          # 例: "#6 T1にenv追加、validator検査11に追加、#7で429の挙動を確認"
```

### 決定後に更新する箇所

- Issue #6 T1のpublic-api `environment`、T4のvalidator（キーの存在と値の一致を検査）、T5のテスト
- `docs/pg17-public-edge.md` §6の注記、`docs/pg17-baseline.md` §4のrate limit行
- PG18 `compose.yaml`にも同じ問題がある。変更禁止のため、別Issueで扱う

## DEC-2: public-api digestの発行経路とVPSの取得方法

### 問い

Issue #7（隔離検証）と本番で使うpublic-apiのimmutable digestを、どのCIとregistryで発行するか。VPSはどの資格で取得するか。

### 事実（出典つき）

- 現在digestを発行する仕組みは**GitLab CIだけ**。`.gitlab-ci.yml`の`public-api-image`（develop:174）が、Mobileのリポジトリ内のMR、または既定ブランチ（:168）で`deploy/build-image.sh`を実行する。kanikoでGitLab Registryの`$CI_REGISTRY_IMAGE/public-api:<commit SHA>`へpushし（:17）、`image-digests/public-api.txt`に`registry/path@sha256:...`を記録する（:25、成果物の保持は30日）。
- GitHub Actionsの`image-build-check`（`.github/workflows/ci.yml:91-95`）は`docker build`だけで、pushしない。
- 両repoとも**GitHubがPrimary、GitLabはDRミラー**という方針。NexusInfraのREADMEは、GitLabへの直接pushや手動のIssue編集を避けるよう求めている。
- イメージの基底は`mcr.microsoft.com/dotnet/aspnet:8.0`（タグ参照）で、実行ユーザーは`USER $APP_UID`（`apps/public-api/Dockerfile:20`、既定1654）。最終イメージのdigestが固定されれば再現性の問題はない。
- VPSのCPUアーキテクチャは未確認（`docs/pg17-operator-checklist.md` Part 0で確認、V4でdigestと照合）。kanikoの出力はrunnerと同じアーキテクチャの単一manifest。
- Caddyとpostgresの公式イメージは、Docker Hubのindex digestで固定する予定。

### 選択肢

| 案 | 内容 | 長所 | 短所・リスク |
| --- | --- | --- | --- |
| A | 既存のGitLab CIとGitLab Registryを使い続ける | 実装済み。digestを記録する成果物がある | 「GitLabはDR」という方針と衝突する。ミラーの同期遅れで、レビューしたcommitとbuildしたcommitがずれるおそれ。VPSにGitLabの`read_registry` tokenが必要 |
| B | NexusMobileのGitHub ActionsでGHCRへpushする（Mobileの別PR） | Primaryに一本化できる。`docker/build-push-action`のdigest出力とprovenance/attestationを使える。アーキテクチャを明示してbuildできる | Mobile側の実装とレビューが必要。privateパッケージの場合、VPSに`read:packages`の読み取り資格が必要（使えるtokenの種類は要確認） |
| C | Macでbuildしてregistryへpushする | すぐにできる | 誰がどのcommitでbuildしたかをCIが保証しない。**本番には不可**（#7の隔離検証の暫定用途に限るなら検討余地あり） |
| D | VPS上でソースからbuildする | registryが不要 | immutable digestのレビューという前提が崩れる。VPSの負荷も大きい。**不可** |

### 判断に必要な追加確認

- NexusMobileのGitLabミラーで、現在もパイプラインが動いているか（`develop` `6459abd`の`public-api-image`が成功しているか）。
- GHCRのパッケージをpublicにしてよいか（public-apiのイメージに秘密は含まれない設計: `deploy/README-production-images.md`）。publicなら、VPSのpull資格は不要になる。
- VPSのアーキテクチャ。arm64の場合、案A（kaniko）はrunnerの対応が必要。

### 決定

```yaml
id: DEC-2
status: open
choice:             # A / B（Cは#7隔離専用の暫定としてのみ）
registry:           # 例: ghcr.io/m0o9l0v3/nexus-public-api
visibility:         # public / private
vps_pull_auth:      # なし / 読み取り専用token（種類）
target_platform:    # linux/amd64 / linux/arm64（Part 0の結果）
decided_on:
decided_by:
rationale:
follow_up:          # 例: "NexusMobileにGHCR push workflowのPR、#5 §4・#7に経路を記録"
```

### 決定後に更新する箇所

- `docs/pg17-public-edge.md` §4の「registry経路」の注記、`docs/pg17-operator-checklist.md` D1・D2・V4
- 案Bの場合、NexusMobileの`deploy/README-production-images.md`と`.github/workflows/`（Mobile側のPR）
- Issue #7の記録項目（使ったdigestと発行したジョブのURL）

## DEC-3: CORS許可origin

### 問い

`Cors__AllowedOrigins__*`にどのoriginを設定するか。Issue #6 T1の`https://${PG17_EDGE_API_DOMAIN}`（API自身のorigin）のままでよいか。

### 事実（出典つき）

- public-apiのCORSポリシーは、`Cors:AllowedOrigins`の配列だけを許可する。任意のheaderとmethodを許可し、credentialsは許可しない（`apps/public-api/Program.cs:45-51`、`:95`で適用）。配列が空なら、cross-originのブラウザ要求はすべて拒否される。
- CORSが効くのはブラウザだけ。iOSネイティブアプリ（`apps/nexus-ios`）の要求はCORSの影響を受けない。
- PG18用`compose.yaml:31`とIssue #6 T1は`https://${API_DOMAIN}`を設定している。これはAPI自身のoriginで、同一originの要求にはCORSが不要なため、**実質的に何も許可していない**のと同じ（害もない）。
- Mobileのテンプレートは`Cors__AllowedOrigins__0: ${NEXUS_PUBLIC_ORIGIN:?Set the HTTPS public origin}`（`deploy/database/compose.production.yml:51`）で、1つのorigin指定を必須にしている。
- `web/`（イベント告知LP、NexusMobile #81で再構成中）は`VITE_API_BASE_URL`（`web/src/api/client.ts:9`）でAPIを呼ぶクライアントを持っている。LPがpublic-apiをブラウザから呼ぶかどうか、呼ぶ場合のLPのドメインは#81で未確定。
- 管理ポータル`apps/admin-web`はadmin-api（`VITE_ADMIN_API_BASE_URL`）を呼ぶため、public-apiのCORSとは無関係。
- 公開GET（`/api/spots/public`、`/api/events/today`など）は公開情報だが、`POST /api/logs`は匿名の参加者ログを受け付ける。originを広く許可すると、第三者のサイトからブラウザ経由でログを投げられる（rate limitはIP単位で効く）。

### 選択肢

| 案 | 内容 | 長所 | 短所・リスク |
| --- | --- | --- | --- |
| A | 初期公開は`https://${PG17_EDGE_API_DOMAIN}`のまま（実質ブラウザ不許可） | #6の設計を変えずに済む。最小権限 | LPがブラウザからAPIを呼ぶ時点で、設定変更が必要になる |
| B | LPのHTTPS originを1つ明示する（例 `https://<lp>.nexus-app.jp`） | LPからの利用に備えられる | LPのドメインが未確定。envが1つ増える |
| C | `*`やワイルドカード | 楽 | `POST /api/logs`を任意サイトから呼べるようになる。**不可** |

### 判断に必要な追加確認

- NexusMobile #81で、LPがpublic-apiをブラウザから直接呼ぶか（呼ばずに静的データやiOSへの導線だけか）。
- LPを公開するドメインと、そのHTTPS運用（HSTSの`includeSubDomains`の再検討、Issue #9にも関係）。

### 決定

```yaml
id: DEC-3
status: open
choice:             # A / B
allowed_origins: [] # 例: ["https://api.nexus-app.jp"] または LPのorigin
decided_on:
decided_by:
rationale:
follow_up:          # 例: "#6 T1のenv名と値を確定、validatorで https:// 以外と * を拒否"
```

### 決定後に更新する箇所

- Issue #6 T1の`Cors__AllowedOrigins__0`、T3の`.env.pg17-edge.example`（案Bならenvを追加）、T4のvalidator（`https://`以外と`*`を拒否）
- `docs/pg17-public-edge.md` §6の注記、`docs/pg17-operator-checklist.md` D4

## 相談するAIへの依頼文（コピーして使う）

```text
添付の docs/pg17-edge-decisions.md を読み、DEC-1〜DEC-3 について判断材料を整理してください。
- 文書の「事実（出典つき）」を前提とし、出典と矛盾する推測をしないこと。不明点は「判断に必要な追加確認」に追記する形で示すこと。
- 各DECについて、推奨案・理由・残るリスク・採用時に必要な変更（ファイル単位）を示すこと。
- 「全判断に共通の制約（MUST）」に反する案は推奨しないこと。
- 出力は各DECの「決定」YAMLを埋めた案とし、decided_by は空欄のままにすること（決定するのは人間）。
```

## 決定の反映手順

1. 人間が各DECの「決定」YAMLを埋め、`status: decided`にする（AIの提案は`rationale`に要約し、出典を残す）。
2. 冒頭のYAMLの`status`を更新する。
3. 「決定後に更新する箇所」を同じPR、または参照先Issueで直す。
4. NexusInfra #6・#7のIssueに、決定内容（値は秘密でないもののみ）とこの文書へのリンクを残す。
