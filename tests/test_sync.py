"""同期オーケストレーションのユニットテスト."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from asken_garmin_sync.asken_client import AskenAuthError, AskenError
from asken_garmin_sync.garmin_client import GarminAuthError, GarminError
from asken_garmin_sync.models import ActivityCalories, BodyComposition
from asken_garmin_sync.sync import (
    run_sync,
    sync_body_composition_to_garmin,
    sync_calories_to_asken,
)

TARGET_DATE = date(2026, 4, 14)
BODY = BodyComposition(date=TARGET_DATE, weight_kg=65.0, body_fat_percent=18.5)
ACTIVITY = ActivityCalories(date=TARGET_DATE, calories_burned=500)


# ─── sync_body_composition_to_garmin ────────────────────────────────────────


def test_sync_body_composition_success():
    """あすけんにデータがある → Garmin に登録 → True."""
    asken = MagicMock()
    garmin = MagicMock()
    asken.get_body_composition.return_value = BODY

    result = sync_body_composition_to_garmin(asken, garmin, TARGET_DATE)

    assert result is True
    asken.get_body_composition.assert_called_once_with(TARGET_DATE)
    garmin.add_body_composition.assert_called_once_with(BODY)


def test_sync_body_composition_no_data():
    """あすけんにデータなし（None） → Garmin 登録なし → False."""
    asken = MagicMock()
    garmin = MagicMock()
    asken.get_body_composition.return_value = None

    result = sync_body_composition_to_garmin(asken, garmin, TARGET_DATE)

    assert result is False
    garmin.add_body_composition.assert_not_called()


def test_sync_body_composition_asken_error_propagates():
    """あすけん取得エラー → 例外伝播."""
    asken = MagicMock()
    garmin = MagicMock()
    asken.get_body_composition.side_effect = AskenError("ページ取得失敗")

    with pytest.raises(AskenError):
        sync_body_composition_to_garmin(asken, garmin, TARGET_DATE)


def test_sync_body_composition_asken_auth_error_propagates():
    """あすけん認証エラー → 例外伝播."""
    asken = MagicMock()
    garmin = MagicMock()
    asken.get_body_composition.side_effect = AskenAuthError("認証失敗")

    with pytest.raises(AskenAuthError):
        sync_body_composition_to_garmin(asken, garmin, TARGET_DATE)


def test_sync_body_composition_garmin_error_propagates():
    """Garmin 登録エラー → 例外伝播."""
    asken = MagicMock()
    garmin = MagicMock()
    asken.get_body_composition.return_value = BODY
    garmin.add_body_composition.side_effect = GarminError("登録失敗")

    with pytest.raises(GarminError):
        sync_body_composition_to_garmin(asken, garmin, TARGET_DATE)


# ─── sync_calories_to_asken ─────────────────────────────────────────────────


def test_sync_calories_success():
    """Garmin にカロリーあり → あすけんに登録 → True."""
    garmin = MagicMock()
    asken = MagicMock()
    garmin.get_activity_calories.return_value = ACTIVITY

    result = sync_calories_to_asken(garmin, asken, TARGET_DATE)

    assert result is True
    garmin.get_activity_calories.assert_called_once_with(TARGET_DATE)
    asken.register_activity_calories.assert_called_once_with(TARGET_DATE, 500)


def test_sync_calories_zero_calories():
    """Garmin のカロリーが 0 → あすけん登録なし → False."""
    garmin = MagicMock()
    asken = MagicMock()
    garmin.get_activity_calories.return_value = ActivityCalories(
        date=TARGET_DATE, calories_burned=0
    )

    result = sync_calories_to_asken(garmin, asken, TARGET_DATE)

    assert result is False
    asken.register_activity_calories.assert_not_called()


def test_sync_calories_negative_calories():
    """Garmin のカロリーが負値 → あすけん登録なし → False."""
    garmin = MagicMock()
    asken = MagicMock()
    garmin.get_activity_calories.return_value = ActivityCalories(
        date=TARGET_DATE, calories_burned=-10
    )

    result = sync_calories_to_asken(garmin, asken, TARGET_DATE)

    assert result is False
    asken.register_activity_calories.assert_not_called()


def test_sync_calories_garmin_error_propagates():
    """Garmin 取得エラー → 例外伝播."""
    garmin = MagicMock()
    asken = MagicMock()
    garmin.get_activity_calories.side_effect = GarminError("取得失敗")

    with pytest.raises(GarminError):
        sync_calories_to_asken(garmin, asken, TARGET_DATE)


def test_sync_calories_garmin_auth_error_propagates():
    """Garmin 認証エラー → 例外伝播."""
    garmin = MagicMock()
    asken = MagicMock()
    garmin.get_activity_calories.side_effect = GarminAuthError("認証失敗")

    with pytest.raises(GarminAuthError):
        sync_calories_to_asken(garmin, asken, TARGET_DATE)


def test_sync_calories_asken_error_propagates():
    """あすけん登録エラー → 例外伝播."""
    garmin = MagicMock()
    asken = MagicMock()
    garmin.get_activity_calories.return_value = ACTIVITY
    asken.register_activity_calories.side_effect = AskenError("登録失敗")

    with pytest.raises(AskenError):
        sync_calories_to_asken(garmin, asken, TARGET_DATE)


# ─── run_sync ────────────────────────────────────────────────────────────────


def _make_secrets(garmin_tokens=None):
    """テスト用 Secrets オブジェクトを生成する."""
    from asken_garmin_sync.config import Secrets

    return Secrets(
        asken_email="test@example.com",
        asken_password="asken_pass",
        garmin_email="garmin@example.com",
        garmin_password="garmin_pass",
        garmin_tokens=garmin_tokens,
    )


@pytest.fixture
def mock_sync_env():
    """run_sync の外部依存をモックするフィクスチャ."""
    secrets = _make_secrets()

    with (
        patch("asken_garmin_sync.sync.get_secrets", return_value=secrets) as mock_get_secrets,
        patch("asken_garmin_sync.sync.load_garmin_tokens", return_value=False) as mock_load,
        patch("asken_garmin_sync.sync.save_garmin_tokens", return_value=True) as mock_save,
        patch("asken_garmin_sync.sync.cleanup_token_dir") as mock_cleanup,
        patch("asken_garmin_sync.sync.AskenClient") as mock_asken_cls,
        patch("asken_garmin_sync.sync.GarminClient") as mock_garmin_cls,
    ):
        mock_asken = MagicMock()
        mock_garmin = MagicMock()
        mock_asken_cls.return_value = mock_asken
        mock_garmin_cls.return_value = mock_garmin

        yield {
            "get_secrets": mock_get_secrets,
            "load_garmin_tokens": mock_load,
            "save_garmin_tokens": mock_save,
            "cleanup_token_dir": mock_cleanup,
            "asken": mock_asken,
            "garmin": mock_garmin,
        }


def test_run_sync_both_success(mock_sync_env):
    """両方向の同期が成功するケース."""
    mock_sync_env["asken"].get_body_composition.return_value = BODY
    mock_sync_env["garmin"].get_activity_calories.return_value = ACTIVITY

    result = run_sync(TARGET_DATE)

    assert result["body_composition"]["synced"] is True
    assert result["body_composition"]["error"] is None
    assert result["calories"]["synced"] is True
    assert result["calories"]["error"] is None
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_body_composition_fails_calories_continues(mock_sync_env):
    """体重同期が失敗してもカロリー同期は継続する."""
    mock_sync_env["asken"].get_body_composition.side_effect = AskenError("スクレイピング失敗")
    mock_sync_env["garmin"].get_activity_calories.return_value = ACTIVITY

    result = run_sync(TARGET_DATE)

    assert result["body_composition"]["synced"] is False
    assert result["body_composition"]["error"] is not None
    assert result["calories"]["synced"] is True
    assert result["calories"]["error"] is None
    # calories が成功したのでトークン保存する
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_calories_fails_body_composition_continues(mock_sync_env):
    """カロリー同期が失敗しても体重同期は維持される."""
    mock_sync_env["asken"].get_body_composition.return_value = BODY
    mock_sync_env["garmin"].get_activity_calories.side_effect = GarminError("API失敗")

    result = run_sync(TARGET_DATE)

    assert result["body_composition"]["synced"] is True
    assert result["body_composition"]["error"] is None
    assert result["calories"]["synced"] is False
    assert result["calories"]["error"] is not None
    # body_composition が成功したのでトークン保存する
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_body_composition_asken_auth_error_propagates(mock_sync_env):
    """体重同期中に AskenAuthError が発生した場合は即座に伝播し、Garmin 初期化後なのでトークンは保存する."""
    mock_sync_env["asken"].get_body_composition.side_effect = AskenAuthError("認証失敗")

    with pytest.raises(AskenAuthError):
        run_sync(TARGET_DATE)

    # Garmin 初期化は成功しているのでトークンを保存する
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_body_composition_garmin_auth_error_propagates(mock_sync_env):
    """体重同期中に GarminAuthError が発生した場合は即座に伝播し、Garmin 初期化後なのでトークンは保存する."""
    mock_sync_env["asken"].get_body_composition.return_value = BODY
    mock_sync_env["garmin"].add_body_composition.side_effect = GarminAuthError("認証失敗")

    with pytest.raises(GarminAuthError):
        run_sync(TARGET_DATE)

    # Garmin 初期化は成功しているのでトークンを保存する
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_calories_auth_error_propagates(mock_sync_env):
    """カロリー同期中に GarminAuthError が発生した場合は即座に伝播し、Garmin 初期化後なのでトークンは保存する."""
    mock_sync_env["asken"].get_body_composition.return_value = None
    mock_sync_env["garmin"].get_activity_calories.side_effect = GarminAuthError("認証失敗")

    with pytest.raises(GarminAuthError):
        run_sync(TARGET_DATE)

    # Garmin 初期化は成功しているのでトークンを保存する
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_garmin_init_auth_error_propagates(mock_sync_env):
    """Garmin クライアント初期化時に GarminAuthError が発生した場合は即座に伝播し、トークンは保存しない."""
    with patch("asken_garmin_sync.sync.GarminClient", side_effect=GarminAuthError("MFA要求")):
        with pytest.raises(GarminAuthError):
            run_sync(TARGET_DATE)

    # garmin_initialized = False のままなのでトークン保存しない
    mock_sync_env["save_garmin_tokens"].assert_not_called()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_asken_init_auth_error_propagates(mock_sync_env):
    """AskenClient 初期化時に AskenAuthError が発生した場合は即座に伝播し、トークンは保存しない."""
    with patch("asken_garmin_sync.sync.AskenClient", side_effect=AskenAuthError("ログインフォームなし")):
        with pytest.raises(AskenAuthError):
            run_sync(TARGET_DATE)

    # AskenClient 初期化失敗のため GarminClient は未初期化 → garmin_initialized = False
    mock_sync_env["save_garmin_tokens"].assert_not_called()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_both_operational_fail_saves_tokens(mock_sync_env):
    """両方向が操作エラーで失敗しても Garmin 初期化成功ならトークンは保存する."""
    mock_sync_env["asken"].get_body_composition.side_effect = AskenError("スクレイピング失敗")
    mock_sync_env["garmin"].get_activity_calories.side_effect = GarminError("API失敗")

    result = run_sync(TARGET_DATE)

    assert result["body_composition"]["synced"] is False
    assert result["body_composition"]["error"] is not None
    assert result["calories"]["synced"] is False
    assert result["calories"]["error"] is not None
    # Garmin 初期化が成功しているのでトークンを保存する
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_no_data(mock_sync_env):
    """データが存在しない場合（asken None, calories 0）でも Garmin 初期化成功ならトークン保存."""
    mock_sync_env["asken"].get_body_composition.return_value = None
    mock_sync_env["garmin"].get_activity_calories.return_value = ActivityCalories(
        date=TARGET_DATE, calories_burned=0
    )

    result = run_sync(TARGET_DATE)

    assert result["body_composition"]["synced"] is False
    assert result["body_composition"]["error"] is None
    assert result["calories"]["synced"] is False
    assert result["calories"]["error"] is None
    # Garmin 初期化が成功しているのでトークンを保存する
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_cleanup_called_even_on_exception(mock_sync_env):
    """get_secrets が例外を投げても cleanup_token_dir は呼ばれる."""
    mock_sync_env["get_secrets"].side_effect = ValueError("シークレット取得失敗")

    with pytest.raises(ValueError):
        run_sync(TARGET_DATE)

    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_passes_secret_name(mock_sync_env):
    """secret_name が get_secrets と save_garmin_tokens に渡される."""
    mock_sync_env["asken"].get_body_composition.return_value = BODY
    mock_sync_env["garmin"].get_activity_calories.return_value = ACTIVITY

    run_sync(TARGET_DATE, secret_name="custom-secret")

    mock_sync_env["get_secrets"].assert_called_once_with("custom-secret")
    mock_sync_env["save_garmin_tokens"].assert_called_once_with("custom-secret")


def test_run_sync_save_tokens_failure_still_cleans_up(mock_sync_env):
    """save_garmin_tokens が例外を投げても cleanup_token_dir は必ず実行される."""
    mock_sync_env["asken"].get_body_composition.return_value = BODY
    mock_sync_env["garmin"].get_activity_calories.return_value = ACTIVITY
    mock_sync_env["save_garmin_tokens"].side_effect = ValueError("保存失敗")

    # save_garmin_tokens の失敗は飲み込まれて run_sync は正常完了する
    result = run_sync(TARGET_DATE)

    assert result["body_composition"]["synced"] is True
    mock_sync_env["save_garmin_tokens"].assert_called_once()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_asken_init_operational_error_propagates(mock_sync_env):
    """AskenClient 初期化時に AskenError（操作エラー）が発生した場合は伝播し、トークンは保存しない."""
    with patch("asken_garmin_sync.sync.AskenClient", side_effect=AskenError("ネットワーク障害")):
        with pytest.raises(AskenError):
            run_sync(TARGET_DATE)

    # AskenClient 初期化失敗のため GarminClient は未初期化 → garmin_initialized = False
    mock_sync_env["save_garmin_tokens"].assert_not_called()
    mock_sync_env["cleanup_token_dir"].assert_called_once()


def test_run_sync_garmin_init_operational_error_propagates(mock_sync_env):
    """GarminClient 初期化時に GarminError（操作エラー）が発生した場合は伝播し、トークンは保存しない."""
    with patch("asken_garmin_sync.sync.GarminClient", side_effect=GarminError("接続失敗")):
        with pytest.raises(GarminError):
            run_sync(TARGET_DATE)

    # GarminClient 初期化失敗 → garmin_initialized = False
    mock_sync_env["save_garmin_tokens"].assert_not_called()
    mock_sync_env["cleanup_token_dir"].assert_called_once()
