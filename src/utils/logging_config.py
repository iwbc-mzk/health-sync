"""構造化ログ設定 - CloudWatch Logs 向け JSON フォーマット（共通モジュール）."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """CloudWatch Logs 向けに構造化 JSON でログを出力するフォーマッター.

    各ログレコードを以下の形式の JSON 1 行として出力する::

        {
            "timestamp": "2026-04-14T10:00:00.123Z",
            "level": "INFO",
            "logger": "asken_garmin_sync.handler",
            "message": "同期開始: target_date=2026-04-14",
            "exc_info": "Traceback ..."   # 例外がある場合のみ
        }

    CloudWatch Logs Insights でのクエリ例::

        fields timestamp, level, logger, message
        | filter level = "ERROR"
        | sort timestamp desc
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self._utc_iso(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exc_info"] = record.exc_text

        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _utc_iso(created: float) -> str:
        """epoch 秒を UTC ISO 8601 文字列（ミリ秒付き）に変換する."""
        dt = datetime.fromtimestamp(created, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def configure_logging(level: int = logging.INFO) -> None:
    """ルートロガーに JSON フォーマッターを設定する.

    Lambda 実行環境では既存のハンドラーが存在する場合があるため、
    ルートロガーのハンドラーをすべて JSON フォーマッターで置き換える。

    Args:
        level: ルートロガーのログレベル（デフォルト: logging.INFO）
    """
    root = logging.getLogger()
    root.setLevel(level)

    formatter = JsonFormatter()

    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(formatter)
            handler.setLevel(level)
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.setLevel(level)
        root.addHandler(handler)
