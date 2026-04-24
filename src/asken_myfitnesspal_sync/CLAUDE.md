# asken-myfitnesspal-sync

あすけん (asken.jp) から MyFitnessPal への食事データ同期ツール。

## プロジェクト概要

- **言語**: Python
- **実行環境**: AWS Lambda（asken-garmin-sync とは別の独立した Lambda 関数）
- **連携頻度**: 1時間ごと + 毎日 23:59（EventBridge Scheduler）
- **認証情報管理**: AWS Secrets Manager（シークレット名: `asken-myfitnesspal-sync`）

## 連携仕様

### あすけん → MyFitnessPal

| データ | 方法 |
|--------|------|
| 朝食のカロリー・PFC | あすけんからスクレイピングで取得 → MyFitnessPal の Breakfast に登録 |
| 昼食のカロリー・PFC | あすけんからスクレイピングで取得 → MyFitnessPal の Lunch に登録 |
| 夕食のカロリー・PFC | あすけんからスクレイピングで取得 → MyFitnessPal の Dinner に登録 |
| 間食のカロリー・PFC（複数あれば合算） | あすけんからスクレイピングで取得 → MyFitnessPal の Snacks に登録 |

### 食事区分マッピング

| あすけん | MyFitnessPal |
|----------|--------------|
| 朝食 | Breakfast |
| 昼食 | Lunch |
| 夕食 | Dinner |
| 間食（1つ以上ある場合は合算） | Snacks |

### 取得対象日

- JST 基準の当日分のみ
- 環境変数 `TARGET_DATE`（YYYY-MM-DD）でオーバーライド可能

### 重複データの扱い

- 同じ日・同じ食事区分のデータが MyFitnessPal に既に存在する場合:
  - カロリー・PFC がすべて同一 → **スキップ**（変更なし）
  - いずれかが異なる → **上書き**（既存エントリを削除して再登録）

## 技術スタック

### あすけん（データ取得元）

- 公式 API なし、データエクスポート機能なし
- Web スクレイピングで対応（requests + BeautifulSoup）
- ログイン URL: `https://www.asken.jp/login/`
- 認証: メール + パスワード（フォームベース）
- 取得データ: 食事区分ごとのカロリー・タンパク質・脂質・炭水化物

### MyFitnessPal（データ登録先）

- ブラウザで利用されている内部 API を直接呼び出す
- 登録エンドポイント: `https://www.myfitnesspal.com/api/services/diary`
- 認証: メール + パスワード → セッションクッキーを取得して API 呼び出しに使用
- 詳細な API 仕様（リクエスト形式・認証フロー・レスポンス形式）は実装時に確認すること

## 認証情報（Secrets Manager）

シークレット名: `asken-myfitnesspal-sync`

```json
{
  "asken_email": "...",
  "asken_password": "...",
  "myfitnesspal_email": "...",
  "myfitnesspal_password": "..."
}
```

## インフラ構成

- **Lambda 関数名**: `asken-myfitnesspal-sync`
- **実行スケジュール**（EventBridge Scheduler、JST 基準）:
  - 毎時 0 分: `cron(0 * * * ? *)` ※ UTC 表記に変換すること
  - 毎日 23:59: `cron(59 14 * * ? *)` ※ UTC（JST -9h）
- **タイムアウト**: 300秒
- **メモリ**: 256MB
- **同時実行数制限**: 1（重複実行防止）
- **CloudWatch ロググループ**: `/aws/lambda/asken-myfitnesspal-sync`（保持期間 30 日）

## エラーハンドリング・ロギング

asken-garmin-sync と同一の方針に従う:

- CloudWatch Logs へ JSON 構造化ログ出力（`logging_config.py` の `JsonFormatter` を流用）
- 認証エラーは例外として伝播させ Lambda を失敗扱いにする
- 個別操作エラー（食事区分単位）は `WARNING` ログに記録し、Lambda は成功として継続する
- Scheduler レベルのリトライは無効（`MaximumRetryAttempts: 0`）
- 環境変数:
  - `SECRET_NAME`: Secrets Manager シークレット名（省略時: `asken-myfitnesspal-sync`）
  - `TARGET_DATE`: 同期対象日（省略時: JST 当日）

## モジュール構成（参考）

asken-garmin-sync と同様の構成を踏襲する:

```
src/asken_myfitnesspal_sync/
├── __init__.py
├── handler.py          # Lambda ハンドラー
├── sync.py             # 同期ロジック（メインフロー）
├── asken_client.py     # あすけんスクレイピングクライアント
├── myfitnesspal_client.py  # MyFitnessPal API クライアント
├── models.py           # データモデル（食事データ等）
├── config.py           # 設定・Secrets Manager アクセス
└── logging_config.py   # src/utils/logging_config.py の re-export shim
```

