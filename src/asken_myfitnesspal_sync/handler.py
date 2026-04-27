"""AWS Lambda ハンドラー - あすけん → MyFitnessPal 同期エントリーポイント.

環境変数:
    SECRET_NAME: Secrets Manager のシークレット名（省略時: "asken-myfitnesspal-sync"）
    TARGET_DATE: 同期対象日（YYYY-MM-DD 形式、省略時: JST 基準の当日）
    MFP_AUTH_ALERT_SNS_TOPIC_ARN: MFP 認証失効を通知する SNS トピック ARN（省略時: 通知なし）
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import TYPE_CHECKING, Any

import boto3

from .config import get_target_date
from .logging_config import configure_logging
from .myfitnesspal_client import MfpAuthError
from .sync import run_sync

if TYPE_CHECKING:
    from mypy_boto3_sns import SNSClient

# モジュールレベルで設定することでコールドスタート時に一度だけ実行される。
# boto3 Secrets Manager クライアントは config._secrets_client_instance に
# 遅延シングルトンとしてキャッシュされ、ウォームスタートで再利用される。
configure_logging()
logger = logging.getLogger(__name__)

_sns_client_instance: SNSClient | None = None


def _sns_client() -> SNSClient:
    """boto3 SNS クライアントを遅延初期化して返す（ウォームスタート最適化）."""
    global _sns_client_instance
    if _sns_client_instance is None:
        _sns_client_instance = boto3.client("sns")
    return _sns_client_instance


def _notify_mfp_auth_failure(error: Exception, target_date: date) -> None:
    """MFP 認証失効を SNS で通知する.

    トピック ARN が未設定でも処理は中断しない（通知失敗で Lambda を二重失敗にしない）。
    """
    topic_arn = os.environ.get("MFP_AUTH_ALERT_SNS_TOPIC_ARN")
    if not topic_arn:
        logger.warning(
            "MFP_AUTH_ALERT_SNS_TOPIC_ARN が未設定のため SNS 通知をスキップします"
        )
        return
    try:
        _sns_client().publish(
            TopicArn=topic_arn,
            Subject="[asken-myfitnesspal-sync] MFP セッショントークン失効",
            Message=(
                "MyFitnessPal のセッションクッキーが無効化されました。\n"
                f"対象日: {target_date.isoformat()}\n"
                f"エラー: {error}\n\n"
                "対応: scripts/create_secret_mfp.sh を使い "
                "Secrets Manager の myfitnesspal_session_cookie を更新してください。"
            ),
        )
        logger.info("MFP 認証失効を SNS で通知しました")
    except Exception:
        logger.exception("SNS 通知に失敗しました")


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
    except MfpAuthError as exc:
        logger.exception("MFP 認証エラーが発生しました: target_date=%s", target_date)
        _notify_mfp_auth_failure(exc, target_date)
        raise
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
