# 実装計画: asken-myfitnesspal-sync

## Phase 1: プロジェクトセットアップ

- [x] 1.1 ディレクトリ構造の作成
  ```
  src/asken_myfitnesspal_sync/
    __init__.py
    handler.py
    config.py
    asken_client.py
    myfitnesspal_client.py
    sync.py
    models.py
    logging_config.py
  tests/asken_myfitnesspal_sync/
    __init__.py
    conftest.py
    test_asken_client.py
    test_myfitnesspal_client.py
    test_sync.py
    test_handler.py
    test_config.py
    fixtures/
      asken_meal_page.html
  ```
- [x] 1.2 `pyproject.toml` / `requirements.txt` に依存追加
  - Runtime: `requests`, `beautifulsoup4`, `boto3`, `lxml`
  - Dev: `pytest`, `pytest-mock`, `responses`, `moto`, `ruff`, `mypy`
- [x] 1.3 共通モジュール (`src/utils/`) の整備検討
  - `asken_client.py`（ログイン・セッション管理）を `src/utils/` に切り出す
    ※ Phase 4.2 で HTML 構造確認後に共通化可否を最終決定する（現時点では各モジュール個別実装）
  - `logging_config.py` を自己完結な実装として配置（utils への依存を排除）→ 完了

## Phase 2: データモデル

- [x] 2.1 `models.py` - データクラス定義
  - `MealType`: Enum（`BREAKFAST`, `LUNCH`, `DINNER`, `SNACKS`）
  - `MealNutrition`: meal_type, calories, protein_g, fat_g, carbs_g
  - `DailyMeals`: date, meals: list[MealNutrition]（間食は合算済み）

## Phase 3: 設定・シークレット管理

- [x] 3.1 `config.py` - AWS Secrets Manager からの認証情報取得
  - シークレット名: `asken-myfitnesspal-sync/credentials`
  - 構造: `asken_email`, `asken_password`, `myfitnesspal_email`, `myfitnesspal_password`
- [x] 3.2 環境変数定義
  - `SECRET_NAME`（省略時: `asken-myfitnesspal-sync/credentials`）
  - `TARGET_DATE`（YYYY-MM-DD、省略時: JST 当日）

## Phase 4: あすけんクライアント

- [x] 4.1 **あすけんサイト調査**（食事ページの HTML 構造を確認）
  - URL: `/wsp/advice/{date}/{meal_id}`（朝食=1, 昼食=2, 夕食=3）、1日合計: `/wsp/advice/{date}`
  - HTML: `<li class="line_left">` 内の `<li class="title">` + `<li class="val">`
  - 欠食判定: "食事記録が無いため" テキストの存在確認
  - 間食 = 1日合計 - 朝食 - 昼食 - 夕食（差分計算）
- [x] 4.2 `asken_client.py` - ログイン処理
  - `src/utils/asken_base_client.py` に `AskenBaseClient` として共通実装
  - `AskenClient` は `AskenBaseClient` を継承
- [x] 4.3 食事データの取得
  - 朝食・昼食・夕食は各アドバイスページから取得
  - 間食 = 1日合計 - 朝食 - 昼食 - 夕食
  - 欠食はエントリ自体をスキップ
  - HTML 構造変更時は `AskenError` を送出（Lambda 失敗扱い）
  - `DailyMeals` を返す

## Phase 5: MyFitnessPal クライアント

- [x] 5.1 **MyFitnessPal API 調査**（ブラウザの内部 API を特定）
  - ログインフロー（フォーム認証 or OAuth）とセッションクッキーの取得方法
  - 食事エントリ取得 API（GET）のエンドポイント・パラメータ・レスポンス形式
  - 食事エントリ登録 API（POST）のエンドポイント・リクエスト形式
  - 食事エントリ削除 API（DELETE）のエンドポイント・パラメータ
  - CSRF トークン・`x-csrf-token` 等の追加ヘッダー要否
- [x] 5.2 `myfitnesspal_client.py` - 認証
  - ログインページからセッションクッキー取得
  - 認証失敗時は例外として伝播（Lambda を失敗扱いにする）
- [x] 5.3 食事エントリの取得
  - 対象日・食事区分ごとの既存エントリを取得
  - `MealNutrition` のリストを返す
- [x] 5.4 食事エントリの登録
  - カスタム食品として1エントリを登録（カロリー・P・F・C を指定）
  - 食事区分（Breakfast / Lunch / Dinner / Snacks）を正しくマッピング
- [x] 5.5 食事エントリの削除
  - 指定エントリを削除（上書き時に使用）

## Phase 6: 同期オーケストレーション

- [x] 6.1 `sync.py` - 重複チェックロジック
  - MyFitnessPal の既存エントリを取得
  - カロリー・PFC が完全一致 → スキップ
  - いずれかが異なる → 既存エントリを削除して再登録
- [x] 6.2 `sync.py` - `sync_meals(date, credentials) -> SyncResult`
  - あすけんから食事データ取得
  - 食事区分ごとに MyFitnessPal と比較・登録（個別エラーは WARNING ログ、継続）
  - 処理結果サマリーを返す（登録件数・スキップ件数・エラー件数）
- [x] 6.3 `sync.py` - `run_sync()`
  - シークレット取得 → 対象日決定 → `sync_meals()` 呼び出し
  - 認証エラーは例外として伝播

## Phase 7: Lambda ハンドラー

- [x] 7.1 `handler.py` - Lambda エントリーポイント
  - 対象日は JST 基準で当日（`zoneinfo.ZoneInfo("Asia/Tokyo")`）
  - `TARGET_DATE` 環境変数でオーバーライド可能
  - boto3 クライアントはモジュールレベルで初期化（ウォームスタート最適化）
- [x] 7.2 `logging_config.py` - JSON 構造化ログ設定
  - asken-garmin-sync の `logging_config.py` を流用（または `src/utils/` から import）

## Phase 8: AWS インフラ

- [x] 8.1 `template.yaml`（SAM テンプレート）
  - Lambda: Python 3.12, 256MB, timeout 300s, 同時実行数制限 1
  - EventBridge Scheduler:
    - 毎時 0 分: `cron(0 * * * ? *)`（UTC = JST と同じ）
    - 毎日 23:59 JST: `cron(59 14 * * ? *)`（UTC）
  - IAM: `secretsmanager:GetSecretValue`、Lambda 基本実行ロール
- [x] 8.2 CloudWatch ロググループ（`/aws/lambda/asken-myfitnesspal-sync`、保持期間 30 日）
- [x] 8.3 Scheduler のリトライ無効化（`MaximumRetryAttempts: 0`）

## Phase 9: エラーハンドリング

- [x] 9.1 リトライ戦略
  - MyFitnessPal: HTTP 429 / 5xx 時に指数バックオフ（最大3回リトライ）
  - あすけん: 接続エラー時にリトライ（最大2回リトライ）
  - 認証エラーは即座に失敗（リトライなし）
- [x] 9.2 食事区分単位のエラー分離
  - 1区分の登録失敗は `WARNING` ログに記録し、他区分の処理を継続
  - Lambda は成功として返す

## Phase 10: テスト

- [x] 10.1 あすけんクライアントのユニットテスト
  - `responses` ライブラリで HTTP モック
  - `tests/asken_myfitnesspal_sync/fixtures/asken_meal_page.html` に HTML fixture を配置
  - 通常ケース・欠食ケース・間食複数ケース・ログイン失敗ケース
- [x] 10.2 MyFitnessPal クライアントのユニットテスト
  - `responses` ライブラリで HTTP モック
  - ログイン・取得・登録・削除の各ケース
  - 認証失敗・API エラーケース
- [x] 10.3 同期ロジックのユニットテスト
  - スキップ・上書き・新規登録の各ケース
  - 食事区分単位のエラー分離が正しく動作するか
- [x] 10.4 Lambda ハンドラーのユニットテスト
  - `TARGET_DATE` 環境変数の動作確認
  - 正常系・認証エラー系
- [x] 10.5 設定モジュールのユニットテスト
  - `moto` で Secrets Manager をモック

## Phase 11: デプロイ

- [ ] 11.1 `scripts/create_secret_mfp.sh` - Secrets Manager シークレット作成スクリプト
- [ ] 11.2 `scripts/deploy_mfp.sh` - SAM build + deploy スクリプト
  - 前提条件チェック・シークレット確認・SAM build/deploy・デプロイ後ガイダンス
- [ ] 11.3 初回デプロイチェックリスト
  1. `scripts/create_secret_mfp.sh` で Secrets Manager にシークレット作成
  2. `scripts/deploy_mfp.sh` で SAM スタックデプロイ
  3. Lambda 手動実行で動作確認
  4. MyFitnessPal でデータ連携を確認
  5. EventBridge スケジュール有効化を確認

---

## リスクと対策

| リスク | 対策 |
|--------|------|
| MyFitnessPal 内部 API の仕様変更 | Phase 5.1 で API を事前調査・文書化。パース失敗時にアラート |
| MyFitnessPal がブラウザ自動化検出 | requests + セッションクッキーで対応。検出された場合は Playwright を検討 |
| MyFitnessPal ログインのレート制限 | セッションクッキーをキャッシュ（SSM Parameter Store 等）して再利用を検討 |
| あすけんの HTML 構造変更 | CSS セレクタを定数化、パース失敗時に WARNING ログ |
| 欠食時のデータ扱い | Phase 4.3 で仕様を決定（スキップ推奨） |
| 間食の合算ロジック | 合算後のカロリーが既存エントリと一致するか確認ロジックの精度 |
