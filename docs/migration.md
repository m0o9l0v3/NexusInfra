# DB migrationのレビューと手動適用条件

`nexus-mobile` の移行SQLは `tools/database/Nexus.Database.csproj` の `script` コマンドでCI成果物として生成する。StudioのEF Core移行SQLはStudio repoの対象コミットから別に生成する。どちらもアプリ起動、イメージビルド、infra CI、CI deployでは適用しない。2つの移行履歴と`public`/`studio` schemaの所有者・権限を混同しない。

適用前に担当者がSQL全文、対象DB・schema・移行ID、ロック時間と既存データへの影響、前後のアプリ互換性をレビューする。同じPostgreSQLメジャー版の隔離DBで、同じSQLを適用し、再実行時の挙動とAPIを確認する。既存DBを新規DBとして扱う移行は拒否する。

本番適用は別途承認されたメンテナンス作業とする。事前にpgBackRestの正常なバックアップ・WAL状態、Macコピーの成功日時と暗号鍵の別保管、**新しい空volumeへの復元試験**、必要な空き容量を確認する。APIを停止して移行専用ロールでレビュー済みSQLを一度だけ実行し、移行履歴・権限・代表データを照合してからAPIを再開する。失敗時はAPIを停止したまま調査し、自動の逆migrationや既存volumeの初期化はしない。必要な復旧は別volumeへ復元して検証した後に人間が切替を判断する。

このMRはSQLの本番適用を実行せず、実VPSのDB版・既存volume・データ配置も確認できていない。これらは適用前の人間による照合事項である。
