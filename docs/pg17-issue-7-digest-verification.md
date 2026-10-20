# Issue #7: 新採用digestのPG17隔離検証

2026-10-20 JST。**採用API/Caddyの実digestで、契約Bの隔離受け入れ条件のうち、検証用volumeの個別削除を除く全項目を確認した。本番未適用。** volumeの個別削除は人間の再確認待ちで未完了（[境界と後片付け](#境界と後片付け)）。 ローカルARM用にビルドした修正版の結果ではなく、Private registryから取得した発行済みAPIを検証した。

## 固定した入力

| 対象 | 値 |
| --- | --- |
| NexusMobile | `d3e20b13847ff973783670fe9bb4d133cc168a5b`（PR #99マージcommit） |
| API | `ghcr.io/m0o9l0v3/nexus-public-api-pg17-private@sha256:847b6d0f20902dbe420da0e59fa955219e979590d80ec010d3dac833acb1e098` |
| Caddy | `caddy@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0`、v2.11.4 |
| PostgreSQL | `postgres@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`、17.11 |
| 移行SQL SHA256 | `7cf58c307611ebec07e617add3f18d7fa65eee30c6de16dc2d989d5e4a9da763` |
| 隔離DB project / volume | `nexus-pg17-edge-iso-issue7-d3-20261020` / `nexus-pg17-edge-iso-issue7-d3-20261020-db` |
| edge project | `nexus-edge-pg17` |

[発行証跡](evidence/issue-7/2026-10-20/public-api-republication.json)と[実イメージ・secret検査](evidence/issue-7/2026-10-20/d3e20b1/image-and-secrets.json)で、APIのOCI revision、`linux/amd64`、実行UID1654を照合した。Caddyもlinux/amd64。Macのengineはlinux/arm64であり、両実イメージを互換実行した。VPSでのネイティブ実行を証明するものではない。

ソースはマージcommitのarchiveを使用。移行生成ツール・admin-apiのschema/migration・grant SQLは旧候補から変更がないことをGit差分で確認し、レビュー済み移行SQLを再利用した。新ソースでmanifest検査を通し、[移行2本とSQL hash](evidence/issue-7/2026-10-20/d3e20b1/manifest.json)を固定した。archiveに`.git`はないため、manifestのcommit欄はarchive元のSHAを明示して補った。

## 条件ごとの結果

| Issue #7の条件 | 結果と根拠 |
| --- | --- |
| 非特権・所有者非所属・schema CREATEなし・移行履歴2本 | 成功。SQL述語と実APIのProduction readiness・起動を確認 |
| 公開spots/eventsだけSELECT、管理表・将来表の拒否 | 成功。公開/非公開各1行、管理用10表、将来表で確認 |
| visit_logs INSERTと指定3列SELECTだけ許可 | 成功。全14列を個別検査。指定3列以外はSQLSTATE 42501 |
| visit_logs UPDATE/DELETE/TRUNCATE拒否 | 成功、42501 |
| spots/eventsへの書込み、移行履歴の更新拒否 | 成功。移行履歴INSERT/UPDATE/DELETE/TRUNCATEも42501 |
| DDL・CREATE ROLE・SET ROLE・COPY PROGRAM拒否 | 成功、42501 |
| 起動時migration/seedなし | 成功。採用ソースの静的検査、非特権での起動、起動前後の表名・履歴・fixture行数不変を確認 |
| 特権接続で起動拒否 | 成功。`nexus`接続はreadinessより前の専用login設定検査で拒否。専用loginを特権化した場合も実DatabaseReadinessで拒否 |
| Caddy内部TLSでhealth・代表GETが200 | 成功。公開fixtureが各1件返ることも確認 |
| ログPOST 202・永続化・hash連鎖 | 成功。2回のPOST後に各1行増加。2行目prev_hashが1行目hashと一致 |
| IP別制限・偽装ヘッダーによる回避不可 | 成功。通常の60回/分をAで使い切ると429、別IPのBには枠が残る。偽装XFFを3通り変更してもAは429。HTTPSへの偽装XFPで応答を変更できず、HTTPは308 |
| UID所有0400のsecretを読める | 成功。実コンテナでUID1654・owner1654・mode400。起動と実HMACログ保存で読み取りも確認 |
| API再起動後の再接続 | 成功。healthとDBを使う両GETが200。表名・履歴・行数不変 |
| Caddyはbackend非参加、host公開portは80/443のみ | 成功。edge参加者はCaddy/APIの2つだけ。テストクライアントはpublic側のみ |
| project指定で停止、down -v不使用、volumeは個別に削除 | **一部未完了。** edge→DBの順でproject指定のdownを行い、コンテナ残存なし・`down -v`不使用は成功。volumeの個別削除は未実施（人間の再確認待ち） |

DBの確認は既存baseline47項目に、全14列と移行履歴UPDATE/TRUNCATEの16項目を加えた。[DBと起動の記録](evidence/issue-7/2026-10-20/d3e20b1/baseline-and-startup.txt)、[HTTP12項目](evidence/issue-7/2026-10-20/d3e20b1/http-checks.txt)、[起動拒否など3項目](evidence/issue-7/2026-10-20/d3e20b1/startup-rejection.txt)を保存した。

特権接続の拒否段階は実装どおり上表に分けた。`nexus`でDatabaseReadinessが実行されたとは記録しない。専用roleの一時的なSUPERUSER化は使い捨てDB内のみで行い、NOSUPERUSERへの復帰を確認した。起動拒否用の補助コンテナは`--init --ulimit core=0`で実行し、互換実行時の例外終了処理を切り分けた。

## hash・転送ヘッダー・ブラウザの追加確認

[独立したHMAC計算](evidence/issue-7/2026-10-20/d3e20b1/independent-hmac.txt)でも2行のhashが一致した。ダミーaudit keyと採用ソースのcanonical形式を使った。PostgreSQLはmicrosecond精度のため、保存時に失われるcreatedAtの最後の100ns桁を0〜9で照合した。occurredAtは固定したfixture時刻である。

[APIが受け取った実ヘッダー](evidence/issue-7/2026-10-20/d3e20b1/forwarded-headers.json)も確認した。偽装XFFとXFP（http / ftp）を2回送り、CaddyからAPIの8080へ届くXFFが実クライアントIP、XFPがhttpsであることを確認した。診断用Python補助コンテナは使い捨てAPIのnetwork namespaceを共有し、port8080へのHTTPヘッダーのうち指定した2要求だけを記録した。edgeへのIP付き参加者はCaddy/APIの2つのままで、補助コンテナも削除済み。補足確認の再開時はComposeのhealth待機と後片付けが重なり、待機コマンドはNo such containerで終了した。受け入れ本体の起動チェックはそれ以前に終了コード0で完了しており、補足のヘッダー照合2件も成功後に停止した。

ブラウザでは未許可originからAPI応答を読めないことを確認した（[結果](evidence/issue-7/2026-10-20/d3e20b1/browser-result.txt)、[fixture](evidence/issue-7/2026-10-20/d3e20b1/browser-fixture.html)、[API応答](evidence/issue-7/2026-10-20/d3e20b1/browser-relay-results.json)）。

内部CAをOSへ登録したり、ブラウザの証明書警告を回避したりせず、loopback限定の検証用relayを使用した。relayはCaddyのCAを検証し、ブラウザのOriginとAPIのAccess-Control応答をそのまま中継する。same-originの200とcross-origin no-corsのopaque成功で疎通を確認した上で、corsモードだけTypeErrorで読めなくなることを確認した。API側はその要求へ200を返し、Allow-Originは付かなかった。これはCORSヘッダーとブラウザ制限の検証であり、ブラウザからapi.localhostへの直接TLS接続の検証ではない。

## 境界と後片付け

新規の空volumeとダミーfixture/password/keyだけを使用。起動前にlive container/volumeが存在しないことを確認し、既存の検証用DB・本番VPS・本番PG17・PG18構成は操作していない。

secretはコンテナ内の所有者・modeで検証した。ホスト側ではUID501、コンテナ内ではUID1654として観測されるため、ホストのstatだけで読取可否を判断していない。

[終了記録](evidence/issue-7/2026-10-20/d3e20b1/teardown.txt)のとおり、検証用relay・クライアントを終了し、edge、DBの順でprojectを指定してdownした。ローカル検証VMは元の停止状態へ戻した。

[§12の手順7](pg17-public-edge.md#12-隔離検証issue-7実digest入手後)と[受け入れ条件](pg17-baseline.md#4-issue-7-の受け入れ条件正式契約b)は、人間が対象を再確認した上で今回の隔離volumeを1個ずつ`docker volume rm`することを求める。今回はこの削除を行っていないため、後片付けの条件は未完了とする。削除対象は検証専用のColima profile `nexus-db-verify`（現在停止中）のengine上にある次のvolumeで、いずれもダミーデータのみを含む。

- 今回: `nexus-pg17-edge-iso-issue7-d3-20261020-db`
- 同日の旧digest・事前検証分（[初回検証記録](pg17-issue-7-verification.md)）: `nexus-pg17-edge-iso-issue7-20261020-db`、`nexus-pg17-baseline-issue7-20261020c-db`、およびDB起動前に失敗した試行で確保した別名volume

人間が同profileを起動し、Docker contextがそのengineのlocal Unix socketを指すことを確認した上で`docker volume ls`で名前を再確認し、本番名（`nexus_postgres_data`等）・別作業のvolumeでないことを確認してから、1個ずつ`docker volume rm <名前>`で削除し、終了後にprofileを停止状態へ戻し、削除日と結果をIssue #7へ記録する。この記録までIssue #7の後片付け条件は完了扱いにしない。

隔離での成功は、本番schemaの構築・VPSのpull資格・DBバックアップ/復元・本番デプロイの承認や成功を意味しない。これらは別の運用手順・承認範囲に残る。
