"""設定モジュールのユニットテスト（moto で Secrets Manager をモック）."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import asken_myfitnesspal_sync.config as config_module
from asken_myfitnesspal_sync.config import Credentials, get_credentials, get_target_date

_JST = ZoneInfo("Asia/Tokyo")
_REGION = "ap-northeast-1"
_SECRET_NAME = "test-asken-myfitnesspal-sync"

_VALID_SECRET = {
    "asken_email": "asken@example.com",
    "asken_password": "asken_pass",
    "myfitnesspal_email": "mfp@example.com",
    "myfitnesspal_password": "mfp_pass",
}


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """moto に必要な偽 AWS 認証情報と region を設定する."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)


@pytest.fixture(autouse=True)
def reset_secrets_client():
    """_secrets_client_instance シングルトンをリセットする.

    moto のモックコンテキスト内で boto3 クライアントを新規生成させるため、
    各テスト前後でシングルトンを None にリセットする。
    """
    config_module._secrets_client_instance = None
    yield
    config_module._secrets_client_instance = None


@pytest.fixture
def sm_client():
    """moto でモックされた Secrets Manager クライアントを返すフィクスチャ."""
    with mock_aws():
        yield boto3.client("secretsmanager", region_name=_REGION)


@pytest.fixture
def secret(sm_client):
    """有効なシークレットを作成してシークレット名を返す."""
    sm_client.create_secret(
        Name=_SECRET_NAME,
        SecretString=json.dumps(_VALID_SECRET),
    )
    return _SECRET_NAME


class TestGetCredentials:
    def test_returns_credentials_with_valid_secret(self, sm_client, secret):
        creds = get_credentials(secret)

        assert isinstance(creds, Credentials)
        assert creds.asken_email == "asken@example.com"
        assert creds.asken_password == "asken_pass"
        assert creds.myfitnesspal_email == "mfp@example.com"
        assert creds.myfitnesspal_password == "mfp_pass"

    def test_uses_default_secret_name_when_not_specified(self, sm_client, monkeypatch):
        monkeypatch.setenv("SECRET_NAME", "")
        sm_client.create_secret(
            Name="asken-myfitnesspal-sync",
            SecretString=json.dumps(_VALID_SECRET),
        )
        get_credentials()

    def test_uses_env_secret_name_when_set(self, sm_client, monkeypatch):
        monkeypatch.setenv("SECRET_NAME", _SECRET_NAME)
        sm_client.create_secret(
            Name=_SECRET_NAME,
            SecretString=json.dumps(_VALID_SECRET),
        )
        creds = get_credentials()

        assert creds.asken_email == "asken@example.com"

    def test_explicit_secret_name_overrides_env(self, sm_client, monkeypatch):
        monkeypatch.setenv("SECRET_NAME", "env/secret")
        sm_client.create_secret(
            Name="explicit/secret",
            SecretString=json.dumps(_VALID_SECRET),
        )
        creds = get_credentials("explicit/secret")

        assert creds.asken_email == "asken@example.com"

    def test_raises_when_secret_string_is_missing(self, sm_client):
        sm_client.create_secret(
            Name=_SECRET_NAME,
            SecretString=json.dumps(_VALID_SECRET),
        )
        import unittest.mock as mock

        with (
            mock.patch.object(
                config_module._secrets_client(),
                "get_secret_value",
                return_value={},
            ),
            pytest.raises(ValueError, match="SecretString"),
        ):
            get_credentials(_SECRET_NAME)

    def test_raises_when_secret_string_is_empty(self, sm_client):
        import unittest.mock as mock

        sm_client.create_secret(
            Name=_SECRET_NAME,
            SecretString=json.dumps(_VALID_SECRET),
        )
        with (
            mock.patch.object(
                config_module._secrets_client(),
                "get_secret_value",
                return_value={"SecretString": ""},
            ),
            pytest.raises(ValueError, match="SecretString"),
        ):
            get_credentials(_SECRET_NAME)

    def test_raises_on_invalid_json(self, sm_client):
        sm_client.create_secret(Name=_SECRET_NAME, SecretString="not-json")
        with pytest.raises(ValueError, match="JSON"):
            get_credentials(_SECRET_NAME)

    def test_raises_when_secret_is_not_object(self, sm_client):
        sm_client.create_secret(
            Name=_SECRET_NAME,
            SecretString=json.dumps(["list"]),
        )
        with pytest.raises(ValueError, match="形式が不正"):
            get_credentials(_SECRET_NAME)

    @pytest.mark.parametrize(
        "missing_key",
        ["asken_email", "asken_password", "myfitnesspal_email", "myfitnesspal_password"],
    )
    def test_raises_when_required_key_missing(self, sm_client, missing_key: str):
        secret = {k: v for k, v in _VALID_SECRET.items() if k != missing_key}
        sm_client.create_secret(
            Name=_SECRET_NAME,
            SecretString=json.dumps(secret),
        )
        with pytest.raises(ValueError, match="必須キー"):
            get_credentials(_SECRET_NAME)

    def test_repr_does_not_expose_passwords(self, sm_client, secret):
        creds = get_credentials(secret)

        repr_str = repr(creds)
        assert "asken_pass" not in repr_str
        assert "mfp_pass" not in repr_str
        assert "asken@example.com" in repr_str

    def test_raises_client_error_when_secret_not_found(self, sm_client):
        """存在しないシークレット名を渡すと ClientError (ResourceNotFoundException) が伝播する."""
        with pytest.raises(ClientError) as exc_info:
            get_credentials("nonexistent-secret-xyz")

        assert exc_info.value.response["Error"]["Code"] == "ResourceNotFoundException"


class TestGetTargetDate:
    def test_returns_today_jst_when_env_not_set(self, monkeypatch):
        from unittest.mock import patch

        fixed_jst_dt = datetime(2024, 3, 15, 10, 0, 0, tzinfo=_JST)
        monkeypatch.delenv("TARGET_DATE", raising=False)
        with patch("asken_myfitnesspal_sync.config.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_jst_dt
            result = get_target_date()

        assert result == date(2024, 3, 15)
        mock_dt.now.assert_called_once_with(_JST)

    def test_returns_jst_date_not_utc_at_boundary(self, monkeypatch):
        from unittest.mock import patch

        utc_dt = datetime(2024, 3, 14, 15, 0, 0, tzinfo=UTC)
        jst_dt = utc_dt.astimezone(_JST)  # 2024-03-15 00:00 JST
        monkeypatch.delenv("TARGET_DATE", raising=False)
        with patch("asken_myfitnesspal_sync.config.datetime") as mock_dt:
            mock_dt.now.return_value = jst_dt
            result = get_target_date()

        assert result == date(2024, 3, 15)

    def test_returns_date_from_env_var(self, monkeypatch):
        monkeypatch.setenv("TARGET_DATE", "2024-03-15")
        result = get_target_date()

        assert result == date(2024, 3, 15)

    def test_raises_on_invalid_date_format_slash(self, monkeypatch):
        monkeypatch.setenv("TARGET_DATE", "2024/03/15")
        with pytest.raises(ValueError, match="TARGET_DATE"):
            get_target_date()

    def test_raises_on_compact_format_yyyymmdd(self, monkeypatch):
        monkeypatch.setenv("TARGET_DATE", "20240315")
        with pytest.raises(ValueError, match="TARGET_DATE"):
            get_target_date()

    def test_raises_on_iso_week_format(self, monkeypatch):
        monkeypatch.setenv("TARGET_DATE", "2024-W11-1")
        with pytest.raises(ValueError, match="TARGET_DATE"):
            get_target_date()

    def test_raises_on_non_date_string(self, monkeypatch):
        monkeypatch.setenv("TARGET_DATE", "not-a-date")
        with pytest.raises(ValueError, match="TARGET_DATE"):
            get_target_date()

    @pytest.mark.parametrize(
        "invalid_date",
        ["2024-02-30", "2024-13-01", "2024-00-15", "2024-01-00"],
    )
    def test_raises_on_impossible_calendar_date(self, monkeypatch, invalid_date: str):
        monkeypatch.setenv("TARGET_DATE", invalid_date)
        with pytest.raises(ValueError, match="TARGET_DATE"):
            get_target_date()
