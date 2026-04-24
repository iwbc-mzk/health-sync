#!/usr/bin/env bash
# Stop hook: ファイル改修を検出した場合のみ evaluator 呼び出しを促す
#
# 終了コード:
#   0 = ファイル改修なし → 何もしない
#   1 = ファイル改修あり → Claude に evaluator 実行を促してブロック

set -euo pipefail

DETECTOR="/workspace/.claude/hooks/detect-file-modified.py"

if [ ! -f "$DETECTOR" ]; then
  echo "[ERROR] detect-file-modified.py が見つかりません: $DETECTOR"
  exit 1
fi

hook_input=$(cat)

if echo "$hook_input" | python3 "$DETECTOR"; then
  cat <<'MSG'
[evaluator] ファイルの改修が検出されました。
以下を必ず実行してください:
  1. evaluator サブエージェントを呼び出してコードを評価する（Agent ツールで agent_name: "evaluator" を指定）
  2. FAIL の場合は指摘を修正して再評価する
  3. PASS になるまで繰り返す
MSG
  exit 1
fi

exit 0
