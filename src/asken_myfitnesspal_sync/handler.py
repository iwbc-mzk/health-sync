"""AWS Lambda ハンドラー - あすけん → MyFitnessPal 同期エントリーポイント.

環境変数:
    SECRET_NAME: Secrets Manager のシークレット名（省略時: "asken-myfitnesspal-sync/credentials"）
    TARGET_DATE: 同期対象日（YYYY-MM-DD 形式、省略時: JST 基準の当日）
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .config import get_target_date
from .logging_config import configure_logging
from .sync import run_sync

# モジュールレベルで設定することでコールドスタート時に一度だけ実行される。
# boto3 Secrets Manager クライアントは config._secrets_client_instance に
# 遅延シングルトンとしてキャッシュされ、ウォームスタートで再利用される。
configure_logging()
logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda ハンドラー.

    EventBridge Scheduler 等から定期実行される。
    対象日は JST 基準の当日、または環境変数 TARGET_DATE でオーバーライド可能。

    Returns:
        同期結果を含むレスポンス辞書

    Raises:
        Exception: 認証エラー等の致命的なエラーが発生した場合（Lambda が失敗としてマーク）
    """
    secret_name = os.environ.get("SECRET_NAME")
    target_date = get_target_date()
    logger.info("同期開始: target_date=%s", target_date)

    try:
        result = run_sync(target_date, secret_name=secret_name)
    except Exception:
        logger.exception("同期中にエラーが発生しました: target_date=%s", target_date)
        raise

    if result.get("errors"):
        logger.warning(
            "一部の食事区分で同期エラーが発生しました: errors=%d", result["errors"]
        )

    logger.info(
        "同期完了: %s",
        json.dumps(result, ensure_ascii=False, default=str),
    )
    return {
        "statusCode": 200,
        "target_date": target_date.isoformat(),
        "result": result,
    }
