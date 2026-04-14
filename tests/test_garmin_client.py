"""Garmin Connect クライアントのユニットテスト."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from asken_garmin_sync.garmin_client import (
    GarminAuthError,
    GarminClient,
    GarminError,
    _call_with_retry,
)
from asken_garmin_sync.models import ActivityCalories, BodyComposition

# ─── テスト用定数 ──────────────────────────────────────────────────────────────

_EMAIL = "test@example.com"
_PASSWORD = "secret"
_DATE = date(2026, 4, 13)


# ─── garminconnect 例外クラスをインポート ────────────────────────────────────

from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)


# ─── フィクスチャ ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_garmin_cls():
    """Garmin クラスをモックするパッチ."""
    with patch("asken_garmin_sync.garmin_client.Garmin") as mock_cls:
        yield mock_cls


@pytest.fixture
def garmin_instance(mock_garmin_cls):
    """ログイン済みの Garmin インスタンスモック."""
    instance = MagicMock()
    mock_garmin_cls.return_value = instance
    return instance


@pytest.fixture
def token_dir(tmp_path: Path) -> Path:
    """有効なトークンファイルを持つ一時ディレクトリ."""
    d = tmp_path / ".garminconnect"
    d.mkdir()
    (d / "oauth2_token.json").write_text(
        json.dumps({"access_token": "tok123"}), encoding="utf-8"
    )
    return d


@pytest.fixture
def empty_token_dir(tmp_path: Path) -> Path:
    """JSON ファイルが存在しない一時ディレクトリ."""
    d = tmp_path / ".garminconnect_empty"
    d.mkdir()
    return d


# ─── _call_with_retry ─────────────────────────────────────────────────────────


class TestCallWithRetry:
    """_call_with_retry のユニットテスト."""

    def test_success_on_first_attempt(self):
        """正常呼び出しはリトライなしで結果を返す."""
        fn = MagicMock(return_value={"key": "value"})
        result = _call_with_retry(fn, "arg1", max_retries=3)
        assert result == {"key": "value"}
        fn.assert_called_once_with("arg1")

    def test_auth_error_raises_immediately(self):
        """認証エラーはリトライせず即座に GarminAuthError を送出する."""
        fn = MagicMock(side_effect=GarminConnectAuthenticationError("auth fail"))
        with pytest.raises(GarminAuthError, match="認証エラー"):
            _call_with_retry(fn, max_retries=3)
        fn.assert_called_once()

    def test_too_many_requests_retries(self):
        """429 エラーは最大回数リトライする."""
        fn = MagicMock(
            side_effect=[
                GarminConnectTooManyRequestsError("429"),
                GarminConnectTooManyRequestsError("429"),
                {"calories": 500},
            ]
        )
        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            result = _call_with_retry(fn, max_retries=3)
        assert result == {"calories": 500}
        assert fn.call_count == 3

    def test_connection_error_retries(self):
        """接続エラーは最大回数リトライする."""
        fn = MagicMock(
            side_effect=[
                GarminConnectConnectionError("conn"),
                {"ok": True},
            ]
        )
        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            result = _call_with_retry(fn, max_retries=3)
        assert result == {"ok": True}
        assert fn.call_count == 2

    def test_max_retries_exceeded_raises_garmin_error(self):
        """max_retries を超えると GarminError を送出する."""
        fn = MagicMock(side_effect=GarminConnectTooManyRequestsError("429"))
        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            with pytest.raises(GarminError, match="4 回失敗"):
                _call_with_retry(fn, max_retries=3)
        assert fn.call_count == 4  # 初回 + 3回リトライ

    def test_auth_error_not_retried_even_after_connection_errors(self):
        """接続エラーの後に認証エラーが来た場合もリトライせず送出する."""
        fn = MagicMock(
            side_effect=[
                GarminConnectConnectionError("conn"),
                GarminConnectAuthenticationError("auth"),
            ]
        )
        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            with pytest.raises(GarminAuthError):
                _call_with_retry(fn, max_retries=3)
        assert fn.call_count == 2

    def test_exponential_backoff_delays(self):
        """リトライ間隔が指数バックオフになっていることを確認する."""
        fn = MagicMock(
            side_effect=[
                GarminConnectConnectionError("e1"),
                GarminConnectConnectionError("e2"),
                GarminConnectConnectionError("e3"),
                GarminConnectConnectionError("e4"),
            ]
        )
        with patch("asken_garmin_sync.garmin_client.time.sleep") as mock_sleep:
            with pytest.raises(GarminError):
                _call_with_retry(fn, max_retries=3)
        # 遅延: 1.0, 2.0, 4.0 秒
        assert mock_sleep.call_args_list == [call(1.0), call(2.0), call(4.0)]

    def test_invalid_max_retries_raises_value_error(self):
        """max_retries < 0 は ValueError を送出する."""
        with pytest.raises(ValueError, match="max_retries"):
            _call_with_retry(MagicMock(), max_retries=-1)


# ─── GarminClient 初期化 ──────────────────────────────────────────────────────


class TestGarminClientInit:
    """GarminClient 初期化のユニットテスト."""

    def test_login_with_tokenstore_when_tokens_exist(
        self, mock_garmin_cls, garmin_instance, token_dir
    ):
        """トークンファイルが存在する場合は tokenstore を指定してログインする."""
        client = GarminClient(_EMAIL, _PASSWORD, token_dir=token_dir)

        garmin_instance.login.assert_called_once_with(tokenstore=str(token_dir))
        assert client._client is garmin_instance

    def test_login_without_tokenstore_when_no_tokens(
        self, mock_garmin_cls, garmin_instance, empty_token_dir
    ):
        """トークンファイルが存在しない場合は tokenstore なしでログインする."""
        client = GarminClient(_EMAIL, _PASSWORD, token_dir=empty_token_dir)

        garmin_instance.login.assert_called_once_with(tokenstore=None)
        assert client._client is garmin_instance

    def test_login_without_tokenstore_when_dir_missing(
        self, mock_garmin_cls, garmin_instance, tmp_path
    ):
        """トークンディレクトリ自体が存在しない場合は tokenstore なしでログインする."""
        nonexistent = tmp_path / "no_such_dir"
        client = GarminClient(_EMAIL, _PASSWORD, token_dir=nonexistent)

        garmin_instance.login.assert_called_once_with(tokenstore=None)
        assert client._client is garmin_instance

    def test_auth_error_raises_garmin_auth_error(
        self, mock_garmin_cls, garmin_instance, tmp_path
    ):
        """認証失敗時は GarminAuthError を送出する."""
        garmin_instance.login.side_effect = GarminConnectAuthenticationError(
            "invalid credentials"
        )
        nonexistent = tmp_path / "no_tokens"

        with pytest.raises(GarminAuthError, match="ログインに失敗"):
            GarminClient(_EMAIL, _PASSWORD, token_dir=nonexistent)

    def test_connection_error_raises_garmin_error(
        self, mock_garmin_cls, garmin_instance, tmp_path
    ):
        """接続エラー時は GarminError を送出する."""
        garmin_instance.login.side_effect = GarminConnectConnectionError(
            "network failure"
        )
        nonexistent = tmp_path / "no_tokens"

        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            with pytest.raises(GarminError, match="ネットワークエラー"):
                GarminClient(_EMAIL, _PASSWORD, token_dir=nonexistent)

    def test_rate_limit_on_login_raises_garmin_error(
        self, mock_garmin_cls, garmin_instance, tmp_path
    ):
        """ログイン時の 429 エラーは GarminError を送出する."""
        garmin_instance.login.side_effect = GarminConnectTooManyRequestsError("429")
        nonexistent = tmp_path / "no_tokens"

        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            with pytest.raises(GarminError, match="ネットワークエラー"):
                GarminClient(_EMAIL, _PASSWORD, token_dir=nonexistent)

    def test_garmin_constructed_with_credentials_and_no_mfa(
        self, mock_garmin_cls, garmin_instance, tmp_path
    ):
        """Garmin インスタンスがメールアドレス・パスワード・prompt_mfa=None で構築される."""
        nonexistent = tmp_path / "no_tokens"
        GarminClient(_EMAIL, _PASSWORD, token_dir=nonexistent)

        mock_garmin_cls.assert_called_once_with(
            email=_EMAIL, password=_PASSWORD, prompt_mfa=None
        )

    def test_login_retries_on_connection_error(
        self, mock_garmin_cls, garmin_instance, tmp_path
    ):
        """ログイン時の接続エラーは _call_with_retry によりリトライされる."""
        garmin_instance.login.side_effect = [
            GarminConnectConnectionError("timeout"),
            None,  # 2回目成功
        ]
        nonexistent = tmp_path / "no_tokens"

        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            client = GarminClient(_EMAIL, _PASSWORD, token_dir=nonexistent)

        assert garmin_instance.login.call_count == 2
        assert client._client is garmin_instance


# ─── add_body_composition ─────────────────────────────────────────────────────


class TestAddBodyComposition:
    """GarminClient.add_body_composition のユニットテスト."""

    @pytest.fixture
    def garmin_client(self, mock_garmin_cls, garmin_instance, tmp_path):
        """初期化済み GarminClient."""
        return GarminClient(_EMAIL, _PASSWORD, token_dir=tmp_path / "no_tokens")

    def test_registers_weight_and_body_fat(self, garmin_client, garmin_instance):
        """体重と体脂肪率を JST オフセット付きタイムスタンプで登録する."""
        body = BodyComposition(date=_DATE, weight_kg=70.5, body_fat_percent=20.0)
        garmin_client.add_body_composition(body)

        garmin_instance.add_body_composition.assert_called_once_with(
            "2026-04-13T00:00:00+09:00",
            70.5,
            20.0,
        )

    def test_registers_weight_without_body_fat(self, garmin_client, garmin_instance):
        """体脂肪率が None の場合も正常に登録する."""
        body = BodyComposition(date=_DATE, weight_kg=65.0, body_fat_percent=None)
        garmin_client.add_body_composition(body)

        garmin_instance.add_body_composition.assert_called_once_with(
            "2026-04-13T00:00:00+09:00",
            65.0,
            None,
        )

    def test_too_many_requests_retries_and_succeeds(
        self, garmin_client, garmin_instance
    ):
        """429 エラー後にリトライして成功する."""
        garmin_instance.add_body_composition.side_effect = [
            GarminConnectTooManyRequestsError("429"),
            {"result": "OK"},
        ]
        body = BodyComposition(date=_DATE, weight_kg=70.0)

        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            garmin_client.add_body_composition(body)

        assert garmin_instance.add_body_composition.call_count == 2

    def test_auth_error_raises_garmin_auth_error(self, garmin_client, garmin_instance):
        """認証エラーは GarminAuthError を送出する."""
        garmin_instance.add_body_composition.side_effect = (
            GarminConnectAuthenticationError("auth")
        )
        body = BodyComposition(date=_DATE, weight_kg=70.0)

        with pytest.raises(GarminAuthError):
            garmin_client.add_body_composition(body)

    def test_persistent_error_raises_garmin_error(
        self, garmin_client, garmin_instance
    ):
        """リトライ上限を超えると GarminError を送出する."""
        garmin_instance.add_body_composition.side_effect = (
            GarminConnectTooManyRequestsError("429")
        )
        body = BodyComposition(date=_DATE, weight_kg=70.0)

        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            with pytest.raises(GarminError):
                garmin_client.add_body_composition(body)


# ─── get_activity_calories ────────────────────────────────────────────────────


class TestGetActivityCalories:
    """GarminClient.get_activity_calories のユニットテスト."""

    @pytest.fixture
    def garmin_client(self, mock_garmin_cls, garmin_instance, tmp_path):
        """初期化済み GarminClient."""
        return GarminClient(_EMAIL, _PASSWORD, token_dir=tmp_path / "no_tokens")

    def test_returns_active_kilocalories(self, garmin_client, garmin_instance):
        """activeKilocalories を正しく返す."""
        garmin_instance.get_stats.return_value = {
            "totalKilocalories": 2500,
            "activeKilocalories": 450,
            "bmrKilocalories": 1800,
        }
        result = garmin_client.get_activity_calories(_DATE)

        assert result == ActivityCalories(date=_DATE, calories_burned=450)
        garmin_instance.get_stats.assert_called_once_with("2026-04-13")

    def test_returns_zero_when_active_kilocalories_missing(
        self, garmin_client, garmin_instance
    ):
        """activeKilocalories が存在しない場合は 0 を返す."""
        garmin_instance.get_stats.return_value = {
            "totalKilocalories": 2000,
            "bmrKilocalories": 1800,
        }
        result = garmin_client.get_activity_calories(_DATE)

        assert result == ActivityCalories(date=_DATE, calories_burned=0)

    def test_truncates_float_calories_to_int(self, garmin_client, garmin_instance):
        """float のカロリー値を int に切り捨てる."""
        garmin_instance.get_stats.return_value = {"activeKilocalories": 350.9}
        result = garmin_client.get_activity_calories(_DATE)

        assert result.calories_burned == 350

    def test_auth_error_raises_garmin_auth_error(self, garmin_client, garmin_instance):
        """認証エラーは GarminAuthError を送出する."""
        garmin_instance.get_stats.side_effect = GarminConnectAuthenticationError(
            "auth"
        )
        with pytest.raises(GarminAuthError):
            garmin_client.get_activity_calories(_DATE)

    def test_too_many_requests_retries_and_succeeds(
        self, garmin_client, garmin_instance
    ):
        """429 エラー後にリトライして成功する."""
        garmin_instance.get_stats.side_effect = [
            GarminConnectTooManyRequestsError("429"),
            GarminConnectTooManyRequestsError("429"),
            {"activeKilocalories": 300},
        ]
        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            result = garmin_client.get_activity_calories(_DATE)

        assert result.calories_burned == 300
        assert garmin_instance.get_stats.call_count == 3

    def test_persistent_error_raises_garmin_error(
        self, garmin_client, garmin_instance
    ):
        """リトライ上限を超えると GarminError を送出する."""
        garmin_instance.get_stats.side_effect = GarminConnectTooManyRequestsError(
            "429"
        )
        with patch("asken_garmin_sync.garmin_client.time.sleep"):
            with pytest.raises(GarminError):
                garmin_client.get_activity_calories(_DATE)

    def test_returns_zero_when_active_kilocalories_is_none_value(
        self, garmin_client, garmin_instance
    ):
        """activeKilocalories キーが存在するが値が None の場合は 0 を返す."""
        garmin_instance.get_stats.return_value = {"activeKilocalories": None}
        result = garmin_client.get_activity_calories(_DATE)

        assert result == ActivityCalories(date=_DATE, calories_burned=0)

    def test_returns_zero_when_active_kilocalories_is_negative(
        self, garmin_client, garmin_instance
    ):
        """activeKilocalories が負値の場合は 0 に丸める."""
        garmin_instance.get_stats.return_value = {"activeKilocalories": -5}
        result = garmin_client.get_activity_calories(_DATE)

        assert result == ActivityCalories(date=_DATE, calories_burned=0)
