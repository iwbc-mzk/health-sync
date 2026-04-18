"""設定・Secrets Manager アクセス."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from mypy_boto3_secretsmanager import SecretsManagerClient

logger = logging.getLogger(__name__)

_DEFAULT_SECRET_NAME = "asken-myfitnesspal-sync/credentials"

_secrets_client: "SecretsManagerClient" = boto3.client(  # type: ignore[assignment]
    "secretsmanager",
    region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1"),
)


@dataclass
class Credentials:
    asken_email: str
    asken_password: str
    myfitnesspal_email: str
    myfitnesspal_password: str


def get_credentials(secret_name: str | None = None) -> Credentials:
    """Secrets Manager から認証情報を取得する.

    Args:
        secret_name: シークレット名（省略時: 環境変数 SECRET_NAME、さらに省略時はデフォルト値）

    Returns:
        Credentials

    Raises:
        Exception: シークレット取得失敗
    """
    name = secret_name or os.environ.get("SECRET_NAME") or _DEFAULT_SECRET_NAME
    logger.debug("Secrets Manager からシークレットを取得: %s", name)
    response = _secrets_client.get_secret_value(SecretId=name)
    secret = json.loads(response["SecretString"])
    return Credentials(
        asken_email=secret["asken_email"],
        asken_password=secret["asken_password"],
        myfitnesspal_email=secret["myfitnesspal_email"],
        myfitnesspal_password=secret["myfitnesspal_password"],
    )
