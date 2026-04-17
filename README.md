# asken-garmin-sync

あすけん (asken.jp) と Garmin Connect 間のデータ同期ツール。AWS Lambda で動作し、EventBridge Scheduler により30分間隔で自動実行されます。

## 機能

| 方向 | データ | 方法 |
|------|--------|------|
| あすけん → Garmin Connect | 体重・体脂肪率 | あすけんをスクレイピングして取得し Garmin Connect に登録 |
| Garmin Connect → あすけん | アクティビティ消費カロリー | Garmin Connect から取得してあすけんに登録 |

## アーキテクチャ

```
EventBridge Scheduler (30分間隔)
         │
         ▼
    Lambda Function (Python 3.12, 256MB, 300s)
         │
         ├─► あすけん (Web スクレイピング)
         │        体重・体脂肪率 取得
         │        消費カロリー 書き込み
         │
         └─► Garmin Connect (garminconnect ライブラリ)
                  体重・体脂肪率 書き込み
                  アクティビティ消費カロリー 取得

    Secrets Manager ◄──► Lambda (認証情報・Garmin OAuthトークン管理)
```

## 前提条件

- AWS CLI v2
- SAM CLI
- Docker (SAM ビルド用)
- Python 3.12+
- あすけんアカウント
- Garmin Connect アカウント (MFA無効)

## デプロイ手順

### 1. Secrets Manager にシークレットを作成

```bash
./scripts/create_secret.sh --region ap-northeast-1 --profile default
```

以下の認証情報を対話形式で入力します:
- あすけん メールアドレス・パスワード
- Garmin Connect メールアドレス・パスワード

### 2. SAM でデプロイ

```bash
# 初回デプロイ (guided モードで対話設定)
./scripts/deploy.sh --guided --region ap-northeast-1 --profile default

# 2回目以降 (samconfig.toml の設定を使用)
./scripts/deploy.sh --region ap-northeast-1 --profile default
```

### 3. Lambda を手動実行して Garmin トークンを確立

```bash
aws lambda invoke \
  --function-name asken-garmin-sync \
  --region ap-northeast-1 \
  /tmp/response.json
cat /tmp/response.json
```

### 4. ログで実行結果を確認

```bash
aws logs tail /aws/lambda/asken-garmin-sync --follow --region ap-northeast-1
```

### 5. データ連携の確認

- **あすけん**: 当日の消費カロリーが Garmin のデータで更新されているか確認
- **Garmin Connect**: 当日の体重・体脂肪率があすけんのデータで登録されているか確認

### 6. EventBridge スケジュール確認

デプロイ後、30分間隔の自動実行が有効になっています:

```bash
aws scheduler get-schedule \
  --name asken-garmin-sync-schedule \
  --region ap-northeast-1
```

## ローカル開発

```bash
# 依存パッケージインストール
pip install -e ".[dev]"

# テスト実行
pytest

# Lint
ruff check src/ tests/

# 型チェック
mypy src/
```

## 設定

### 環境変数

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `SECRET_NAME` | ○ | Secrets Manager のシークレット名 (SAM テンプレートで自動設定) |
| `TARGET_DATE` | - | 対象日 (YYYY-MM-DD形式、省略時はJST当日) |

### Secrets Manager シークレット構造

```json
{
  "asken_email":     "your-email@example.com",
  "asken_password":  "your-password",
  "garmin_email":    "your-garmin-email@example.com",
  "garmin_password": "your-garmin-password",
  "garmin_tokens":   ""
}
```

`garmin_tokens` は Lambda 初回実行後に自動的に設定されます。

## 注意事項

- **Garmin Connect MFA**: MFA が有効なアカウントは使用できません。MFA を無効化してください。
- **同時実行**: Lambda の同時実行数は 1 に制限されています（Garmin OAuthトークンの破損防止）。
- **同一日データ**: 同じ日のデータが既に存在する場合は上書きされます。
- **摂取カロリー連携**: Garmin Connect への栄養データ書き込みは現時点でスコープ外です。

## トラブルシューティング

### Garmin ログインエラー

Garmin Connect のレート制限に引っかかっている場合があります。しばらく時間を置いてから再実行してください。`garmin_tokens` が古い場合は Secrets Manager で値を `null` にリセットすると、次回実行時にフルログインが試みられます。

### あすけんスクレイピングエラー

あすけんの HTML 構造が変更された可能性があります。CloudWatch ログでパースエラーを確認し、[asken_client.py](src/asken_garmin_sync/asken_client.py) のセレクタを更新してください。

### Lambda タイムアウト

タイムアウト上限は 300 秒に設定されています。ネットワーク環境が原因の場合、`template.yaml` の `Timeout` を調整してください（最大 900 秒）。
