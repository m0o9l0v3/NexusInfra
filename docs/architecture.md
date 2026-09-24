# 構成と責務

## 配置

```
Internet --80/443--> Caddy --edge--> public-api (:8080)
                            |-------> studio-web (:8080)
                            |-------> studio-api (:8080, /api/*)
                            |-------> admin-api (:8080, /admin/*)
public-api/admin-api/studio-api --db--> PostgreSQL (:5432)
```

ホスト公開はCaddyの80/443だけ。SSH 22はホストで別途管理し、さくらのパケットフィルターでも22/80/443のみを許可する設計。Docker daemon socketはどのコンテナにもマウントしない。dbとedgeは内部ネットワーク。Caddyはdbに属さない。Caddyには証明書保存用named volumeを付け、HTTPからHTTPSへのリダイレクトと証明書更新はCaddyのautomatic HTTPSに任せる。

本番の既存DBは未調査で、現在のvolumeとイメージを自動発見・変更しない。特にPostgreSQL 15の開発用Composeと、別ブランチで設計した18系の本番DBはデータ配置が異なる。設定中の`/var/lib/postgresql`は18系の採用案であり、既存VPSを検査するまで適用不可。

RAM 4GBではPrometheus/Grafanaは初期導入しない。systemdタイマーの結果、`scripts/healthcheck.sh`、`docker compose ps`、Docker logs、`df`、`free`、`uptime`を日次確認する。disk 80%で調査、90%で更新停止と容量対処。バックアップ成功時刻が26時間を超える、コンテナ不健康、TLS残存30日未満なら対応する。通知は既存の経路が確定してから設定し、未設定の間は人が毎日確認する。TLS期限は`echo | openssl s_client -connect "$API_DOMAIN:443" -servername "$API_DOMAIN" 2>/dev/null | openssl x509 -noout -enddate`をドメインごとに確認する。

## イメージと更新

アプリのDockerfile・ビルド・テスト・registry pushはアプリrepoの責務。本repoはdigestで参照する。Git SHAタグは追跡とロールバック候補の識別に用い、実際に適用する参照はdigest。前回のdigestと変更前のCompose環境ファイルを安全に保管し、失敗時は前回digestを再適用する。DBのスキーマ変更を伴うときはイメージだけを戻しても復旧できないので、事前のバックアップ、互換性評価、個別の復元判断が必要。
