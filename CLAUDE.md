# asken-garmin-sync

あすけん (asken.jp) と Garmin Connect 間のデータ同期ツール。

## プロジェクト概要

- **言語**: Python
- **実行環境**: AWS Lambda
- **連携頻度**: 30分〜1時間間隔（EventBridge Scheduler等で定期実行）
- **認証情報管理**: AWS Secrets Manager

## 連携仕様

### あすけん → Garmin Connect

| データ | 方法 |
|--------|------|
| 体重 | あすけんからスクレイピングで取得 → Garmin Connect に登録 |
| 体脂肪率 | あすけんからスクレイピングで取得 → Garmin Connect に登録 |
| 摂取カロリー | **スコープ外** (Garmin Connect への書き込み手段に制限あり) |

### Garmin Connect → あすけん

| データ | 方法 |
|--------|------|
| アクティビティ消費カロリー | Garmin Connect から取得 → あすけんに登録 |

### 重複データの扱い

- 同じ日のデータが既に存在する場合は **上書き** する

## 技術スタック

### あすけん (データ取得元/登録先)

- 公式APIなし、データエクスポート機能なし
- Web スクレイピングで対応（requests + BeautifulSoup or Playwright）
- ログインURL: `https://www.asken.jp/login/`
- 認証: メール + パスワード（フォームベース）

### Garmin Connect (データ取得元/登録先)

- `garminconnect` ライブラリ (python-garminconnect) を使用
- 認証: メール + パスワード → OAuth トークン自動管理
- 体重・体脂肪率登録: `add_body_composition()` メソッド
- アクティビティ取得: `get_activities_by_date()` / `get_stats()` メソッド
- 栄養データ書き込み: ライブラリ未サポート（要検討）

## 決定事項

- 摂取カロリーのGarmin Connect連携はスコープ外（ライブラリ未サポートのため）

## ハーネスエンジニアリング ルール

各実装フェーズ完了後、必ず以下の手順に従うこと：

1. `evaluator` サブエージェントを呼び出してコードを評価する
2. FAIL の場合: Critical/Major の指摘を修正し、再度 evaluator を呼び出す
3. PASS になるまで 1-2 を繰り返す
4. 評価エラーが 4 回連続した場合: 実装を停止し、ユーザーに状況を報告して指示を仰ぐ
