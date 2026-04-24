#!/usr/bin/env bash
# Stop hook: セッション終了時に progress-logger agent の呼び出しを促す
#
# 終了コード:
#   1 = 常にブロックして Claude に progress.txt への追記を促す

set -euo pipefail

PROGRESS_FILE="/workspace/.claude/progress.txt"

if [ ! -f "$PROGRESS_FILE" ]; then
  touch "$PROGRESS_FILE"
fi

cat <<'MSG'
[progress-logger] 作業ログを記録してください:
  - Agent ツールで agent_name: "progress-logger" を指定して呼び出す
  - 今回の会話で実施した内容を /workspace/.claude/progress.txt に追記する
  - スキップ厳禁です
MSG

exit 1
