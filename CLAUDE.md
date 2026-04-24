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

## コマンド

```bash
# テスト
pytest tests/

# Lint / フォーマットチェック
ruff check src/ tests/

# 型チェック
mypy src/

# デプロイ（初回 or 設定変更時）
./scripts/deploy.sh --guided
```

## ディレクトリ構成

```
src/
  utils/                      # 共通モジュール（logging_config.py, asken_base_client.py 等）
  asken_garmin_sync/          # Garmin 連携 Lambda
  asken_myfitnesspal_sync/    # MyFitnessPal 連携 Lambda
tests/                        # pytest テストスイート
scripts/deploy.sh             # SAM デプロイスクリプト
template-garmin.yaml          # SAM テンプレート（Garmin）
template-mfp.yaml             # SAM テンプレート（MFP）
requirements_garmin.txt       # Garmin 用 Lambda 依存ライブラリ
requirements_mfp.txt          # MFP 用 Lambda 依存ライブラリ
pyproject.toml                # 開発依存（pytest, ruff, mypy 等）
```

## 共通モジュール

各機能で共通して利用されるコードは `src/utils/` に実装すること。

- 例: JSON 構造化ログ設定、あすけんスクレイピングクライアント、リトライユーティリティ等
- 各機能モジュール（`asken_garmin_sync/`、`asken_myfitnesspal_sync/` 等）は `src/utils/` をインポートして利用する
- 各モジュール内の `logging_config.py` は `src/utils/logging_config.py` の re-export shim（実装本体は `src/utils/` 側）

## ハーネスエンジニアリング ルール

### 検証ルール

タスク完了後は必ず以下の手順に従いevaluatorによる検証を行うこと
検証なしにタスクの完了を報告するのは断じて許すことはできない

1. `evaluator` サブエージェントを呼び出してコードを評価する
2. FAIL の場合: 指摘された点を修正し、再度 evaluator を呼び出す
3. PASS になるまで 1-2 を繰り返す
4. 評価エラーが 4 回連続した場合: それ以上の自律的な修正は断じて許容できない。実装を停止し、ユーザーに状況を報告して指示を仰ぐ。それ以上の修正は厳禁。

### 進捗ログ管理

- 作業を開始する前に、必ず `.claude/progress.txt` と Git のログを読み、最新の作業状況と未完了のタスクを確認すること
- タスク完了時またはセッション終了時に、実施した内容、遭遇したエラーとその解決策、下した重要な意思決定を `.claude/progress.txt` に追記すること
