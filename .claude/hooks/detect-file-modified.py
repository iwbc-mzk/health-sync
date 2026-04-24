#!/usr/bin/env python3
"""
transcript_path の JSONL を読み、直近ターンで Edit または Write が
使われたかを検出する。

Claude Code の transcript JSONL 構造:
  各行がルートエントリで type は "assistant" / "user" / "last-prompt" 等。
  tool_use は assistant エントリの message.content[] にネストされている。
  例:
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", ...}]}}

使い方:
  echo '<hook JSON>' | python3 detect-file-modified.py
  exit code: 0 = 改修あり, 1 = 改修なし / エラー
"""
import collections
import json
import sys

# 末尾から走査するエントリ数の上限（メモリ節約）
MAX_TAIL_ENTRIES = 500


def main() -> int:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 1

    transcript_path = hook_input.get("transcript_path", "")
    if not transcript_path:
        return 1

    try:
        # 末尾 MAX_TAIL_ENTRIES 行のみ保持してメモリを節約する
        tail: collections.deque[dict] = collections.deque(maxlen=MAX_TAIL_ENTRIES)
        with open(transcript_path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if raw:
                    try:
                        tail.append(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return 1

    # 末尾から走査して直近ターン（最後の user エントリより後）の
    # assistant エントリ内 message.content[] に Edit / Write が含まれるか確認する
    # セッション終了時に attachment 等のシステムエントリが末尾に付加されるため、
    # assistant 以外は user が出るまでスキップする
    try:
        for entry in reversed(tail):
            entry_type = entry.get("type", "")
            if entry_type == "user":
                # 直近ターンの開始より前に到達 → 改修なし
                break
            if entry_type != "assistant":
                continue
            content = (entry.get("message") or {}).get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "tool_use"
                    and item.get("name") in ("Edit", "Write")
                ):
                    return 0  # 改修あり
    except Exception:
        return 1

    return 1  # 改修なし


sys.exit(main())
