"""AWS Lambda ハンドラー - あすけん → MyFitnessPal 同期エントリーポイント.

環境変数:
    SECRET_NAME: Secrets Manager のシークレット名（省略時: "asken-myfitnesspal-sync/credentials"）
    TARGET_DATE: 同期対象日（YYYY-MM-DD 形式、省略時: JST 基準の当日）
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .logging_config import configure_logging
from .sync import run_sync

configure_logging()
logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")


def _get_target_date() -> date:
    target_date_str = os.environ.get("TARGET_DATE")
    if target_date_str:
        try:
            return date.fromisoformat(target_date_str)
        except ValueError as exc:
            raise ValueError(
                f"TARGET_DATE の形式が不正です（YYYY-MM-DD を期待）: {target_date_str!r}"
            ) from exc
    return datetime.now(_JST).date()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda ハンドラー."""
    target_date = _get_target_date()
    secret_name = os.environ.get("SECRET_NAME")
    logger.info("同期開始: target_date=%s", target_date)

    try:
        result = run_sync(target_date, secret_name=secret_name)
    except Exception:
        logger.exception("同期中にエラーが発生しました: target_date=%s", target_date)
        raise

    logger.info("同期完了: target_date=%s result=%s", target_date, result)
    return {
        "statusCode": 200,
        "target_date": target_date.isoformat(),
        "result": result,
    }
