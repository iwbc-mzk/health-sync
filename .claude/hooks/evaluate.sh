#!/usr/bin/env bash
# PostToolUse hook: 静的解析 + 失敗カウンター管理
# 終了コード:
#   0 = 静的解析OK → evaluator 呼び出しを促すメッセージを出力
#   1 = 静的解析エラー → エラーをClaudeに注入
#   2 = 4回連続失敗 → 実装停止・人間エスカレーション指示

set -euo pipefail

COUNTER_FILE="/tmp/asken_eval_failures"
MAX_FAILURES=4

# 現在の失敗カウントを読み取る
current_count=$(cat "$COUNTER_FILE" 2>/dev/null || echo "0")

# カウントが上限に達していれば即座に停止指示
if [ "$current_count" -ge "$MAX_FAILURES" ]; then
  echo "================================================================"
  echo "[HALT] 評価エラーが ${MAX_FAILURES} 回連続で発生しました。"
  echo "自動修正ループを停止します。人間の判断が必要です。"
  echo "以下を確認してください:"
  echo "  - 仕様の解釈が正しいか"
  echo "  - 設計方針の変更が必要かどうか"
  echo "  - 現在の実装アプローチに根本的な問題がないか"
  echo "================================================================"
  echo "実装を停止し、ユーザーに状況を説明して指示を仰いでください。"
  exit 2
fi

# Pythonソースが存在するか確認
SRC_DIR="src/asken_garmin_sync"
if [ ! -d "$SRC_DIR" ]; then
  exit 0  # まだソースがない場合はスキップ
fi

# Python ファイルが空でない場合のみ解析を実行
py_files=$(find "$SRC_DIR" -name "*.py" -size +0c 2>/dev/null || true)
if [ -z "$py_files" ]; then
  exit 0
fi

ERRORS=0

# ruff linting
echo "[静的解析] ruff を実行中..."
if ! python -m ruff check "$SRC_DIR" 2>&1; then
  ERRORS=1
fi

# mypy type check
echo "[静的解析] mypy を実行中..."
if ! python -m mypy "$SRC_DIR" 2>&1; then
  ERRORS=1
fi

if [ "$ERRORS" -ne 0 ]; then
  new_count=$((current_count + 1))
  echo "$new_count" > "$COUNTER_FILE"
  echo ""
  echo "[評価失敗 ${new_count}/${MAX_FAILURES}] 静的解析でエラーが検出されました。"
  echo "上記のエラーを修正してから次のステップに進んでください。"
  exit 1
fi

# 静的解析OK → evaluator サブエージェントの呼び出しを促す
# カウンターをリセット
echo "0" > "$COUNTER_FILE"
echo "[静的解析 OK] 次のステップ: evaluator サブエージェントを呼び出してコードを評価してください。"
echo "evaluator が FAIL を報告した場合:"
echo "  - 指摘された Critical/Major を修正する"
echo "  - 修正後にまた評価を依頼する"
echo "  - evaluator が PASS を報告したら次のフェーズへ進む"
echo "  - 失敗が続く場合はカウンターが増加し、${MAX_FAILURES} 回で自動停止します"
exit 0
