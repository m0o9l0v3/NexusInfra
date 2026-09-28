# VPS全損からの復旧

1. 障害と最終のVPS外バックアップ時刻を確認。DNS切替を急ぐ前にバックアップのハッシュと復号鍵が揃うことを確認する。
2. 新しいさくらVPSとUbuntuを人間が用意。SSH bootstrapで公開鍵認証のみ、rootログイン・パスワード認証無効を確認。22/80/443以外を閉じる。OS更新、2GiB swap、`vm.swappiness=10`、Docker Engine/Compose、daemonログローテーション、`/srv/nexus`を再現する。既存手動設定を検査してから自動化範囲を決める。SSHやfirewallの変更はCIから行わない。
3. レビュー済み`nexus-infra` commit、イメージdigest一覧、秘密情報を安全な保管先から戻す。DNSとACME連絡先を設定。DBメジャー版とマウント先、secret、権限を確認。
4. 新しい空volumeへDBを復元。第一候補は既存pgBackRestのMacコピーと別保管の鍵を使う手順。論理dumpしかない場合は新しいDBへpg_restoreし、ロール・schema・権限を再構築する。元volumeを再利用・削除しない。移行履歴、代表レコード、checksum、件数を確認。
5. CaddyとAPI/Studioのイメージをreview済みdigestで起動。`/health`、認証、管理画面、iOS APIを確認。DNSを切り替え、HTTPS・証明書・ログを監視。
6. 復旧時刻、失われた更新範囲、障害原因、残るリスクを記録し、バックアップと月次復元試験を再開。

RPOは「最後にMacへ正常コピーした時点まで」、RTOはVPS再調達・転送・復元の実測値が得られるまで数値保証しない。運用目標としてMacコピーを利用日には1日1回、重要更新の前後に行う。4時間復旧や15分以内のデータ損失を現時点で約束しない。月1回、新規volumeへの復元リハーサルで実測し目標を更新する。
