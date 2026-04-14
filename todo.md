# 実装計画: asken-garmin-sync

## Phase 1: プロジェクトセットアップ

- [x] 1.1 ディレクトリ構造の作成
  ```
  src/asken_garmin_sync/
    __init__.py, handler.py, config.py, asken_client.py, garmin_client.py, sync.py, models.py
  tests/
    __init__.py, test_asken_client.py, test_garmin_client.py, test_sync.py, test_handler.py, conftest.py
  ```
- [x] 1.2 `pyproject.toml` 作成（依存パッケージ定義）
  - Runtime: `garminconnect`, `requests`, `beautifulsoup4`, `boto3`, `lxml`
  - Dev: `pytest`, `pytest-mock`, `responses`, `moto`, `ruff`, `mypy`

## Phase 2: データモデル

- [x] 2.1 `models.py` - データクラス定義
  - `BodyComposition`: date, weight_kg, body_fat_percent
  - `ActivityCalories`: date, calories_burned

## Phase 3: 設定・シークレット管理

- [x] 3.1 `config.py` - AWS Secrets Manager からの認証情報取得
  - シークレット構造: asken_email, asken_password, garmin_email, garmin_password, garmin_tokens
- [x] 3.2 Garmin トークン永続化ヘルパー
  - `load_garmin_tokens()`: Secrets Manager → `/tmp/.garminconnect/`
  - `save_garmin_tokens()`: `/tmp/.garminconnect/` → Secrets Manager
- [x] 3.3 環境変数定義 (SECRET_NAME, GARMINTOKENS)

## Phase 4: あすけんクライアント

- [x] 4.1 **あすけんサイト調査** (ログインページ・体重ページ・運動ページのHTML構造を確認)
- [x] 4.2 `asken_client.py` - ログイン処理
  - requests.Session でフォームログイン、CSRFトークン対応
- [x] 4.3 体重・体脂肪率の取得
  - 対象日のページをスクレイピング → BodyComposition を返す
- [x] 4.4 消費カロリーの登録
  - 運動ページにカロリー値をPOST（上書き対応）

## Phase 5: Garmin Connect クライアント

- [x] 5.1 `garmin_client.py` - 認証付きクライアント初期化
  - トークン復元 → 失敗時はフルログイン
  - MFA無効のアカウントが必要（制約として文書化）
- [x] 5.2 体重・体脂肪率の登録
  - `add_body_composition(timestamp, weight, percent_fat)` を呼び出し
- [x] 5.3 アクティビティ消費カロリーの取得
  - `get_stats(cdate)` から totalKilocalories / activeKilocalories を取得

## Phase 6: 同期オーケストレーション

- [x] 6.1 `sync.py` - `sync_body_composition_to_garmin()`
  - あすけんから取得 → Garmin Connect に登録
- [x] 6.2 `sync.py` - `sync_calories_to_asken()`
  - Garmin Connect から取得 → あすけんに登録
- [x] 6.3 `sync.py` - `run_sync()`
  - 両方向を独立に実行（片方失敗しても他方は続行）
  - 実行後にGarminトークンを永続化

## Phase 7: Lambda ハンドラー

- [x] 7.1 `handler.py` - Lambda エントリーポイント
  - 対象日はJST基準で当日（環境変数でオーバーライド可能）
  - タイムゾーン: `Asia/Tokyo` (zoneinfo)
- [x] 7.2 boto3 クライアントはモジュールレベルで初期化（ウォームスタート最適化）

## Phase 8: AWS インフラ

- [x] 8.1 `template.yaml` (SAM テンプレート)
  - Lambda: Python 3.12, 256MB, timeout 300s
  - EventBridge Scheduler: `rate(30 minutes)`
  - IAM: secretsmanager:GetSecretValue, PutSecretValue, DescribeSecret, Lambda基本実行ロール
- [x] 8.2 CloudWatch ログ設定 (保持期間30日)

## Phase 9: エラーハンドリング

- [ ] 9.1 リトライ戦略
  - Garmin: 429 レート制限時に指数バックオフ (最大3回)
  - あすけん: 接続エラー時にリトライ (最大2回)
  - 認証失敗は即座にfail
- [ ] 9.2 構造化ログ (JSON形式、CloudWatch向け)

## Phase 10: テスト

- [ ] 10.1 あすけんクライアントのユニットテスト (`responses`でHTTPモック)
  - `tests/fixtures/` にHTML fixtures を配置
- [ ] 10.2 Garmin クライアントのユニットテスト (Garminクラスをモック)
- [ ] 10.3 同期ロジックのユニットテスト (両クライアントをモック)
- [ ] 10.4 Lambda ハンドラーのユニットテスト
- [ ] 10.5 AWS周り (`moto` で Secrets Manager をモック)

## Phase 11: デプロイ

- [ ] 11.1 `sam build` + `sam deploy` でデプロイ
- [ ] 11.2 初回デプロイチェックリスト
  1. Secrets Manager にシークレット作成
  2. SAM スタックデプロイ
  3. Lambda 手動実行 (Garmin トークン確立)
  4. 両サービスでデータ連携を確認
  5. EventBridge スケジュール有効化

---

## リスクと対策

| リスク | 対策 |
|--------|------|
| あすけんのHTML構造変更 | CSSセレクタを定数化、パース失敗時アラート |
| Garmin ログインレート制限 | OAuthトークンを永続化、毎回のフルログインを回避 |
| Garmin MFA 要求 | MFA無効アカウントが必要 (制約として文書化) |
| あすけんがJavaScript必須 | まず requests+BS4、ダメなら Playwright (Lambda Chromiumレイヤー必要) |
| トークン有効期限切れ | 毎回トークン保存、復元失敗時はフルログインにフォールバック |
