# asken-sync

あすけん (asken.jp) を起点とした各サービスへのデータ同期ツール群。

## 共通仕様

- **言語**: Python
- **実行環境**: AWS Lambda（機能ごとに独立した Lambda 関数）
- **認証情報管理**: AWS Secrets Manager
- **ログ**: CloudWatch Logs へ JSON 構造化ログ出力
- **あすけん認証**: メール + パスワード（フォームベース、`https://www.asken.jp/login/`）

## 機能一覧

| 機能                    | 概要                                   | 詳細                                                                           |
| ----------------------- | -------------------------------------- | ------------------------------------------------------------------------------ |
| asken-garmin-sync       | あすけん ↔ Garmin Connect データ同期   | [src/asken_garmin_sync/CLAUDE.md](src/asken_garmin_sync/CLAUDE.md)             |
| asken-myfitnesspal-sync | あすけん → MyFitnessPal 食事データ同期 | [src/asken_myfitnesspal_sync/CLAUDE.md](src/asken_myfitnesspal_sync/CLAUDE.md) |

## 共通モジュール

各機能で共通して利用されるコードは `src/utils/` に実装すること。

- 例: JSON 構造化ログ設定、あすけんスクレイピングクライアント、リトライユーティリティ等
- 各機能モジュール（`asken_garmin_sync/`、`asken_myfitnesspal_sync/` 等）は `src/utils/` をインポートして利用する

## ハーネスエンジニアリング ルール

各実装フェーズ完了後、必ず以下の手順に従うこと：

1. `evaluator` サブエージェントを呼び出してコードを評価する
2. FAIL の場合: 指摘された点を修正し、再度 evaluator を呼び出す
3. PASS になるまで 1-2 を繰り返す
4. 評価エラーが 4 回連続した場合: それ以上の自律的な修正は断じて許容できない。実装を停止し、ユーザーに状況を報告して指示を仰ぐ。それ以上の修正は厳禁。
