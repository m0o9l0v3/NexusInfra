# 稼働中のPostgreSQL 17から18へ移るための準備と停止条件

状態: 設計・レビュー用。**この文書とスクリプトを追加しても本番作業は開始しない。** 2026-09-24に人間から報告された現行VPSは`postgres:17-alpine` / PostgreSQL 17.11、named volume `nexus_postgres_data`を`/var/lib/postgresql/data`へマウントしている。バックアップとNexus用timerはまだない。DB名・全DB一覧・ユーザー表・ロール・書込元・CPUアーキテクチャは未確定。

次期`compose.yaml`は18系のpgBackRest派生イメージと**別の**DB/バックアップvolumeを使う。現行17のvolumeを次期Composeに指定しない。同じvolumeを18の`/var/lib/postgresql`へ付け替えない。旧Composeと新Composeを同じDB volumeに対して同時実行しない。

## Phase 0: 読み取り専用の現状記録

人間がコンテナ名、実イメージID、PostgreSQL版、DB一覧、各DBのschema/表/件数、role名と権限、現行アプリ接続、DBとvolumeの実サイズ、空き容量、既存バックアップ・timerを記録する。`docker inspect`全文や環境変数全文、パスワード・接続文字列は会話やIssueに貼らない。ユーザーデータがないことをvolumeサイズから推測しない。出力に秘密が含まれる場合は保護された運用記録へ保存する。

この記録がない間、バックアップ対象DB名も、18へ移すデータ範囲も確定しない。

## Phase 1: 17の復旧元を確保

`backup-pg17-source.sh`は現行コンテナのimage名、volume名、mount先を照合し、不一致なら停止する。人間が対象名とDBユーザー/DB名、root所有0700の保存先を設定して`ALLOW_PG17_EXPORT=1`を明示した時だけ、**読み取りのみ**の`pg_dumpall`（password hashなし）とNexus DBのcustom-format dumpを作る。元volumeを操作・削除しない。これは18用`nexus-backup`とは別の移行前バックアップである。

生成物には表データやrole情報が含まれる。Git、MR、CI成果物、会話へ載せない。SHA-256照合後、承認した暗号化方式で既存MacなどVPS外へコピーする。暗号鍵とコピーを別管理し、コピー先の保存完了時刻・サイズ・ハッシュを記録する。**ファイルを作っただけでは合格ではない。**

VPS外コピーから、元と同じ17系の隔離コンテナ・新規空volumeへ戻す。外部ポートは開けない。`globals.sql`は既存roleとの衝突と認証情報欠落をレビューし、盲目的に実行しない。DBごとにschema、件数、代表レコード、拡張、所有者・権限を照合する。Nexus以外のDBがあればそれらも復元対象に含める。元volumeの識別子と内容が変わっていないことを記録する。復元失敗時は切替計画へ進まない。

## Phase 2: 18の隔離リハーサル

Macにある成功コピーから、17と異なる新規空volume・別Composeプロジェクトに18系を構築する。18のDB image digest、CPUアーキテクチャ、`/var/lib/postgresql`マウントを固定する。4GB VPSで17と18を同時に走らせる場合は先にRAM/swap/diskの上限を実測し、可能ならMac等の隔離環境で先に試す。実VPSでの試験は別途承認する。

DBの内容で手順を分岐する。

- 現行17にNexusの表や実データがある: custom dumpを18の別DBに`pg_restore --exit-on-error`で復元。`pg_dumpall`のglobalsを確認し、18の初期role作成スクリプトと衝突しないrole/owner/権限マッピングを先にレビューする。履歴のない既存表や未知のmigrationがあれば停止し、手作業で履歴を偽装しない。
- Nexusの実データがなく、新規DBとして扱えることを一覧・件数で証明できる: 18の空DBへレビュー済みMobile/Studio migrationを適用する。17のバックアップと隔離復元gateは省略しない。

Mobileの`public` schema migration履歴とStudioの`studio` schema migration履歴は別管理。Studio専用のmigration/runtime roleと権限SQLは現在未完成なので、追加MRの検証が済むまでAPIを起動しない。SQL全文をレビューし、移行専用roleで一度だけ適用する。通常起動やCI deployでmigration/seedしない。

データ件数と代表行、MapDatasetのpayload/checksum、両履歴、runtime roleの拒否/許可、Admin/Public/Studio API、healthcheck、DB再起動後の永続化を確認する。結果と時間・ピークRAM/diskを記録する。

## Phase 3: 18のバックアップと切替判断

18環境でpgBackRest stanza、暗号化フルバックアップ、Macコピー、**別の18用空volumeへの復元**を通す。17のdumpをpgBackRestの18復元として扱わない。初めてここで次期定期timerを採用候補にする。`nexus-mobile`と`nexus-infra`のhost timerを二重登録しない。

切替前には書込元を停止し、17の最終dumpとVPS外コピーを再度取得する。最後のdumpから18での書込開始までを同じメンテナンス窓として扱う。旧17 volumeは保持する。18へ書き込む前に失敗したなら17へ戻す。18で新しい書込を受けた後は旧17へ単純に戻すと更新を失うため、18側の復元または更新差分の整合計画を別途承認する。

## Go/No-Go

- [ ] 現行17のDB/role/書込元/データの棚卸しが完了
- [ ] 17のVPS外暗号化コピーと別volumeへの復元が成功
- [ ] 18の別volumeで論理移行、全migration、Studio専用role・権限が成功
- [ ] Admin APIの`/health`と実イメージhealthcheckが成功
- [ ] 18のpgBackRestからMacコピーし、さらに別volumeへ復元成功
- [ ] disk/RAMと停止時間を実測し、旧17への戻し条件を文書化
- [ ] image digest、secrets contract、DNS、80/443、移行SQLを人間が承認

いずれか未達なら**現行17を維持**し、`nexus-infra` MR !1を本番適用可能として扱わない。
