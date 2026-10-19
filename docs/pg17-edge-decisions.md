# PG17公開レイヤー: 判断3件（DEC-1〜3決定済み）

この文書は、Codex・ChatGPTなど別のAIと3件の判断を相談するための**自己完結した資料**である。この文書だけで判断できるように、事実・制約・選択肢・決定の記録欄をまとめた。決まったら各判断の「決定」欄を埋め、「決定後に更新する箇所」を直す。

```yaml
document: pg17-edge-decisions
status: decided         # 方針の決定。実装・検証・本番適用の完了ではない
created: 2026-10-18
repositories:
  infra: m0o9l0v3/NexusInfra (main 1f55578)
  mobile: m0o9l0v3/NexusMobile (develop 6459abd; API/DB code unchanged since 87446aa)
related_issues: [NexusInfra#5, NexusInfra#6, NexusInfra#7, NexusMobile#81]
decisions:
  - id: DEC-1
    topic: public-apiのrate limitで、Caddy越しのクライアントIPを正しく扱う
    status: decided
    blocks: [NexusInfra#6 T1, NexusInfra#7]
  - id: DEC-2
    topic: public-apiのimmutable digestをどこで発行し、VPSがどう取得するか
    status: decided
    blocks: [NexusInfra#5 §4, NexusInfra#7]
  - id: DEC-3
    topic: public-apiのCORS許可origin
    status: decided
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

- 設計確認済み（2026-10-18）: [Issue #6 T4](https://github.com/m0o9l0v3/NexusInfra/issues/6)はサービス集合を`{"public-api", "caddy"}`に限定し、各サービスの接続networkも検査する。今回validatorと拒否テストを実装した。実networkの参加状態は運用時に別途確認する。
- 採用するCaddy版・digestで、受信した偽装`X-Forwarded-*`が信頼されず、接続元IPを基に転送されることを隔離環境で確認する。[Caddy公式資料](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#defaults)は既定で受信値を無視すると説明しているが、採用イメージでの実行確認は未完了。
- 起動時にedgeの実参加コンテナを確認する手順を定める。静的validatorでは運用中の手動接続までは防げない。
- [Issue #9](https://github.com/m0o9l0v3/NexusInfra/issues/9)でStudioを同じCaddyに追加する前に、案Bまたはnetwork分離を含めて信頼範囲を再判断する。同じsubnetをそのまま信頼することは今回の決定に含めない。

### 決定

```yaml
id: DEC-1
status: decided
choice: A
decided_on: 2026-10-18
decided_by: m0o9l0v3
rationale:
  summary: "AI提案を人間が承認。初期API公開ではRateLimiting__KnownNetworks__0にPG17_EDGE_SUBNETを渡す。"
  reasons:
    - "CaddyのIPを使った全クライアント共通のrate limit枠を解消するため。"
    - "既存envを再利用でき、Mobileの本番テンプレートと整合し、固定IP管理が不要なため。"
  sources:
    - "NexusMobile apps/admin-api/RateLimiting/RateLimitingExtensions.cs:33-46,67"
    - "NexusMobile apps/public-api/Program.cs:83,96"
    - "NexusMobile deploy/database/compose.production.yml:52"
    - "NexusInfra #6 T4（サービス集合と接続networkの検査設計）"
  conditions:
    - "初期edgeへの参加はCaddyとpublic-apiのみとし、validatorで検査する。"
    - "Studioなどのサービス追加前に信頼範囲を再判断する。"
  remaining_risks:
    - "subnet内の全接続元が信頼対象になる。"
    - "静的検査では運用中の手動network接続を防げない。"
follow_up:
  file_changes:
    - "compose.pg17-edge.yaml: public-apiにRateLimiting__KnownNetworks__0: ${PG17_EDGE_SUBNET}を追加（ローカル実装済み、未レビュー）。"
    - "scripts/validate-pg17-edge.py: rawではenv参照、resolvedではedge subnetとの一致、参加サービスと接続networkを検査（ローカル実装済み、未レビュー）。"
    - "tests/test_validate_pg17_edge.py: 設定欠落・subnet不一致・追加サービスのedge参加を拒否するテスト（ローカル実装済み、未レビュー）。"
    - "docs/pg17-public-edge.md §6: 採用条件と残る確認を記載。"
    - "docs/pg17-baseline.md §4: クライアント別の制限枠・429・偽装ヘッダーの確認を記載。"
  issue_updates_pending:
    - "#6 T1・T4・T5に採用内容を反映する。"
    - "#7に採用イメージでの確認項目と実行結果を記録する。"
    - "PG18 compose.yamlの同種問題は別Issueで扱う。"
  application_status: "Compose・validator・テストをローカル実装済み。採用digestによるAPI隔離検証・本番適用は未実施。"
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

- VPSのOS・CPUアーキテクチャをPart 0で確認し、`target_platform`を確定する。確認前にplatformを推測して採用digestを決めない。
- 提案名`ghcr.io/m0o9l0v3/nexus-public-api`の利用可否、既存パッケージとの競合、Mobileのworkflowから発行できる権限を確認する。
- PAT発行アカウントが対象パッケージの読み取り権限を持つこと、PAT（classic）がアカウント・組織の認証方針で利用できることを確認する。必要な場合はSSO承認も確認する。
- 発行対象のブランチ・commit、レビュー済みcommitの採用手順、digest・発行ジョブ・provenance/attestationの保存方法と期間を確定する。
- 初期はprivateとする。将来publicに変更する場合は、公開してよい成果物かを別途判断する。GitLab経路の稼働確認は案B採用の前提とせず、暫定利用を検討する場合に限って行う。

認証方式の出典（2026-10-18確認）: [GitHub公式資料](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。workflowからの発行には`GITHUB_TOKEN`、VPSからのprivateイメージ取得にはPAT（classic）の`read:packages`を使用する。publicイメージは匿名取得できる。これは仕様確認であり、対象パッケージの権限・実際の取得可否は未検証。

### 決定

```yaml
id: DEC-2
status: decided
choice: B
registry: ghcr.io/m0o9l0v3/nexus-public-api-pg17-private # 2026-10-19承認の新名。利用可否・発行は未確認
visibility: private
vps_pull_auth: "PAT（classic）、scopeはread:packagesのみ"
target_platform: linux/amd64 # 2026-10-19の人間による読み取り確認。新digestとの照合は未完了
decided_on: 2026-10-18
decided_by: m0o9l0v3
initial_registry: ghcr.io/m0o9l0v3/nexus-public-api # 2026-10-18決定時の提案名。初回発行でPublic表示となり置換
updated_on: 2026-10-19 # registry・target_platform・remaining_risks・application_statusを更新
rationale:
  summary: "AI提案を人間が承認。GitHub Actionsからpublic-apiをGHCRへ発行し、初期はprivateとする。platformは確認後に確定する。"
  reasons:
    - "GitHub Primary・GitLab DRの方針に整合する。"
    - "レビューしたcommit SHA・発行ジョブ・immutable digestを対応づけられる。"
    - "公開可否が未決のままイメージを公開せずに進められる。"
  sources:
    - "本文DEC-2の事実: 現行GitLab発行経路とGitHub Primary方針"
    - "https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry"
  conditions:
    - "CIの発行認証にはGITHUB_TOKENを使う。"
    - "VPSには読み取り専用資格だけを置き、値をGitや文書に記録しない。"
    - "採用commit・digest・platformを照合し、本番参照は@sha256:で固定する。"
  remaining_risks:
    - "Mobile PR #97は統合済み。旧名の初回発行はprivate検査で失敗。新名への修正レビュー・再発行・private確認が必要。"
    - "VPSの取得資格には期限・更新・失効管理が必要。"
    - "digest固定だけでは、同じソースからの再build結果の一致までは保証しない。"
follow_up:
  file_changes:
    - "NexusMobile .github/workflows/public-api-image.yml（新規案）: 発行条件・platform・GHCR push・digestと証跡の記録（ローカル実装済み、未レビュー）。"
    - "NexusMobile deploy/README-production-images.md: 発行経路・採用手順・取得認証を追記（ローカル実装済み、Mobile別PRでレビュー）。"
    - "NexusInfra docs/pg17-public-edge.md §4: GHCR経路と採用条件を記載。"
    - "NexusInfra docs/pg17-operator-checklist.md D1・D2・V4: GHCR認証とplatform照合に対応。"
  issue_updates_pending:
    - "#5 §4・#7に採用経路を反映し、採用commit・digest・発行ジョブURLを記録する。"
  application_status: "2026-10-19: PR #97統合・Environment設定済み。旧名へのpush成功、発行後private検査失敗、証跡保存未完了。新名の修正レビュー・利用可否確認・発行・取得認証・本番適用は未完了。"
```

### 2026-10-19追記: Private方針を維持した発行先変更

[初回発行ジョブ](https://github.com/m0o9l0v3/NexusMobile/actions/runs/39200756827)はAPIテスト・build・旧名`ghcr.io/m0o9l0v3/nexus-public-api`へのpushまで成功し、発行後のprivate検査で失敗した。人間の画面確認でPublic表示を確認した。公開に変わった経緯は未確定。ユーザーはprivate方針を維持し、別名へ切り替える修正を承認した。

新しい発行先は`ghcr.io/m0o9l0v3/nexus-public-api-pg17-private`とする。名前だけでprivateとは判定せず、利用可否・権限を確認し、発行前後のprivate検査・採用証跡の保存を完了してから採用する。初回ジョブの証跡保存は未完了。VPS platformは人間の読み取り確認で`linux/amd64`と確認済みだが、新digestとの照合・取得認証は未完了。旧Public版の削除や公開範囲の変更はこの修正に含めず、扱いを別途判断する。上の「判断に必要な追加確認」は2026-10-18時点の記録として旧名のまま残し、決定ブロックは`updated_on`で更新日を区別する。

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
- `web/`は`VITE_API_BASE_URL`（`web/src/api/client.ts:9`）を使うfetchクライアントを持つ。[NexusMobile #81](https://github.com/m0o9l0v3/NexusMobile/issues/81)はclosedで、LPの動的データを当日イベント一覧に必要な範囲でpublic-apiと結合する方針と、`GET /api/events/today`の実装・検証を記載している（2026-10-18確認）。closedという状態だけでは本番公開・結合検証済みとは判断しない。ブラウザから直接呼ぶか、LPの公開originは今回の確認では未確定。
- 管理ポータル`apps/admin-web`はadmin-api（`VITE_ADMIN_API_BASE_URL`）を呼ぶため、public-apiのCORSとは無関係。
- 公開GET（`/api/spots/public`、`/api/events/today`など）は公開情報だが、`POST /api/logs`は匿名の参加者ログを受け付ける。originを広く許可すると、第三者のサイトからブラウザ経由でログを投げられる（rate limitはIP単位で効く）。

### 選択肢

| 案 | 内容 | 長所 | 短所・リスク |
| --- | --- | --- | --- |
| A | 初期公開は`https://${PG17_EDGE_API_DOMAIN}`のまま（実質ブラウザ不許可） | #6の設計を変えずに済む。最小権限 | LPがブラウザからAPIを呼ぶ時点で、設定変更が必要になる |
| B | LPのHTTPS originを1つ明示する（例 `https://<lp>.nexus-app.jp`） | LPからの利用に備えられる | LPのドメインが未確定。envが1つ増える |
| C | `*`やワイルドカード | 楽 | `POST /api/logs`を任意サイトから呼べるようになる。**不可** |

### 判断に必要な追加確認

- LPの正確なHTTPS originと、APIへの接続構成（ブラウザから直接呼ぶか、同一originの中継を使うか）。#81にAPI利用方針があるため、「LPはAPIを使わない」とは扱わない。
- LPとAPIを同時公開する必要があるか。別originから直接呼ぶLPを同時公開する場合は、公開前にoriginを確定し案Bを再判断する。
- LP接続時に、現行CORSポリシーの任意method許可を維持してよいか。originの許可はGET専用の許可ではない。
- LPのHTTPS運用（HSTSの`includeSubDomains`の再検討、Issue #9にも関係）。

### 決定

```yaml
id: DEC-3
status: decided
choice: A
allowed_origins:
  - "https://${PG17_EDGE_API_DOMAIN}" # 実行時にAPIのHTTPS originへ展開
decided_on: 2026-10-18
decided_by: m0o9l0v3
rationale:
  summary: "AI提案を人間が承認。初期API公開は案Aとし、別originのLP接続前に案Bを再判断する。"
  reasons:
    - "iOSネイティブの要求はCORSの影響を受けない。"
    - "未確認のLP originを推測して許可しない。"
    - "初期構成ではAPI自身のHTTPS origin指定を維持できる。"
  sources:
    - "NexusMobile apps/public-api/Program.cs: CORSの許可origin設定と適用"
    - "https://github.com/m0o9l0v3/NexusMobile/issues/81"
    - "NexusMobile web/src/api/client.ts: VITE_API_BASE_URLを使ったfetch"
  conditions:
    - "別originのLPがAPIを直接呼ぶ場合、接続開始前にそのHTTPS originを確定して案Bを再判断する。"
    - "ワイルドカードは許可しない。"
  remaining_risks:
    - "案Aのままでは別originのLPからAPIレスポンスを読み取れない。"
    - "将来LPのoriginを許可すると、現行ポリシーはGET以外のmethodも許可する。"
follow_up:
  file_changes:
    - "compose.pg17-edge.yaml: Cors__AllowedOrigins__0をAPI自身のHTTPS originに設定（ローカル実装済み、未レビュー）。"
    - ".env.pg17-edge.example: 初期はLP用origin変数を追加しない（ローカル実装済み、未レビュー）。"
    - "scripts/validate-pg17-edge.py: rawのenv参照とresolvedのAPI origin一致、HTTPS、ワイルドカード不使用を検査（ローカル実装済み、未レビュー）。"
    - "tests/test_validate_pg17_edge.py: 正常例と不正originの拒否を検証（ローカル実装済み、未レビュー）。"
    - "docs/pg17-public-edge.md §6: 初期構成とLP接続前の再判断条件を記載。"
    - "docs/pg17-operator-checklist.md D4: 同じ条件を反映。"
  issue_updates_pending:
    - "#6 T1・T3・T4・T5へ設定と検査条件を反映する。"
    - "#7へ初期構成のCORS確認項目と結果を記録する。"
  application_status: "Compose・validator・テストをローカル実装済み。LP origin・接続構成は未確認。採用digestによるブラウザ検証・本番適用は未実施。"
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
