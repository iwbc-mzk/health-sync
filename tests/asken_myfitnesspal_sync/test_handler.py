"""Lambda ハンドラーのユニットテスト."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.asken_myfitnesspal_sync.handler import lambda_handler


class TestLambdaHandler:
    def test_returns_200_on_success(self):
        mock_result = {"registered": 2, "skipped": 1, "errors": 0}
        with (
            patch(
                "src.asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch(
                "src.asken_myfitnesspal_sync.handler.run_sync",
                return_value=mock_result,
            ),
        ):
            response = lambda_handler({}, MagicMock())

        assert response["statusCode"] == 200
        assert response["target_date"] == "2024-03-15"
        assert response["result"] == mock_result

    def test_reraises_when_get_target_date_fails(self):
        with (
            patch(
                "src.asken_myfitnesspal_sync.handler.get_target_date",
                side_effect=ValueError("TARGET_DATE の形式が不正です"),
            ),
            pytest.raises(ValueError, match="TARGET_DATE"),
        ):
            lambda_handler({}, MagicMock())

    def test_reraises_when_run_sync_fails(self):
        with (
            patch(
                "src.asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch(
                "src.asken_myfitnesspal_sync.handler.run_sync",
                side_effect=RuntimeError("auth error"),
            ),
            pytest.raises(RuntimeError, match="auth error"),
        ):
            lambda_handler({}, MagicMock())

    def test_uses_secret_name_from_env(self):
        import os

        mock_run_sync = MagicMock(return_value={})
        with (
            patch(
                "src.asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch("src.asken_myfitnesspal_sync.handler.run_sync", mock_run_sync),
            patch.dict(os.environ, {"SECRET_NAME": "custom/secret"}, clear=False),
        ):
            lambda_handler({}, MagicMock())

        mock_run_sync.assert_called_once_with(date(2024, 3, 15), secret_name="custom/secret")

    def test_uses_none_secret_name_when_env_absent(self):
        import os

        mock_run_sync = MagicMock(return_value={})
        env_without_secret = {k: v for k, v in os.environ.items() if k != "SECRET_NAME"}
        with (
            patch(
                "src.asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch("src.asken_myfitnesspal_sync.handler.run_sync", mock_run_sync),
            patch.dict(os.environ, env_without_secret, clear=True),
        ):
            lambda_handler({}, MagicMock())

        mock_run_sync.assert_called_once_with(date(2024, 3, 15), secret_name=None)
