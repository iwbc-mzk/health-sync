"""構造化ログ設定 - CloudWatch Logs 向け JSON フォーマット."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
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
        dt = datetime.fromtimestamp(created, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def configure_logging(level: int = logging.INFO) -> None:
    """ルートロガーに JSON フォーマッターを設定する."""
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
