"""AWS Lambda ハンドラー - あすけん ↔ Garmin Connect 同期エントリーポイント.

環境変数:
    SECRET_NAME: Secrets Manager のシークレット名（省略時: "asken-garmin-sync"）
    TARGET_DATE: 同期対象日（YYYY-MM-DD 形式、省略時: JST 基準の当日）
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .logging_config import configure_logging
from .sync import run_sync

# ロガー設定
# Lambda 実行環境では既存ハンドラーに JSON フォーマッターを適用する。
# モジュールレベルで呼び出すことでコールドスタート時に一度だけ設定される。
configure_logging()
logger = logging.getLogger(__name__)

_JST = ZoneInfo("Asia/Tokyo")

# ウォームスタート最適化について:
# boto3 Secrets Manager クライアントは config._secrets_client_instance に
# モジュールレベルでキャッシュされる（遅延シングルトンパターン）。
# 初回呼び出し時（コールドスタート中の最初の Lambda 実行）に生成され、
# 以降のウォームスタートではキャッシュ済みインスタンスが再利用される。
# handler.py での追加の boto3 クライアント生成は不要。


def _get_target_date() -> date:
    """同期対象日を決定する.

    環境変数 TARGET_DATE（YYYY-MM-DD 形式）が設定されている場合はその日付を使用する。
    未設定の場合は JST（Asia/Tokyo）基準の当日を返す。

    Returns:
        同期対象日

    Raises:
        ValueError: TARGET_DATE の形式が不正な場合
    """
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
    """Lambda ハンドラー.

    EventBridge Scheduler 等から定期実行される。
    対象日は JST 基準の当日、または環境変数 TARGET_DATE でオーバーライド可能。

    Args:
        event: Lambda イベント（EventBridge からは {"source": "aws.scheduler"} 等）
        context: Lambda コンテキスト

    Returns:
        同期結果を含むレスポンス辞書

    Raises:
        Exception: 同期中に予期しないエラーが発生した場合（Lambda が失敗としてマーク）
    """
    target_date = _get_target_date()
    # SECRET_NAME を明示的に読み取り run_sync に渡す。
    # config.get_secrets() も同じ環境変数を参照するが、ここで明示することで
    # handler レベルで設定を把握しやすくする。
    secret_name = os.environ.get("SECRET_NAME")
    logger.info("同期開始: target_date=%s", target_date)

    try:
        result = run_sync(target_date, secret_name=secret_name)
    except Exception:
        logger.exception("同期中にエラーが発生しました: target_date=%s", target_date)
        raise

    # 操作エラーが発生した場合は WARNING ログを出力する。
    # 認証エラーは例外として伝播するが、操作エラーは result に記録されるため
    # CloudWatch アラートのトリガーとなるログを明示的に出力する。
    errors = {
        k: v["error"] for k, v in result.items() if v.get("error") is not None  # type: ignore[index, attr-defined]
    }
    if errors:
        logger.warning("一部の同期でエラーが発生しました: %s", errors)

    logger.info(
        "同期完了: %s",
        json.dumps(result, ensure_ascii=False, default=str),
    )
    return {
        "statusCode": 200,
        "target_date": target_date.isoformat(),
        "result": result,
    }
