# Studio を `studio.nexus-app.jp` で常設する手順（#9、#8 の Studio 認証）

この文書は、NexusStudio（管理画面）を本番の PG17 edge に常設するための設計・承認・手順・確認・切り戻しの正本です。本番の操作はすべてオーナーが行います。Claude はコマンドを示し、結果を確かめます。

**2026-11-16 に本番へ適用しました**（記録は 12節）。隔離環境での確認は 11節にあります。

## 0. いまの状態と、この手順で変わること

いまの状態（2026-11-16）:

- VPS では Caddy が1つ（compose project `nexus-edge-pg17`）だけ動いていて、`www.nexus-app.jp`（静的サイト）と `api.nexus-app.jp`（public-api）を返しています。DB は PG17.11 です。
- DB には `studio`・`delivery` スキーマと、ロール `nexus_studio_migrator`・`nexus_studio_app` があります（移行 16件、#8 承認 C）。
- 参加者 bundle の Release #1 が公開済みです。そのときの一時 Studio は削除済みで、イメージ（NexusStudio `165d468`、VPS でネイティブビルド）と管理者1人が残っています。
- `studio.nexus-app.jp` の A レコードは既にあり、www と同じホストを指しています（2026-11-16 に Mac から確認。値は記録しない）。

この手順で変わること:

- 同じ Caddy に `studio.nexus-app.jp` のサイトを足します。2つ目の Caddy は起動しません。
- `studio-api` と `studio-web` を、同じ project に常駐させます（`restart: unless-stopped`）。
- DB のスキーマ・ロール・移行は**変えません**。Studio を使うと、`studio` の表（下書き・操作ログ）と、公開したときだけ `delivery` に行が増えます。

## 1. 構成

```text
Internet ──443/80──▶ caddy ──edge（internal、既存）──▶ public-api ──backend──▶ PG17
                       │
                       └──studio（internal、新規）──▶ studio-web（SPA）
                                                  └─▶ studio-api ──backend──▶ PG17
```

- `studio.nexus-app.jp` の `/api/*` は `studio-api:8080` へ、それ以外は `studio-web:8080` へ中継します。パスはそのまま渡します（`handle`。`handle_path` ではない）。`studio-web` は直接の URL（例 `/releases/history`）に `index.html` を返します。
- Compose の profile では Caddy のサイトを出し分けできないため、**Caddy の設定ファイルを差し替える**方式にしました。
  - Studio なし: `compose.pg17-edge.yaml` だけ（`Caddyfile.pg17-api` を読む）。今と同じ。
  - Studio あり: `compose.pg17-edge.yaml` に `compose.pg17-studio.yaml` を重ねる（`Caddyfile.pg17-with-studio` を読む）。
- `Caddyfile.pg17-with-studio` は、`Caddyfile.pg17-api` と一字一句同じ内容に、Studio のサイトを1つ足しただけのものです。`scripts/validate-pg17-studio-edge.py` がこれを検査します。www・api の設定と、public-api の CORS（`https://www.nexus-app.jp` の `GET /health` だけ）は変わりません。

### 1.1 信頼する範囲（`ReverseProxy__TrustedNetwork`）

DEC-1 では「Studio を足す前に信頼範囲を決め直す」としていました。今回は **network を分ける** 方式にしました。

- Studio 用に internal network `studio` を新しく作り、ここには `caddy`・`studio-api`・`studio-web` だけが入ります。`studio-api` の `ReverseProxy__TrustedNetwork` は、この network の subnet（`PG17_STUDIO_SUBNET`）と同じ値です。
- 既存の `edge`（Caddy と public-api だけ、DEC-1）には Studio を入れません。public-api の `RateLimiting__KnownNetworks__0` も変わりません。
- `studio-api` が信頼するのは `X-Forwarded-Proto` だけで、それも `studio` の subnet から来たときだけです。Caddy には `trusted_proxies` を設定しないので、利用者が送った `X-Forwarded-*` は Caddy が捨てて、自分の値で付け直します。
- validator は、`edge` の参加者が `caddy`・`public-api` だけ、`studio` の参加者が `caddy`・`studio-api`・`studio-web` だけ、2つの subnet が重ならないこと、TrustedNetwork が `studio` の subnet と同じことを検査します。

### 1.2 HSTS の `includeSubDomains`

**付けません（今のまま）。** 理由:

- HSTS の `includeSubDomains` は、ヘッダーを返したホスト**の下**のサブドメインにだけ効きます。`www.nexus-app.jp` や `studio.nexus-app.jp` で付けても、兄弟の `api`・`studio` には効きません。全体に効かせるには、`nexus-app.jp`（apex）が HTTPS で返す必要がありますが、apex はこの Caddy で返していません。
- 3つのホストは、どれも自分で `max-age=31536000` を返しています。Studio も同じです。
- 付けるかどうかは、apex を HTTPS で返すときに決め直します（preload もそのとき）。

### 1.3 Studio の認証（#8 の残り）

- 管理者ごとのアカウントで、同じオリジンの HttpOnly Cookie と CSRF トークンを使います（NexusStudio 15 v01 §3）。
- Cookie は `nexus_studio_session`・`nexus_studio_csrf`。どちらも `Secure; HttpOnly; SameSite=Strict`（Production では常に Secure）。セッションは 8時間で、使うたびに延びます（決定事項30 S1）。
- 状態を変える `/api` の要求には `X-XSRF-TOKEN` が必要です。無いと 400 `csrf_invalid`。
- パスワードは 12文字以上（長さを重視、NIST SP 800-63B）。5回まちがえると 15分ロックされます。ログイン失敗の理由は応答では区別せず、操作ログにだけ残ります。
- 無効化・パスワード再設定は、既存のセッションの次の要求ですぐ効きます。
- Cookie を守る鍵（DataProtection）は volume `nexus-edge-pg17_studio_dataprotection_keys` に置きます。再起動してもログインは切れません。この volume は消さないでください（消しても全員の再ログインが要るだけで、データは失われません）。鍵は平文の XML なので、バックアップするなら暗号化します。
- Studio API 自体には CORS がありません（同じオリジンだけ）。

## 2. オーナーの判断（2026-11-16）

| 項目 | 判断 | 補足 |
|---|---|---|
| ログインできる人 | いまは管理者1人。あとで増やす | 増やす手順は 8節 |
| ログインの前の追加の守り（basic 認証・IP 制限） | **付けない** | 残るリスクは 10節 |
| 動かす時間 | **常時** | `restart: unless-stopped` |
| イメージ | **VPS にある `165d468` のネイティブビルド** | api `cb2e9cbf…05fb`、web `ade96b9f…8e42`。GHCR は別の作業 |
| DNS | `studio` の A レコードは既にある | 承認不要。4節 S-0 で確かめる |
| TrustedNetwork | `studio` 用の別 network と subnet | 1.1節（DEC-1 の決め直し） |
| HSTS `includeSubDomains` | 付けない | 1.2節 |

## 3. 前提

すべて満たしてから始めます。

- この PR がマージされている。VPS の edge の専用ディレクトリに、マージ後の `compose.pg17-studio.yaml`・`Caddyfile.pg17-with-studio`・`scripts/validate-pg17-studio-edge.py`・`scripts/validate-pg17-edge.py`・`sql/pg17-baseline/studio-runtime-privileges.sql` を置く（`compose.pg17-edge.yaml`・`Caddyfile.pg17-api` は今のものと同じであること。7.1 で SHA-256 を照合する）。
- www と api が正常（`https://api.nexus-app.jp/health` が 200）。
- VPS に `nexus-studio-api:165d468`・`nexus-studio-web:165d468` が残っていて、イメージ ID が上の値と合う。
- `apps/studio-api/Migrations` が本番の DB と同じ（`165d468` は 16件で、本番と一致）。違うイメージを使うときは止める。

## 4. 承認

オーナーが記録します。1つの承認は他に広げません。

| # | 承認すること | 影響 | 戻し方 |
|---|---|---|---|
| S-0 | 事前確認（読み取りだけ） | なし | — |
| S-1 | 設定ファイルと接続情報の配置 | ファイルが増えるだけ。動いているものは変わらない | ファイルを消す |
| S-2 | `studio-api`・`studio-web` の起動 | コンテナ2つと network `studio`、volume 1つが増える。まだ外からは見えない | 9節 R2 |
| S-3 | Caddy の設定の差し替え（**公開**） | Caddy が作り直され、www・api が数秒止まる。`studio.nexus-app.jp` の証明書を取り、公開される | 9節 R1 |

S-3 の「数秒」は、Caddy のコンテナを作り直すあいだです（隔離環境の測定で約1〜2秒、`03-caddy-swap-gap.txt`）。証明書と ACME のアカウントは volume に残るので、www・api の証明書は取り直しません。

## 5. 手順

表記: `<…>` は私有メモの値に置き換えます（`<` `>` は付けない）。compose のコマンドには必ず `-f` で PG17 のファイルを指定します（`$BASE`・`$DC`）。`-p` だけでは設定が読まれず、ディレクトリにある既定の `compose.yaml`（PG18 用）を探してしまいます。実値（IP、パス、container 名、パスワード）は Git・Issue・会話に書きません。VPS では root のシェルで実行します。

### 5.0 S-0 事前確認（読み取りだけ）

Mac:

```sh
dig +short studio.nexus-app.jp A; dig +short www.nexus-app.jp A    # 同じ値（値は貼らない。「同じ」とだけ伝える）
curl -s -o /dev/null -w '%{http_code}\n' https://api.nexus-app.jp/health   # 200
```

VPS（edge の専用ディレクトリで）:

```sh
cd <edge のディレクトリ>
set -a; . /etc/nexus/pg17-edge.env; set +a
BASE="docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml"   # -f を必ず付ける（無いと既定の compose.yaml を探す）
sha256sum compose.pg17-edge.yaml Caddyfile.pg17-api compose.pg17-studio.yaml Caddyfile.pg17-with-studio   # 7.1 の値と一致
$BASE ps --format '{{.Service}} {{.Status}}'      # public-api healthy、caddy healthy の2つだけ
docker image inspect -f '{{.Id}} {{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}' nexus-studio-api:165d468 nexus-studio-web:165d468
docker network inspect $(docker network ls -q) --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'   # 使っていない /28 を選ぶ
docker inspect --format '{{.Config.Image}} {{.RestartCount}}' $($BASE ps -q caddy)   # 戻すとき用に私有メモへ
```

- 期待値: イメージ ID が `sha256:cb2e9cbf…05fb`（api）・`sha256:ade96b9f…8e42`（web）、`amd64`、revision `165d4687d5b8…`。
- `PG17_STUDIO_SUBNET` には、既存のどれとも重ならない private の /28 を選びます（例: `PG17_EDGE_SUBNET` の隣）。一時 Studio の subnet を再利用してもかまいません（その network は削除済み）。
- 止める条件: DNS が違う、api が 200 でない、イメージが無い・ID が違う、ファイルの SHA-256 が違う。

### 5.1 S-1 設定ファイルと接続情報の配置

DB の alias と、`nexus_studio_app` のパスワードのファイルの場所は、11/16 の公開と同じです（`docs/first-bundle-publish.md` 4.5節。表示せずに変数へ入れる）。

```sh
S=$(dirname "$(find / -xdev -name nexus_studio_app_password -type f 2>/dev/null | head -1)"); ls "$S" | grep -c '_password$'   # 3
install -d -m 0711 /etc/nexus/pg17-studio-secrets
( umask 077; printf 'Host=<DB の alias>;Port=5432;Database=nexus;Username=nexus_studio_app;Password=%s' \
    "$(cat "$S/nexus_studio_app_password")" > /etc/nexus/pg17-studio-secrets/studio_connection )
chown 1654 /etc/nexus/pg17-studio-secrets/studio_connection && chmod 0400 /etc/nexus/pg17-studio-secrets/studio_connection   # APP_UID=1654
( umask 077; cat > /etc/nexus/pg17-studio.env <<'EOF'
PG17_STUDIO_API_IMAGE=<5.0 の api のイメージ ID（sha256:… 64桁）>
PG17_STUDIO_WEB_IMAGE=<5.0 の web のイメージ ID（sha256:… 64桁）>
PG17_STUDIO_DOMAIN=studio.nexus-app.jp
PG17_STUDIO_SUBNET=<5.0 で選んだ /28>
PG17_STUDIO_SECRET_DIR=/etc/nexus/pg17-studio-secrets
EOF
)
set -a; . /etc/nexus/pg17-edge.env; . /etc/nexus/pg17-studio.env; set +a
BASE="docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml"
DC="$BASE -f compose.pg17-studio.yaml"
umask 077; r=$(mktemp)
$DC config --format json > "$r"
python3 scripts/validate-pg17-studio-edge.py --resolved "$r" --env && python3 scripts/validate-pg17-edge.py --env
$BASE config --format json > "$r" && python3 scripts/validate-pg17-edge.py --resolved "$r"   # 戻し先も合格すること
rm "$r"
```

- 期待値: `PG17 Studio edge checks passed` と `PG17 edge checks passed` が2回。
- 展開した JSON には機微な値があるので、画面に出さず、貼りません。
- 同じシェルで 5.2 以降を続けます（`BASE`・`DC` と環境変数を使う）。シェルを開き直したら `cd`・`set -a …`・`BASE=`・`DC=` をもう一度。

### 5.2 S-2 Studio サービスの起動（まだ公開しない）

```sh
$DC up -d --no-deps studio-api studio-web
$DC ps --format '{{.Service}} {{.Status}}'          # 30秒ほどで studio-api・studio-web が healthy
$DC logs --tail 20 studio-api   # エラーが無いこと（接続文字列は出ない）
docker exec -i --user postgres <PG17_CONTAINER> psql -X -At -U nexus -d nexus < sql/pg17-baseline/studio-runtime-privileges.sql
```

- 期待値: 最後の SQL の全行が `|t` で終わり、最後の行が `studio_connections|nexus_studio_app|t`。
- `public-api` と `caddy` は作り直されません（`--no-deps` で、2つだけを起動する）。
- 止める条件: healthy にならない（`logs` を見る。直せないときは R2）。

### 5.3 S-3 Caddy の設定の差し替え（公開）

```sh
$DC up -d --no-deps caddy
$DC ps --format '{{.Service}} {{.Status}}'          # caddy が healthy（1分ほどかかる）
$DC logs --tail 50 caddy | grep -E '"level":"error"|certificate obtained'
```

- 期待値: `studio.nexus-app.jp` の `certificate obtained successfully` が出る。www・api の証明書は取り直さない。
- 止める条件: Caddy が起動しない・healthy にならない、www か api が 200 にならない → すぐ R1。

## 6. 確認

### 6.1 Caddy 経由（Mac、読み取り）

```sh
STUDIO=https://studio.nexus-app.jp API=https://api.nexus-app.jp SITE=https://www.nexus-app.jp sh scripts/studio-edge-http-checks.sh
```

- 期待値: `RESULT: 0 failure(s)`。www と api（CORS が www の `/health` だけ）、Studio の画面と直接の URL、`/api/auth/csrf` の 200 と Secure Cookie、偽装した `X-Forwarded-*` を無視すること、別オリジンに CORS が無いこと、CSRF なしのログインが 400 になることを確かめます。
- 出力にはホスト名しか出ませんが、貼るのは `PASS`/`FAIL` の行だけにします。

### 6.2 ブラウザ（オーナー）

1. Chrome で `https://studio.nexus-app.jp` を開き、証明書の警告が出ないこと。
2. 既存の管理者でログインし、画面が出ること。開発者ツールの Application → Cookies で、`nexus_studio_session` に Secure・HttpOnly・SameSite=Strict が付いていること。
3. アドレスバーに直接 `https://studio.nexus-app.jp/releases/history` を入れて再読み込みし、画面が出ること。
4. 「公開履歴」に Release 1 があること。**この確認では公開しない。**
5. ログアウトし、再読み込みでログイン画面に戻ること。

### 6.3 DB とコンテナ（VPS、読み取り）

```sh
docker exec -i --user postgres <PG17_CONTAINER> psql -X -At -U nexus -d nexus < sql/pg17-baseline/studio-runtime-privileges.sql   # 5.2 と同じ
for n in edge studio; do docker network inspect nexus-edge-pg17_$n --format '{{range .Containers}}{{.Name}} {{end}}'; echo; done
docker port $($DC ps -q caddy)      # 80 と 443 だけ
```

- 期待値: `edge` には caddy と public-api だけ、`studio` には caddy・studio-api・studio-web だけ。studio-api・studio-web にはホストの公開ポートが無い。

### 6.4 記録

#9 にコメントします（この文書の 11節の形。IP・パス・メールアドレスは書かない）。

## 7. 参考値

### 7.1 ファイルの SHA-256

マージ後の `main` で `shasum -a 256` した値を、オーナーが VPS で照合します（マージで変わるので、ここには書きません。Mac で `git show origin/main:<ファイル> | shasum -a 256`）。

### 7.2 本番のイメージ

| | イメージ ID | 出所 |
|---|---|---|
| studio-api | `sha256:cb2e9cbf…05fb` | NexusStudio `165d468` を VPS でネイティブビルド（amd64、2026-11-16） |
| studio-web | `sha256:ade96b9f…8e42` | 同上 |

## 8. 管理者を増やす・止める

管理者の操作は Studio の CLI で行います（画面には無い）。Web サーバーは起動しません。

```sh
$DC run --rm --no-deps studio-api admin list
$DC run --rm --no-deps studio-api admin add --email <メールアドレス> --name <表示名>   # パスワードを2回入力（引数・履歴に残らない）
$DC run --rm --no-deps studio-api admin disable --email <メールアドレス>              # 既存のログインもすぐ切れる
$DC run --rm --no-deps studio-api admin enable --email <メールアドレス>               # 無効化・ロックアウトの解除
$DC run --rm --no-deps studio-api admin reset-password --email <メールアドレス>
```

- パスワードは、パスワード管理アプリで作った 16文字以上を勧めます（Studio の最小は 12文字）。共有しません。人ごとにアカウントを作ります。
- 人を増やすとき・やめた人がいるときは、#9 か専用の Issue に「誰を（名前は書かない。役割だけ）、いつ」を残します。

## 9. 切り戻し

**順番を守ります。先に Caddy を戻して www・api を確かめ、そのあとで Studio を止めます。** DB の移行を逆に流すこと、volume を消すこと（`down -v`、`volume rm`）はしません。

### R1 Caddy を Studio の前の設定に戻す

```sh
cd <edge のディレクトリ>; set -a; . /etc/nexus/pg17-edge.env; set +a
docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml up -d --no-deps caddy
docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml ps --format '{{.Service}} {{.Status}}'
```

- Studio のコンテナが「orphan」と警告されますが、`--remove-orphans` は**付けません**。
- Mac で確認: `EXPECT_STUDIO=absent STUDIO=https://studio.nexus-app.jp API=https://api.nexus-app.jp SITE=https://www.nexus-app.jp sh scripts/studio-edge-http-checks.sh` が `RESULT: 0 failure(s)`（www・api は今までどおり、Studio は返らない）。

### R2 Studio のサービスだけを止める

R1 で www・api が正常なことを確かめてから:

```sh
set -a; . /etc/nexus/pg17-edge.env; . /etc/nexus/pg17-studio.env; set +a
docker compose -p nexus-edge-pg17 -f compose.pg17-edge.yaml -f compose.pg17-studio.yaml stop studio-api studio-web
```

- `stop` だけにします。コンテナ・network `studio`・DataProtection の volume は残ります。また公開するときは 5.2 から。
- 完全に片付けるのは、Studio をやめると決めたときだけです（別の承認）: `... rm studio-api studio-web`、`docker network rm nexus-edge-pg17_studio`。volume は消さずに残します。

### 公開した bundle について

Studio を止めても、配信中の bundle（`/api/v2/bundle`）は変わりません。公開の切り戻しは `docs/first-bundle-publish.md` 6節（ポインタは戻せない。訂正は新しい版で）。

## 10. 残るリスクと、あとで決めること

- **ログインの前の守りが無い**（オーナー判断）。Studio のログイン画面はインターネットのだれでも開けます。守りはアカウントごとのロックアウト（5回で15分）だけで、IP ごとの回数制限はありません（Caddy の標準には rate limit が無い）。総当たりは遅くできますが、止められません。長いパスワードを使い、操作ログの「ログイン失敗」を時々見てください。増えたら basic 認証か IP 制限を足します（Caddyfile の Studio のサイトに数行。この文書の改訂と validator の更新が要る）。
- 常時稼働なので、Studio の脆弱性が見つかったら、イメージを作り直して入れ替える必要があります（下）。
- `studio-api` は DB のために `backend` にも入るので、同じ network の public-api から `studio-api:8080` に届きます。そこからの要求は信頼範囲の外なので、転送ヘッダーは使われず、ログインも要ります。
- `GET https://studio.nexus-app.jp/health` は studio-web の `ok` です（studio-api の `/health` は中継しない）。
- **イメージの更新**: 新しい commit を使うときは、`apps/studio-api/Migrations` が本番と同じことを確かめ（違えば DB の移行の承認が先）、11/16 と同じ方法でビルドし、`/etc/nexus/pg17-studio.env` のイメージ ID を替えて `$DC up -d --no-deps studio-api studio-web`。Caddy は作り直しません。GHCR に置く仕組みは別の作業（NexusStudio の CI にはまだ無い）。
- 地図を公開するには、`MapValidation__ApprovedSettingsFile`（承認済みの設定ファイル）を Studio に渡す追加の変更が要ります（NexusStudio `deploy/README.md`）。今回の compose には入れていません。

## 11. 隔離環境での確認（2026-11-16）

証跡は [`docs/evidence/issue-9/2026-11-16/`](evidence/issue-9/2026-11-16/) にあります。**本番には何もしていません。**

- 環境: colima（arm64）の使い捨て PG17.11。本番と同じ順（B1〜B4、C1〜C6）・同じ入力（SHA-256 一致）で DB を作り、この PR の compose・Caddyfile をそのまま使った。Studio は `165d468` を arm64 でビルド、public-api は NexusMobile `f42d9da`、Caddy は v2.11.4（arm64）。名前は `*.localhost` にして、Caddy の内部 CA で TLS を検証した（ACME は使わない）。
- 流れ: 今の本番の形（Studio なし）→ S-1 → S-2 → S-3 → 確認 → R1 → R2 → もう一度 S-2・S-3（`rehearse-flow.sh`、結果は `02-flow.txt`）。

| 確認 | 結果 |
|---|---|
| validator（合成した構成・戻し先・環境変数） | 合格。違いはローカルの public-api がレジストリの digest でないことだけ |
| S-2 で caddy・public-api が作り直されない／S-3・R1 で public-api が作り直されない | 合格 |
| network の参加者 | `edge` = caddy・public-api、`studio` = caddy・studio-api・studio-web。ホストに出るのは caddy の 80/443 だけ |
| www・api（差し替えの前・後・戻した後・再適用の後） | すべて `www /` 200、`api /health` 200、CORS は www の `/health` だけ（Studio のオリジンや bundle には付かない） |
| Studio の画面・直接の URL・アセット・http→https | 200、`/releases/history` は `index.html` と同じ、JS は javascript、308 |
| `/api/auth/csrf` | 200、`nexus_studio_csrf` は Secure・HttpOnly・SameSite=Strict、`Cache-Control: no-store` |
| 偽装した `X-Forwarded-Proto/Host/For`・`Forwarded` | Caddy 経由では無視（200、Cookie は Secure のまま）。信頼範囲の外（backend）から studio-api へ直接 `X-Forwarded-Proto: https` を送ると 500（「SSL でない要求」として拒否）、`studio` subnet からだけ 200。インターネット側の `public` network からは studio-api に届かない |
| CORS・CSRF | 別オリジンに `Access-Control-Allow-Origin` なし。CSRF トークンなしのログインは 400 `csrf_invalid` |
| ログイン・ログアウト | 誤りは 401、正しいと 204 で `nexus_studio_session` が Secure・HttpOnly・SameSite=Strict、`/me` 200、ログアウト 204 の後 `/me` 401 |
| studio-api の再起動 | 再起動の前の Cookie で `/me` 200（DataProtection の鍵が volume に残る。ディレクトリは 0700） |
| DB の最小権限（`studio-runtime-privileges.sql`） | 全項目 `t`。Studio の接続は `nexus_studio_app` だけ。レビュー後に、必要な権限を1つずつ確かめる形に直し、ポインタの SELECT・INSERT・UPDATE の行を足した（権限を外すと `f` になることを確認、`04-review-privilege-check.txt`） |
| R1・R2 | Caddy を戻すと Studio は返らず、www・api はそのまま。Studio を止めても volume・移行履歴（16）・管理者（1）・配信（0）は変わらない |
| 差し替えの停止時間 | 約1〜2秒（どちらの向きも 0.1秒おきの確認で 3回だけ失敗） |

本番と違う点: CPU が arm64、Caddy の digest が違う（採用 amd64 の digest はエミュレーションで落ちる）、ACME を使わない（本番では S-3 で `studio.nexus-app.jp` の証明書を取る）、studio-api のビルドで apt の再試行を足した（ミラーの不調のため。アプリは同じ commit）。

## 12. 本番の記録（2026-11-16）

オーナーが S-0〜S-3 を本番で行い、Claude はコマンドを示して結果を確かめた。外からの確認は Mac から行った。

- **承認**: S-1・S-2・S-3 は、それぞれの実行の前にオーナーが承認した。S-0 は読み取りだけで、PR #30 のマージ後に「適用へ」の指示を受けて行った。
- **S-0**: 送付した 7ファイルの SHA-256 はすべて一致した。edge の既存の `compose.pg17-edge.yaml`（`83d107b9…e558`）と `Caddyfile.pg17-api`（`66b34536…e174`）は main と同じだった。caddy と public-api は healthy で、caddy の再起動は 0回。イメージは api `sha256:cb2e9cbf…05fb`・web `sha256:ade96b9f…8e42`（amd64、revision `165d4687…`）。DNS の `studio` は www と同じホストを指していた。
- **S-1**: Studio 用のファイルを置いた（既存のファイルは上書きしていない）。Studio の subnet は、空いている `/28` から自動で選んだ。DB の接続先は public-api の接続情報と同じホストにした。`studio_connection` は UID 1654・0400、`pg17-studio.env` は root・0600。validator は3回とも合格した（合成した構成と環境変数、切り戻し先）。
- **S-2**: studio-api と studio-web は約30秒で healthy になった。caddy と public-api は作り直されていない（稼働時間が続いていた）。ログの `warn` は HTTPS リダイレクトのポートが決まらないという1行だけで、これは想定どおり（HTTPS は Caddy が終端する）。`studio-runtime-privileges.sql` は全行 `t`。`edge` の参加者は caddy と public-api、`studio` の参加者は studio-api と studio-web だった。
- **S-3**: caddy を作り直し、healthy になった。public-api は作り直されていない。`studio.nexus-app.jp` の証明書を取得した（Let's Encrypt、期限は 2027-02-14）。差し替えたあと、`studio` の参加者に caddy が加わった。ホストに出ているのは 80/443 だけ。
- **6.1（Mac）**: `studio-edge-http-checks.sh` は `RESULT: 0 failure(s)`。www と api は今までどおりで、CORS は www の `/health` だけ。配信中の bundle は版 1 のまま（ETag `sha256-a010e161…9171`）で、変わっていない。
- **6.2（オーナー、Chrome）**: 6項目すべて OK。証明書の警告は無く、ログインできた。`nexus_studio_session` は Secure・HttpOnly・SameSite=Strict。直接の URL も開け、公開履歴に Release 1 があり、ログアウトもできた。公開はしていない。
- **確かめていないこと**: ログインしたあとの `studio_connections` の行（接続が `nexus_studio_app` だけであること）は、本番では確かめていない。隔離環境では確認済み（11節）。
- **残っているもの**: 送付に使ったファイルの写し（VPS のホームと `/root` の作業ディレクトリ、Mac の `~/nexus-studio-hosting-20261116`）。秘密は含まない。VPS には再起動を求める通知が出ているが、再起動は別に判断する（Studio は `restart: unless-stopped` なので、再起動のあと自動で戻る）。

