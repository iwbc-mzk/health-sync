"""設定・Secrets Manager アクセス."""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import boto3

if TYPE_CHECKING:
    from mypy_boto3_secretsmanager import SecretsManagerClient

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_NAME = "asken-myfitnesspal-sync"
_JST = ZoneInfo("Asia/Tokyo")

# boto3 クライアントの遅延初期化（モジュールレベル変数、ウォームスタート最適化）
_secrets_client_instance: SecretsManagerClient | None = None


def _secrets_client() -> SecretsManagerClient:
    """boto3 Secrets Manager クライアントを遅延初期化して返す.

    モジュール import 時の NoRegionError を防ぐため、初回呼び出し時に初期化する。
    Lambda のウォームスタートではキャッシュ済みのインスタンスを再利用する。
    """
    global _secrets_client_instance
    if _secrets_client_instance is None:
        _secrets_client_instance = boto3.client("secretsmanager")
    return _secrets_client_instance


@dataclass
class Credentials:
    asken_email: str
    asken_password: str
    myfitnesspal_session_cookie: str

    def __repr__(self) -> str:
        return f"Credentials(asken_email={self.asken_email!r})"


def get_credentials(secret_name: str | None = None) -> Credentials:
    """Secrets Manager から認証情報を取得する.

    Args:
        secret_name: シークレット名（省略時: 環境変数 SECRET_NAME、さらに省略時はデフォルト値）

    Returns:
        Credentials

    Raises:
        ValueError: 必須キーが欠けているか形式が不正な場合
    """
    name = secret_name or os.environ.get("SECRET_NAME") or _DEFAULT_SECRET_NAME
    logger.debug("Secrets Manager からシークレットを取得: %s", name)

    response = _secrets_client().get_secret_value(SecretId=name)

    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str) or not secret_string:
        raise ValueError("SecretString が取得できません（バイナリシークレットは未サポート）")

    try:
        raw: Any = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise ValueError("シークレットの JSON 解析に失敗しました") from exc

    if not isinstance(raw, dict):
        raise ValueError("シークレットの形式が不正です（JSON オブジェクトを期待）")

    required_keys = ("asken_email", "asken_password", "myfitnesspal_session_cookie")
    missing = [k for k in required_keys if k not in raw]
    if missing:
        raise ValueError(f"シークレットに必須キーが存在しません: {missing}")

    return Credentials(
        asken_email=str(raw["asken_email"]),
        asken_password=str(raw["asken_password"]),
        myfitnesspal_session_cookie=str(raw["myfitnesspal_session_cookie"]),
    )


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def get_target_date() -> date:
    """同期対象日を返す.

    環境変数 TARGET_DATE（YYYY-MM-DD）が設定されていればその日付、
    なければ JST 基準の当日を返す。

    Raises:
        ValueError: TARGET_DATE の形式が不正な場合
    """
    target_date_str = os.environ.get("TARGET_DATE")
    if target_date_str:
        if not _DATE_RE.match(target_date_str):
            raise ValueError(
                f"TARGET_DATE の形式が不正です（YYYY-MM-DD を期待）: {target_date_str!r}"
            )
        try:
            return date.fromisoformat(target_date_str)
        except ValueError as exc:
            raise ValueError(
                f"TARGET_DATE の形式が不正です（YYYY-MM-DD を期待）: {target_date_str!r}"
            ) from exc
    return datetime.now(_JST).date()
