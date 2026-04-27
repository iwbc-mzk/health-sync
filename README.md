# health-sync

あすけん (asken.jp) を起点とした各サービスへのデータ同期ツール群。AWS Lambda で動作し、EventBridge Scheduler により自動実行されます。

## 機能一覧

| 機能 | 概要 | スケジュール |
|------|------|------------|
| asken-garmin-sync | あすけん ↔ Garmin Connect データ同期 | 30分間隔 |
| asken-myfitnesspal-sync | あすけん → MyFitnessPal 食事データ同期 | 食事時間帯 (8:30/13:30/19:30 JST) + 毎日23:59 JST、各 ±10分ジッター |

---

## asken-garmin-sync

あすけんと Garmin Connect 間でデータを双方向同期します。

| 方向 | データ |
|------|--------|
| あすけん → Garmin Connect | 体重・体脂肪率 |
| Garmin Connect → あすけん | アクティビティ消費カロリー |

### アーキテクチャ

```
EventBridge Scheduler (30分間隔)
         │
         ▼
    Lambda Function (asken-garmin-sync, Python 3.12, 256MB, 300s)
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

### デプロイ手順

#### 1. Secrets Manager にシークレットを作成

```bash
./scripts/create_secret.sh --region ap-northeast-1 --profile default
```

以下の認証情報を対話形式で入力します:
- あすけん メールアドレス・パスワード
- Garmin Connect メールアドレス・パスワード

#### 2. SAM でデプロイ

```bash
# 初回デプロイ (guided モードで対話設定)
./scripts/deploy.sh --guided --region ap-northeast-1 --profile default

# 2回目以降 (samconfig.toml の設定を使用)
./scripts/deploy.sh --region ap-northeast-1 --profile default
```

#### 3. Lambda を手動実行して Garmin トークンを確立

```bash
aws lambda invoke \
  --function-name asken-garmin-sync \
  --region ap-northeast-1 \
  /tmp/response.json
cat /tmp/response.json
```

#### 4. ログで実行結果を確認

```bash
aws logs tail /aws/lambda/asken-garmin-sync --follow --region ap-northeast-1
```

#### 5. EventBridge スケジュール確認

```bash
aws scheduler get-schedule \
  --name asken-garmin-sync-schedule \
  --region ap-northeast-1
```

### 設定

**環境変数**

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `SECRET_NAME` | ○ | Secrets Manager のシークレット名 (SAM テンプレートで自動設定) |
| `TARGET_DATE` | - | 対象日 (YYYY-MM-DD形式、省略時はJST当日) |

**Secrets Manager シークレット** (シークレット名: `asken-garmin-sync`)

```json
{
  "asken_email":     "your-email@example.com",
  "asken_password":  "your-password",
  "garmin_email":    "your-garmin-email@example.com",
  "garmin_password": "your-garmin-password",
  "garmin_tokens":   null
}
```

`garmin_tokens` は Lambda 初回実行後に自動的に設定されます。

### 注意事項

- **Garmin Connect MFA**: MFA が有効なアカウントは使用できません。MFA を無効化してください。
- **同時実行**: Lambda の同時実行数は 1 に制限されています（Garmin OAuthトークンの破損防止）。
- **同一日データ**: 同じ日のデータが既に存在する場合は上書きされます。
- **摂取カロリー連携**: Garmin Connect への栄養データ書き込みは現時点でスコープ外です。

---

## asken-myfitnesspal-sync

あすけんの食事データ（朝食・昼食・夕食・間食）を MyFitnessPal に同期します。

| あすけん | MyFitnessPal |
|----------|--------------|
| 朝食 | Breakfast |
| 昼食 | Lunch |
| 夕食 | Dinner |
| 間食（複数あれば合算） | Snacks |

重複データは カロリー・PFC が同一ならスキップ、異なれば上書きされます。

### アーキテクチャ

```
EventBridge Scheduler (食事時間帯 8:30/13:30/19:30 JST + 毎日23:59 JST、各 ±10分ジッター)
         │
         ▼
    Lambda Function (asken-myfitnesspal-sync, Python 3.12, 256MB, 300s)
         │
         ├─► あすけん (Web スクレイピング)
         │        食事区分ごとのカロリー・PFC 取得
         │
         ├─► MyFitnessPal (セッションクッキー認証 + 内部 API)
         │        食事データ 書き込み・スクレイピングによる重複検出
         │
         └─► SNS Topic (MFP セッションクッキー失効時に通知)
                  メール購読でユーザーに即時通知

    Secrets Manager ◄──► Lambda (認証情報・MFP セッションクッキー管理)
```

> **MFP の bot 検出対策について**
> MFP は自動アクセスを検知してセッションクッキーを無効化することがあります。本実装は実ブラウザに近づけるための対策（ブラウザヘッダー忠実化、`?refresh=true` 撤廃、呼び出し回数削減、スケジュールジッター）を導入しており、失効時は SNS 経由でユーザーへ通知し手動更新を促します。

### デプロイ手順

#### 1. MyFitnessPal セッションクッキーを取得

ブラウザで MyFitnessPal にログイン後、開発者ツール → Application → Cookies → `www.myfitnesspal.com` から
`__Secure-next-auth.session-token` の値をコピーしておきます。

#### 2. Secrets Manager にシークレットを作成

```bash
./scripts/create_secret_mfp.sh --region ap-northeast-1 --profile default
```

以下を対話形式で入力します:
- あすけん メールアドレス・パスワード
- MyFitnessPal セッションクッキー (`__Secure-next-auth.session-token` の値)

#### 3. SAM でデプロイ

```bash
# 初回デプロイ (guided モードで対話設定)
./scripts/deploy_mfp.sh --guided --region ap-northeast-1 --profile default

# 2回目以降 (samconfig.toml の設定を使用)
./scripts/deploy_mfp.sh --region ap-northeast-1 --profile default
```

#### 4. SNS トピックにメール購読を追加（セッションクッキー失効通知用）

デプロイ完了後、Outputs の `MfpAuthAlertTopicArn` を控えてメール購読を登録します:

```bash
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name asken-myfitnesspal-sync \
  --query "Stacks[0].Outputs[?OutputKey=='MfpAuthAlertTopicArn'].OutputValue" \
  --output text --region ap-northeast-1)

aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol email \
  --notification-endpoint your-email@example.com \
  --region ap-northeast-1
```

メール宛に確認リンクが届くのでクリックして購読を確定してください。

#### 5. Lambda を手動実行して動作確認

```bash
aws lambda invoke \
  --function-name asken-myfitnesspal-sync \
  --region ap-northeast-1 \
  /tmp/response.json
cat /tmp/response.json
```

#### 6. ログで実行結果を確認

```bash
aws logs tail /aws/lambda/asken-myfitnesspal-sync --follow --region ap-northeast-1
```

#### 7. EventBridge スケジュール確認

```bash
aws scheduler get-schedule --name asken-myfitnesspal-sync-meals --region ap-northeast-1
aws scheduler get-schedule --name asken-myfitnesspal-sync-daily --region ap-northeast-1
```

### 設定

**環境変数**

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `SECRET_NAME` | ○ | Secrets Manager のシークレット名 (SAM テンプレートで自動設定) |
| `MFP_AUTH_ALERT_SNS_TOPIC_ARN` | ○ | MFP 認証失効通知用 SNS トピック ARN (SAM テンプレートで自動設定) |
| `TARGET_DATE` | - | 対象日 (YYYY-MM-DD形式、省略時はJST当日) |

**Secrets Manager シークレット** (シークレット名: `asken-myfitnesspal-sync`)

```json
{
  "asken_email":                   "your-email@example.com",
  "asken_password":                "your-password",
  "myfitnesspal_session_cookie":   "<__Secure-next-auth.session-token の値>"
}
```

> **セッションクッキーが失効したとき**
> Lambda が `MfpAuthError` で失敗すると、登録済みメールアドレス宛に SNS 通知が届きます。
> ブラウザで MFP に再ログインし、新しい `__Secure-next-auth.session-token` の値を取得後、
> `./scripts/create_secret_mfp.sh` を再実行して値を更新してください。

---

## ローカル開発

```bash
# 依存パッケージインストール
pip install -e ".[dev]"

# テスト実行
pytest tests/

# Lint
ruff check src/ tests/

# 型チェック
mypy src/
```

## 前提条件

- AWS CLI v2
- SAM CLI
- Docker (SAM ビルド用)
- Python 3.12+
- あすけんアカウント
- Garmin Connect アカウント (MFA無効) ※ asken-garmin-sync のみ
- MyFitnessPal アカウント ※ asken-myfitnesspal-sync のみ

## ディレクトリ構成

```
src/
  utils/                      # 共通モジュール (logging_config.py, asken_base_client.py 等)
  asken_garmin_sync/          # Garmin 連携 Lambda
  asken_myfitnesspal_sync/    # MyFitnessPal 連携 Lambda
tests/                        # pytest テストスイート
scripts/
  create_secret.sh            # Garmin 用シークレット作成
  create_secret_mfp.sh        # MFP 用シークレット作成
  deploy.sh                   # Garmin 用 SAM デプロイ
  deploy_mfp.sh               # MFP 用 SAM デプロイ
template-garmin.yaml          # SAM テンプレート (Garmin)
template-mfp.yaml             # SAM テンプレート (MFP)
pyproject.toml                # 開発依存 (pytest, ruff, mypy 等)
samconfig.toml                # SAM デプロイ設定
```

## トラブルシューティング

### Garmin ログインエラー

Garmin Connect のレート制限に引っかかっている場合があります。しばらく時間を置いてから再実行してください。`garmin_tokens` が古い場合は Secrets Manager で値を `null` にリセットすると、次回実行時にフルログインが試みられます。

### あすけんスクレイピングエラー

あすけんの HTML 構造が変更された可能性があります。CloudWatch ログでパースエラーを確認し、該当機能の `asken_client.py` のセレクタを更新してください。

### Lambda タイムアウト

タイムアウト上限は 300 秒に設定されています。ネットワーク環境が原因の場合、各 SAM テンプレートの `Timeout` を調整してください（最大 900 秒）。

### MyFitnessPal セッションクッキー失効

MFP は自動アクセスを検知してセッションクッキー (`__Secure-next-auth.session-token`) を無効化することがあります。失効すると Lambda が `MfpAuthError` で失敗し、SNS 経由でメール通知が届きます。

対応手順:
1. ブラウザで MFP に再ログイン
2. 開発者ツール → Application → Cookies → `www.myfitnesspal.com` から `__Secure-next-auth.session-token` の値をコピー
3. `./scripts/create_secret_mfp.sh` を実行してシークレットを更新
4. 必要に応じて `aws lambda invoke` で動作確認

CloudWatch ログでは「ログイン画面へリダイレクトされました」「Cloudflare の bot 検出によりブロック」「HTTP 401/403」等のメッセージで失効を確認できます。
