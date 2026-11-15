# 最初の参加者 bundle を本番に公開する手順（Studio を常設しない一時セッション）

状態: **手順とリハーサルの記録。本番には未適用。** 2026-11-15 に使い捨ての PG17.11（colima）で通しのリハーサルを行った（7節）。本番の操作はすべてオーナーが行う。Claude・CI は本番に接続しない。

関係: #17（`delivery` の権限と Caddy 経由の確認）、#8（Studio の role と `studio`・`delivery` スキーマ、2026-11-15 本番適用済み）、#9（Studio の常設。未着手）、NexusMobile E2-5（配信契約）・E2-5 改訂1（2.1.0、イベントの同梱）、NexusMobile #22・#109（iOS の取得と保存）、NexusStudio `deploy/README.md`。

## 0. いまの状態と、この手順で変わること

- 本番の `GET https://api.nexus-app.jp/api/v2/bundle` は 404（`urn:nexus:problem:bundle-not-published`）。`delivery.participant_bundles`・`delivery.current_bundle` が 0 行のため。
- bundle を作れるのは Studio の公開処理だけ（`PublishingService.PublishAsync` → 同じトランザクションで `studio.releases` と `delivery.participant_bundles` に1行、`current_bundle` を作成または前進。`pg_advisory_xact_lock(7201)` で直列化）。payload は `ParticipantBundleProjection`（schema 2.1.0）、`sha256`・`byte_length` は payload から計算し、§5.3 の不変条件（`ParticipantBundleInvariants`）を満たさなければ公開ごと取り消す。**署名は無い**（完全性は SHA-256 と長さ。public-api は送信前に再計算し、iOS は `Nexus-Bundle-Sha256`・`Nexus-Bundle-Length` と不変条件で検証する）。
- Studio は本番に常設していない（#9）。この手順は、**公開のあいだだけ** VPS 上で Studio を動かし、終わったら止める。インターネットからは到達させない。
- この手順で本番に残るもの: Studio の管理者1人（`studio.admins`）、入力した参照データ・公開記録（`studio.*`）、bundle 1件と `current_bundle` 1行（`delivery.*`）。**bundle の版は戻せない**（6節）。

## 1. 方式の比較と推奨

| 案 | 内容 | 長所 | 短所 |
|---|---|---|---|
| **1（推奨）VPS 上の一時 Studio** | `compose.studio-oneoff-publish.yml`。studio-api は DB の network と専用 network に参加、Caddy（`tls internal`）は VPS の `127.0.0.1` だけで待ち受ける。オーナーは `ssh -L` でブラウザから使う | 本番と同じ経路（`nexus_studio_app`、Production 設定、Secure Cookie・CSRF、DB の network 内の接続）。DB をホストや Mac に出さない。公開の検証・Release・ID 予約表・不変条件が Studio のコードのまま働く | VPS でイメージをビルドする（ベースイメージの取得が要る）。一時的に DB の network に Studio が加わる |
| 2 Mac の Studio から SSH トンネルで DB へ | Mac で Studio を動かし、PG17 へ SSH 経由で接続 | VPS で動かすものが無い | PG17 は host port を持たないため、DB を VPS の loopback へ出す中継（socat 等）を新たに置く必要がある。`nexus_studio_app` のパスワードと DB の通信が Mac に出る |
| 3 SQL で bundle を直接入れる | 手で作った JSON を `participant_bundles` に INSERT | 道具が要らない | Studio の検証・Release・ID 予約表を通らない。次に Studio で公開すると `release.Sequence`（1から）が既存の版と衝突し、公開が失敗するか版が後退する。**採らない** |

**推奨は案1。** 理由: 契約（E2-5 §3.3「bundle は Studio の公開でだけ作る」）を守ったまま、DB を外に出さずに済み、2026-11-15 のリハーサルで同じ Compose・Caddyfile・コマンドを通したため。

案1 に要るもの:

- **資格情報（場所だけ）**: `nexus_studio_app` のパスワード＝#8 の C で作った VPS の私有ディレクトリの root 所有 0600 のファイル（`nexus_studio_app_password`）。DB 確認用の `nexus_public_password`・`nexus_studio_migrator_password` も同じ場所。Studio 管理者のパスワード＝オーナーのパスワードマネージャー（新規に作る）。VPS への SSH 鍵＝オーナーの Mac。どれも Git・Issue・会話に出さない。
- **ネットワーク**: VPS の `127.0.0.1:<空きポート>` だけ。DNS・本番 Caddy・ファイアウォールは変えない。Mac からは `ssh -L 8443:127.0.0.1:<ポート> <VPS>`。
- **承認**: 3節の P-1〜P-5。

## 2. 最初の bundle の中身（オーナーの判断）

Studio の公開前検証（`CandidateValidator`、develop `165d468`）の事実:

- イベントの枠は会場が1件以上必要（`slot_venue_required`）、会場の Spot は公開済みであること（`venue_spot_unpublished`）。
- Spot は公開中の地図に同じ ID の目的地 Feature が無いと公開できない（`spot_map_unpublished`・`undefined_spot_canonical_id`、Studio 判断記録29 M1-D4・31 D2）。
- したがって **地図なしでイベントだけ**の bundle は、Studio のコードを変えないと作れない。案内（Info）は bundle に入らない（NexusMobile E6-6、アプリに同梱）。
- カテゴリ一覧・開催回（開催日）は地図なしで公開でき、`spots: []`・`map: null`・`events: []` の 2.1.0 bundle になる（Schema・不変条件に合格。リハーサルで確認）。

選択肢（推奨は A）:

| | 内容 | 結果 |
|---|---|---|
| **A** | 実在するカテゴリ一覧と開催回・開催日だけ（地図・地点・イベントなし） | 配信の経路を本番で確かめられ、作り物のデータを出さない。iOS は地図を「表示できる建物・地点がありません」、ホームを「この後のイベントはありません」と表示する（7.4節の注意） |
| B | 測量が終わるまで公開しない | 本番は 404 のまま。この手順は保留 |
| C | 暫定の地図・地点・イベントを公開 | 「本番の地図データになってから」の方針とずれる。地図の公開には `MapValidation__ApprovedSettingsFile`（承認済み設定）も要る |

**決定（2026-11-15、オーナー）: A。** 先の開催回の情報は直前まで得られないため、オーナーが提供したのは **2026-11-14（土）に実施済みの開催回**の実績（午前の部、参加者の時間 10:00〜13:30 JST）である。

### 2.1 中身の提案（オーナーの確認待ち）

| 項目 | 提案 | 確認 |
|---|---|---|
| 公開する対象 | **開催回 1件だけ**。カテゴリ一覧は今回は公開しない | 要確認 |
| 開催回の名称（`name`） | `11月オープンキャンパス（午前の部）` | **要確認**（資料の題は「2026年11月 AM version タイムスケジュール」で、正式名ではない） |
| 開催日（`days[0].date`） | `2026-11-14` | 資料どおり |
| 一般公開時間（`publicStart`・`publicEnd`） | `10:00`・`13:30`（JST） | 資料の最初の項目（10:00 学科説明）と帰りのバスの出発（13:30）から。要確認 |
| 開催状況（`status`） | `normal` | — |
| 出典（`sourceNote`、Studio 内部だけ。bundle には入らない） | `2026年11月 AM version タイムスケジュール（オーナー提供）` | 担当者名は書かない |

カテゴリを今回入れない理由: カテゴリはイベントの分類で、イベントの無い bundle では iOS のどこにも表示されない。さらに **保存したカテゴリは削除できず**（「新規選択停止」だけ）、カテゴリ一覧は取り下げもできない（`withdraw_not_supported`）。推測した名称を本番に残さないため、実際のイベントを入れるときに正式な一覧で作る。資料の項目からの候補（正式な一覧ではない）: 学科説明、学生生活・座談会、体験授業（ドローン・CATIA）、寮見学、食事。

過去の開催回について確かめたこと（2026-11-16 のリハーサル、7.6節）:

- Studio: 開催日が過去でも公開は止まらず、警告も出ない（過去日の警告 `slot_in_past` はイベントの枠だけ）。不変条件・Schema にも合格する。
- public-api: 日付を見ない。200 で配信する。
- iOS のホーム: 開催日カードに「オープンキャンパス開催日／11月14日（土）10:00〜13:30／**終了**／この日の公開は終了しました」と表示する。「次の開催」としては出さない。その下に「この後のイベントはありません／公開されているイベントは、すべて終了しています。」と出る（イベント0件でも同じ文言。NexusMobile #22 に記録）。地図タブは「表示できる建物・地点がありません」。
- 参加者への影響: iOS の Release ビルドの接続先はまだ空（`NEXUS_API_BASE_URL`）で、App Store にも公開していない。この bundle を受け取るのは、接続先を設定した Debug ビルド（オーナーの確認用）だけである。
- 後から置き換えられるか: 地図・地点・イベントを含む次の公開は版 2 になり、iOS は版 1 を「1つ前の版」として残して版 2 に切り替えた（`pointer.json` の `current: v2`、`previous: v1`）。ホームは未来の開催日を優先して表示し、地図も描画された。その公開では、配信データが1.5倍以上になる警告 `bundle_grew` が出る（意図した増加なので確認して進めてよい）。

### 2.2 オーナーが記入する欄（Studio の項目名のまま）

時刻はすべてキャンパスの現地時刻（JST、`HH:mm` の24時間制）。日付は `YYYY-MM-DD`。下の例の値は**架空**。

**開催回（Studio の「開催回」画面。1件ごと）**

| 項目（API の名前） | 必須 | 形式・制限 | 例（架空） |
|---|---|---|---|
| 名称（`name`） | 公開に必須 | 200文字以内 | `架空キャンパス 体験会（午前）` |
| 出典（`sourceNote`） | 任意 | 1000文字以内。Studio の中だけで使い、配信しない。個人名を書かない | `配布資料 v3 より` |
| 開催日（`days[]`） | 任意（0件でも保存できるが警告） | 100件以内。同じ開催回に同じ日付は不可 | 下の表 |

**開催日（開催回の中に1行ずつ）**

| 項目 | 必須 | 形式・制限 | 例（架空） |
|---|---|---|---|
| 日付（`date`） | 必須 | `YYYY-MM-DD` | `2099-01-02` |
| 一般公開の開始（`publicStart`） | 任意 | `HH:mm`。開始と終了は両方入れるか両方空 | `09:30` |
| 一般公開の終了（`publicEnd`） | 任意 | `HH:mm`。開始より後 | `12:00` |
| 開催状況（`status`） | 必須 | `normal`（開催）／`cancelled`（中止） | `normal` |
| 中止案内（`cancelNote`） | 任意 | 500文字以内。中止のときに表示 | （空） |

**カテゴリ一覧（今回は記入不要。イベントを入れるときに使う）**

| 項目 | 必須 | 形式・制限 | 例（架空） |
|---|---|---|---|
| 名称（`items[].name`） | 必須 | 100文字以内、200件まで。**保存後は削除できない** | `架空の体験コーナー` |
| 新規選択（`items[].selectable`） | 必須 | はい／いいえ（新規選択停止） | はい |

## 3. 前提と承認

前提（すべて満たしてから始める）:

- 本番の PG17 に #8 の B・C が適用済み（Studio の移行 16件、最後 `20261112071332_RemoveCampusGroundEntityKind`）。public-api（NexusMobile `f42d9da`）と PG17 edge の Caddy が稼働中。
- NexusStudio の採用 commit を決める（リハーサルは `165d468`。`apps/studio-api` は本番の Studio SQL の出所 `70994f0` と同一）。その後に `apps/studio-api` の移行が増えていたら**止める**（本番の DB と合わない）。
- 2節の内容が決まっていて、入力する値（カテゴリ名、開催回の名前、開催日・一般公開時間）が手元にある。

承認（オーナーが記録する。1つの承認は他に広げない）:

| # | 承認すること | 影響 |
|---|---|---|
| P-1 | 2節の中身 | 参加者のアプリに表示される |
| P-2 | 公開前のバックアップ（[pg17-issue-8-production-apply.md](pg17-issue-8-production-apply.md) 5.1節と同じ形） | 6節 R3 の唯一の手段 |
| P-3 | VPS でのイメージのビルド（`mcr.microsoft.com/dotnet/sdk:10.0`・`aspnet:10.0`・`node:22-alpine`・`caddy:2-alpine` の取得を含む） | ディスク使用量（1〜2 GB）。終了後に削除できる |
| P-4 | 一時 Studio の起動と、最初の管理者の登録（`admin add`） | 管理者は Studio に残る（#9 で常設するときもそのまま使う） |
| P-5 | 公開の実行 | `delivery` に版 1 ができ、`/api/v2/bundle` が 200 になる。戻せない |

## 4. 手順

表記: `<…>` は私有メモの値に置き換える。実値（IP、パス、container 名、system_identifier、パスワード）は Git・Issue・会話に書かない。VPS では root のシェルで実行する。

### 4.1 Mac での準備

```sh
cd <nexus-studio のチェックアウト> && git fetch origin
STUDIO_COMMIT=<採用する commit（例 165d4687d5b8a02b78fe5d470a309592a2c16022）>
git merge-base --is-ancestor "$STUDIO_COMMIT" origin/develop && echo reachable
git diff --stat 70994f0 "$STUDIO_COMMIT" -- apps/studio-api/Migrations   # 空であること
git archive --format=tar "$STUDIO_COMMIT" | gzip -n > nexus-studio-"$STUDIO_COMMIT".tar.gz
shasum -a 256 nexus-studio-"$STUDIO_COMMIT".tar.gz        # 私有メモに記録
cd <nexus-infra のチェックアウト> && git fetch origin   # この文書を含む origin/main
```

VPS の root 所有 0700 の作業ディレクトリ（例 `/root/nexus-first-bundle-<日付>/`）へ、tar.gz と NexusInfra の `compose.studio-oneoff-publish.yml`・`Caddyfile.studio-oneoff`・`scripts/first-bundle-db-checks.sh` を送る。VPS で `sha256sum` が Mac の値と一致すること。

### 4.2 事前確認（読み取りだけ）

```sh
C=<PG17_CONTAINER>
docker exec --user postgres "$C" psql -X -At -U nexus -d nexus -c "SELECT current_setting('server_version'), (SELECT count(*) FROM studio.\"__EFMigrationsHistory\"), (SELECT max(\"MigrationId\") FROM studio.\"__EFMigrationsHistory\"), (SELECT count(*) FROM delivery.participant_bundles), (SELECT count(*) FROM delivery.current_bundle), (SELECT count(*) FROM studio.admins)"
docker inspect --type container --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$C"   # DB の network 名（私有メモ）
docker ps --format '{{.Names}} {{.Status}}'          # public-api・Caddy が healthy
ss -ltn | grep -c ':<予定のポート> '                   # 0（空いている）
df -h /var/lib/docker                                 # 3 GB 以上の空き
curl -s -o /dev/null -w '%{http_code}\n' https://api.nexus-app.jp/api/v2/bundle   # 404
```

- 期待値: `17.11|16|20261112071332_RemoveCampusGroundEntityKind|0|0|0`、ポートが空き、404。
- 止める条件: どれかが違う（特に `studio.admins` や `delivery` に行がある＝誰かが先に何かをした）。

### 4.3 バックアップ（P-2）

[pg17-issue-8-production-apply.md](pg17-issue-8-production-apply.md) 5.1節（`scripts/backup-pg17-source.sh`、Mac へのコピー、別 volume への復元確認）と同じ。合格するまで先へ進まない。

### 4.4 イメージのビルド（P-3、VPS でネイティブに）

Mac（arm64）からの amd64 クロスビルドは、リハーサル環境のビルダーでは失敗した（7.5節）。VPS（`linux/amd64`）でビルドする。

```sh
cd /root/nexus-first-bundle-<日付> && mkdir src && tar -xzf nexus-studio-<commit>.tar.gz -C src && cd src
docker build -f apps/studio-api/Dockerfile --label org.opencontainers.image.revision=<commit> -t nexus-studio-api:<commit7> .
docker build -f apps/studio-web/Dockerfile --label org.opencontainers.image.revision=<commit> -t nexus-studio-web:<commit7> .
docker image inspect -f '{{.Id}} {{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}' nexus-studio-api:<commit7> nexus-studio-web:<commit7>
```

- 期待値: `amd64` と採用 commit。イメージ ID を私有メモと Issue の記録に書く（ID は秘密ではない）。
- 止める条件: ビルドの失敗、revision の不一致。

### 4.5 接続情報と設定（値を表示しない）

```sh
cd /root/nexus-first-bundle-<日付>
install -d -m 0700 secrets
( umask 077; printf 'Host=<DB の alias>;Port=5432;Database=nexus;Username=nexus_studio_app;Password=%s' \
    "$(cat <私有ディレクトリ>/nexus_studio_app_password)" > secrets/studio_connection )
chown 1654 secrets/studio_connection && chmod 0400 secrets/studio_connection   # イメージの実行 UID（APP_UID=1654）
chmod 0711 secrets
cat > oneoff.env <<'EOF'
STUDIO_ONEOFF_PROJECT=nexus-studio-oneoff-<YYYYMMDD>
STUDIO_ONEOFF_API_IMAGE=nexus-studio-api:<commit7>
STUDIO_ONEOFF_WEB_IMAGE=nexus-studio-web:<commit7>
STUDIO_ONEOFF_CADDY_IMAGE=<採用済み Caddy の digest（PG17 edge と同じ）>
STUDIO_ONEOFF_SUBNET=<既存と衝突しない private /28>
STUDIO_ONEOFF_PORT=<空いている loopback ポート>
STUDIO_ONEOFF_BACKEND_NETWORK=<DB の network 名>
STUDIO_ONEOFF_SECRET_DIR=/root/nexus-first-bundle-<日付>/secrets
EOF
chmod 0600 oneoff.env
DC="docker compose --env-file oneoff.env -p nexus-studio-oneoff-<YYYYMMDD> -f compose.studio-oneoff-publish.yml"
$DC config --quiet && echo config-ok
```

- `STUDIO_ONEOFF_SUBNET` は PG17 edge（`PG17_EDGE_SUBNET`）と別の値にする。Studio はこの subnet からの `X-Forwarded-Proto` だけを信頼する（public-api の DEC-1 の信頼範囲は変わらない）。

### 4.6 管理者の登録（P-4）

```sh
$DC run --rm --no-deps studio-api admin add --email <管理者のメール> --name <表示名>
```

パスワードは端末に2回入力する（引数・履歴に残らない）。8時間で切れる Cookie のセッションになる。

### 4.7 一時 Studio の起動と公開（P-5）

```sh
$DC up -d
$DC ps                                         # studio-api・studio-web が healthy、caddy が running
curl -sk -o /dev/null -w "%{http_code}\n" https://localhost:<ポート>/health   # 200
```

Mac で `ssh -N -L 8443:127.0.0.1:<ポート> <VPS>` を開き、Chrome で `https://localhost:8443` を開く（内部 CA の警告は、この一時セッションだけ許可する）。

1. 4.6 の管理者でログインする。
2. 2.1節の確定した値で「開催回」を1件作って保存する（カテゴリは作らない）。
3. 「公開確認」で対象を選び、**公開阻止 0件**であることと差分を確かめる。警告があれば内容を記録する。
4. 公開メッセージ（例「最初の bundle」）を入れて公開する。結果が「公開成功」で、Release の番号が 1 であること。
5. 「公開履歴」に Release 1 があることを確かめ、ログアウトする。

止める条件: 公開阻止がある、結果が「失敗」（公開内容は変わらない。原因をログで確認し、直してから3から）。

### 4.8 一時 Studio の停止と片付け

```sh
$DC down                                       # -v は付けない
docker volume rm nexus-studio-oneoff-<YYYYMMDD>_dataprotection_keys nexus-studio-oneoff-<YYYYMMDD>_caddy_data
shred -u secrets/studio_connection             # またはファイルを削除
docker ps -a --filter label=com.docker.compose.project=nexus-studio-oneoff-<YYYYMMDD> -q   # 空
```

イメージは #9 まで残してよい（再公開に使える）。消す場合は `docker image rm nexus-studio-api:<commit7> nexus-studio-web:<commit7>` と、使ったベースイメージを名前で個別に消す（prune は使わない）。Mac の `ssh -L` も閉じる。

## 5. 確認

### 5.1 公開の直後（VPS、読み取り＋必ず ROLLBACK する probe）

```sh
DB_NETWORK=<DB の network 名> DB_HOST=<DB の alias> PG_IMAGE="$(docker inspect --type container --format '{{.Image}}' "$C")" \
  SECRET_DIR=<私有ディレクトリ> EXPECT_CURRENT=1 sh scripts/first-bundle-db-checks.sh
```

- 全 probe を `BEGIN … ROLLBACK` の中で各ロールとして実行する（拒否されるはずの更新が通っても残らない）。確認内容: 版が 1、payload の SHA-256・長さが一致、`nexus_public` は2表の SELECT だけ、`nexus_studio_app` は UPDATE・DELETE・TRUNCATE・トリガー無効化・**ポインタの後退**が拒否され、INSERT と前進は可能、所有者でも後退は拒否、前後で状態が同じ。
- 期待値: `RESULT: 0 failure(s)`。#17 の受入条件2・3を本番で確かめた記録になる。

監視（#17 の受入条件4）: `monitor-delivery-size.sql` を `nexus_public` で実行し、`1|<bytes>|<size>|f` になること。

### 5.2 Caddy 経由（Mac、読み取り）

```sh
API=https://api.nexus-app.jp SITE_ORIGIN=https://www.nexus-app.jp EXPECT_VERSION=1 sh scripts/first-bundle-http-checks.sh
```

- 確認内容: 200、`ETag = "sha256-<Nexus-Bundle-Sha256>"`、本体の SHA-256・長さがヘッダーと一致、`Nexus-Bundle-*`・`Nexus-Recheck-After` がある、`If-None-Match` で 304、gzip・zstd で接尾辞付き ETag と 304、`Accept-Encoding` を変えると 200、古い ETag で 200、**CORS は `/health` だけ**（`/api/v2/bundle` に `Access-Control-Allow-Origin` が付かない、別 origin は拒否）。
- 本体が Caddy の圧縮の最小長（512 バイト）未満だと gzip・zstd は圧縮されず、スクリプトは `NOTE` を出す（不合格ではない）。**2.1節の bundle は約 500 バイト**（リハーサルで 504 バイト）なので、本番の最初の版では接尾辞付き ETag と圧縮時の 304 は確かめられない。それらは 2026-10-25 の I4 と今回のリハーサルで確認済みで、本番では地図を含む次の版で確かめる（#17）。
- 期待値: `RESULT: 0 failure(s)`。#17 の受入条件5（Caddy 経由の確認）の本番での記録になる。

### 5.3 iPhone

Release ビルドの接続先（`NEXUS_API_BASE_URL`）は空のままなので、Debug ビルドで確かめる。

1. `apps/nexus-ios/Config/Local.xcconfig`（Git に入らない）に `NEXUS_API_BASE_URL = https:/$()/api.nexus-app.jp` を書く。
2. scheme「Nexus API」で実機にインストールして起動する。その iPhone で以前に別の環境（隔離環境など）の bundle を取得していたら、先にアプリを削除する（iOS は自分の版より低い版を受け取らず、本番の版は 1 から始まる）。
3. 期待: ホームに開催日カード「11月14日（土）10:00〜13:30／終了」が出る（2.1節の内容の場合）。マップは「表示できる建物・地点がありません」。設定などの保存版の表示に `bundle_version` 1・公開日時が出る。機内モードで再起動しても同じ表示（保存版）になる。
4. 終わったら Local.xcconfig を消すか空にする。Release の接続先を設定するかは NexusMobile 側の別判断。

## 6. 切り戻し（ポインタは戻せない）

前提: `current_bundle` の後退はトリガーで所有者にも拒否される。iOS は版を戻さず（E2-5 §6 規則4）、一度取得した版を保存して使い続ける。**取得済みの端末から内容を消す手段は無い。**

| # | いつ | 方法 | 結果 |
|---|---|---|---|
| R0 | 公開前に止めた | 何もしない（4.8 で片付け） | 本番は 404 のまま |
| R1（既定） | 内容を直したい | 一時 Studio を再び起動し（4.5〜4.8）、訂正・取り下げ・復旧を**新しい版**として公開する | 版 2 以降。端末は次の確認（既定 300 秒、起動・前面復帰時）で置き換える |
| R2（緊急停止、要承認） | 配信そのものを止めたい | 所有者（`nexus_studio_migrator`）で `DELETE FROM delivery.current_bundle;` だけを行う（`participant_bundles` は消さない） | API は 404 に戻る。取得済みの端末は保存版を表示し続ける。次の Studio の公開で行が作り直される（版は続きの番号） |
| R3（最後の手段） | Studio のデータが壊れた | 4.3 のバックアップから別 volume へ復元して判断する | 公開記録も戻り、次の公開の版がまた 1 から始まる。**取得済みの端末は自分の版以下を受け取らない**ため、端末が版 1 を取得した後では使えない |

R2 で使う所有者の DELETE は、`03-grant-delivery.sql` が「将来の削除（レビュー済みの手動手順）」のために残している権限で、配信を止めることだけに使う。実行前に承認を記録する。

## 7. リハーサルの記録（2026-11-15）

証跡: [docs/evidence/first-bundle-publish/2026-11-15/](evidence/first-bundle-publish/2026-11-15/)（入力の commit・イメージ・SQL は `00-manifest.txt`）。**隔離環境での成功であり、本番適用の根拠ではない。**

### 7.1 DB（本番と同じ順序）

PG17.11（`postgres@sha256:b0f9560a…`）の新しい volume に、[pg17-issue-8-production-apply.md](pg17-issue-8-production-apply.md) 5.2〜5.4節のコマンドどおり B1〜B4・C1〜C6 を適用した（`rehearsal/rehearse-db.sh`）。入力の SHA-256 は本番の `SHA256SUMS` と一致し、Studio の移行 SQL は本番と同じ CI artifact（`c6df200b…`）。B4・C6 は本番の期待値と完全に一致し、`delivery_rows|0`（`01-db-build.txt`）。

### 7.2 公開と配信

- public-api（`f42d9da` を手元でビルド）と Caddy（`Caddyfile.pg17-api`、`api.localhost` の内部 CA）で、公開前は本番と同じ 404（`02-before-publish.txt`）。
- `compose.studio-oneoff-publish.yml` で Studio（`165d468`、Production、`nexus_studio_app`）を起動し、CLI で管理者を登録、Caddy（`tls internal`、loopback）経由で API からログイン → カテゴリ2件・開催回1件（架空のリハーサル用の値）を保存 → 公開確認（阻止0・警告0）→ 公開。Release 1、同じ操作 ID の再送で新しい Release ができないことも確認（`03-publish-first.txt`）。オーナーは同じ操作を画面で行う。
- bundle 1: schema 2.1.0、669 バイト、`spots 0`・`map null`・`categories 2`・`occurrences 1`・`events 0`（`bundle-v1.json`）。HTTP 確認 18項目すべて合格（`04-http-checks-v1.txt`）。
- 開催日の追加を公開して版 2（ポインタの前進）。版 2 でも全項目合格し、版 1 の ETag では 200（`05`・`06`）。

### 7.3 #17 の権限

- 公開済みの行に対して19項目すべて合格（`07-permission-checks.txt`）。`nexus_public` は2表の SELECT だけ。`nexus_studio_app` は UPDATE・DELETE・TRUNCATE（42501）、後退（23514、トリガー）、2行目（23505）、存在しない版（23503）、トリガーの無効化（42501）が拒否され、INSERT と前進は成功（ROLLBACK）。所有者の後退も 23514。前後で状態は不変。
- 本番用の `scripts/first-bundle-db-checks.sh`（全 probe を ROLLBACK）でも全項目合格（`12-first-bundle-db-checks.txt`）。`monitor-delivery-size.sql` も動いた（`08-monitor.txt`）。

### 7.4 切り戻しと iOS

- R1: 開催回の取り下げを公開して版 3（`occurrences 0`）（`09-rollback-forward-fix.txt`）。
- R2: 所有者でポインタ行だけを削除 → 404 → 次の Studio の公開で行が作り直され版 4 で 200（`10-rollback-stop.txt`）。
- iOS（develop `43a2dbd`、シミュレーター iPhone 17、Caddy の内部 CA をシミュレーターに登録、Local.xcconfig で接続）: 版 4 を取得・保存（`pointer.json` の `current: v4`）。マップは「表示できる建物・地点がありません」（`ios-map-v4.png`）。**ホームは「この後のイベントはありません／公開されているイベントは、すべて終了しています。」と表示した**（`ios-home-v4.png`）。イベントが1件も公開されていない A の bundle では事実と違う文言になる。A を選ぶ場合は、公開前に NexusMobile 側で文言を分けるかを判断する。

### 7.5 本番と違う点・確かめていないこと

- 実行環境は colima（linux/arm64）。イメージは手元のネイティブビルド。採用済み Caddy の amd64 digest（`040e9f74…`）はエミュレーション下で起動時・稼働中に落ちた（`caddy-amd64-emulation-crash-head.txt`）ため、同じ v2.11.4 の arm64 版（`0c994536…`）で行った。2026-10-25 の I4 では同じ digest が動いており、VPS ではネイティブ実行になる。
- Mac での `--platform linux/amd64` のビルドは、リハーサル環境のビルダー（legacy builder）で失敗した。このため 4.4 は VPS でのビルドにした。VPS でのビルドそのものは未確認。
- `git archive` からのビルドは、リハーサルで使った studio-api と同じイメージ ID になった（`11-image-build-from-archive.txt`）。
- 画面からの操作（studio-web）は起動と 200 応答だけを確かめ、公開は API で行った。
- 本番の Caddy、実機の iPhone、ACME 証明書、VPS のディスク・メモリは未確認（5節で確かめる）。

### 7.6 2026-11-16 の再リハーサル（A：過去の開催回だけ → 地図ありの版で置き換え）

証跡: [docs/evidence/first-bundle-publish/2026-11-16/](evidence/first-bundle-publish/2026-11-16/)。DB は 7.1節と同じスクリプト・入力で作り直した（`01-db-build.txt`、`delivery_rows|0`）。

1. **A（版 1）**: 一時 Studio で 2.1節の提案どおりに開催回だけを公開（名称は提案のまま、2026-11-14 10:00〜13:30）。公開確認は**阻止 0・警告 0**（過去の日付でも警告なし）、Release 1（`03-publish-a.txt`）。bundle は 504 バイト、`spots 0`・`map null`・`categories 0`・`occurrences 1`・`events 0`（`bundle-a.json`）。HTTP 確認は合格（gzip・zstd は最小長未満で圧縮されず `NOTE`）（`04-http-checks-a.txt`）。
2. **iOS（版 1）**: ホームは開催日カード「11月14日（土）10:00〜13:30／終了／この日の公開は終了しました」と「この後のイベントはありません」。地図タブは「表示できる建物・地点がありません」（`ios-a-home-{light,dark}.png`・`ios-a-map-{light,dark}.png`）。
3. **地図ありの版（版 2）**: Studio の試験用の地図（`map-dataset-geojson-v2.example.json`、`MapPublishingTests` の試験用の承認済み設定。キャンパスのデータではない）を `map import` で取り込み、Spot 2件（`reference import`）・カテゴリ1件・架空の未来の開催回とイベント1件を、地図と一緒に1回で公開した。初回は「説明」未入力の公開阻止（`description_required`）で止まり、入力後に公開できた。警告は `bundle_grew` だけ（`05`・`06`）。HTTP 確認は全項目合格し、7,043 バイトなので gzip・zstd の接尾辞付き ETag と 304 も確認できた（`07`）。DB 確認も全項目合格（`08-db-checks.txt`）。
4. **iOS（版 2）**: アプリの再起動で版 2 に切り替わった（`current: v2`、`previous: v1`）。ホームは未来の開催日（開催前）とイベントを表示し、過去の開催日は出さない。地図は建物を描画した（`ios-map-stage-*-{light,dark}.png`）。

シミュレーターには先のリハーサルで保存した版 4 が残っていたため、アプリを消してから入れ直した。iOS は自分の版より低い版を受け取らないので、試験の端末を本番に向けるときも、別の環境で取得した版が残っていないかを確かめる（本番の版は 1 から始まる）。
