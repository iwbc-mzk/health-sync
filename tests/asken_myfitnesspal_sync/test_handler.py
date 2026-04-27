"""Lambda ハンドラーのユニットテスト."""
from __future__ import annotations

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from asken_myfitnesspal_sync.handler import lambda_handler
from asken_myfitnesspal_sync.myfitnesspal_client import MfpAuthError


class TestLambdaHandler:
    def test_returns_200_on_success(self):
        mock_result = {"registered": 2, "skipped": 1, "errors": 0}
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch(
                "asken_myfitnesspal_sync.handler.run_sync",
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
                "asken_myfitnesspal_sync.handler.get_target_date",
                side_effect=ValueError("TARGET_DATE の形式が不正です"),
            ),
            pytest.raises(ValueError, match="TARGET_DATE"),
        ):
            lambda_handler({}, MagicMock())

    def test_reraises_when_run_sync_fails(self):
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch(
                "asken_myfitnesspal_sync.handler.run_sync",
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
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch("asken_myfitnesspal_sync.handler.run_sync", mock_run_sync),
            patch.dict(os.environ, {"SECRET_NAME": "custom/secret"}, clear=False),
        ):
            lambda_handler({}, MagicMock())

        mock_run_sync.assert_called_once_with(date(2024, 3, 15), secret_name="custom/secret")

    def test_warning_logged_and_200_returned_when_errors_present(self):
        mock_result = {"registered": 1, "skipped": 0, "errors": 2}
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch(
                "asken_myfitnesspal_sync.handler.run_sync",
                return_value=mock_result,
            ),
            patch("asken_myfitnesspal_sync.handler.logger") as mock_logger,
        ):
            response = lambda_handler({}, MagicMock())

        assert response["statusCode"] == 200
        mock_logger.warning.assert_called_once()
        warning_args = mock_logger.warning.call_args[0]
        assert "errors" in warning_args[0]

    def test_no_warning_when_no_errors(self):
        mock_result = {"registered": 2, "skipped": 1, "errors": 0}
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch(
                "asken_myfitnesspal_sync.handler.run_sync",
                return_value=mock_result,
            ),
            patch("asken_myfitnesspal_sync.handler.logger") as mock_logger,
        ):
            lambda_handler({}, MagicMock())

        mock_logger.warning.assert_not_called()

    def test_uses_none_secret_name_when_env_absent(self):
        mock_run_sync = MagicMock(return_value={})
        env_without_secret = {k: v for k, v in os.environ.items() if k != "SECRET_NAME"}
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2024, 3, 15),
            ),
            patch("asken_myfitnesspal_sync.handler.run_sync", mock_run_sync),
            patch.dict(os.environ, env_without_secret, clear=True),
        ):
            lambda_handler({}, MagicMock())

        mock_run_sync.assert_called_once_with(date(2024, 3, 15), secret_name=None)


class TestMfpAuthFailureNotification:
    """MfpAuthError 発生時の SNS 通知をテストする."""

    def test_publishes_to_sns_on_mfp_auth_error(self):
        """MfpAuthError 発生時に SNS publish が呼ばれてから例外が再 raise されること."""
        sns_mock = MagicMock()
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2026, 4, 25),
            ),
            patch(
                "asken_myfitnesspal_sync.handler.run_sync",
                side_effect=MfpAuthError("cookie expired"),
            ),
            patch(
                "asken_myfitnesspal_sync.handler._sns_client",
                return_value=sns_mock,
            ),
            patch.dict(
                os.environ,
                {"MFP_AUTH_ALERT_SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:111:topic"},
            ),
            pytest.raises(MfpAuthError, match="cookie expired"),
        ):
            lambda_handler({}, MagicMock())

        sns_mock.publish.assert_called_once()
        kwargs = sns_mock.publish.call_args.kwargs
        assert kwargs["TopicArn"] == "arn:aws:sns:us-east-1:111:topic"
        assert "MFP" in kwargs["Subject"]
        assert "cookie expired" in kwargs["Message"]
        assert "2026-04-25" in kwargs["Message"]

    def test_skips_publish_when_topic_arn_not_set(self):
        """SNS トピック ARN が未設定なら publish せず、それでも例外は再 raise."""
        sns_mock = MagicMock()
        env_without_sns = {
            k: v for k, v in os.environ.items() if k != "MFP_AUTH_ALERT_SNS_TOPIC_ARN"
        }
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2026, 4, 25),
            ),
            patch(
                "asken_myfitnesspal_sync.handler.run_sync",
                side_effect=MfpAuthError("expired"),
            ),
            patch(
                "asken_myfitnesspal_sync.handler._sns_client",
                return_value=sns_mock,
            ),
            patch.dict(os.environ, env_without_sns, clear=True),
            pytest.raises(MfpAuthError),
        ):
            lambda_handler({}, MagicMock())

        sns_mock.publish.assert_not_called()

    def test_sns_publish_failure_is_swallowed(self):
        """SNS publish が失敗しても元の MfpAuthError は伝播し、failure ログが記録されること."""
        sns_mock = MagicMock()
        sns_mock.publish.side_effect = RuntimeError("SNS down")
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2026, 4, 25),
            ),
            patch(
                "asken_myfitnesspal_sync.handler.run_sync",
                side_effect=MfpAuthError("expired"),
            ),
            patch(
                "asken_myfitnesspal_sync.handler._sns_client",
                return_value=sns_mock,
            ),
            patch.dict(
                os.environ,
                {"MFP_AUTH_ALERT_SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:111:t"},
            ),
            patch("asken_myfitnesspal_sync.handler.logger") as mock_logger,
            pytest.raises(MfpAuthError, match="expired"),
        ):
            lambda_handler({}, MagicMock())

        # SNS publish 失敗は logger.exception として記録される（運用観測の要）
        exception_calls = [
            call for call in mock_logger.exception.call_args_list
            if "SNS" in (call.args[0] if call.args else "")
        ]
        assert len(exception_calls) >= 1, "SNS publish 失敗時に logger.exception が呼ばれていない"

    def test_no_sns_publish_for_non_auth_error(self):
        """MfpAuthError 以外の例外では SNS publish を行わないこと."""
        sns_mock = MagicMock()
        with (
            patch(
                "asken_myfitnesspal_sync.handler.get_target_date",
                return_value=date(2026, 4, 25),
            ),
            patch(
                "asken_myfitnesspal_sync.handler.run_sync",
                side_effect=RuntimeError("other error"),
            ),
            patch(
                "asken_myfitnesspal_sync.handler._sns_client",
                return_value=sns_mock,
            ),
            patch.dict(
                os.environ,
                {"MFP_AUTH_ALERT_SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:111:t"},
            ),
            pytest.raises(RuntimeError),
        ):
            lambda_handler({}, MagicMock())

        sns_mock.publish.assert_not_called()
