"""同期ロジックのユニットテスト."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from asken_myfitnesspal_sync.models import DailyMeals, MealNutrition, MealType
from asken_myfitnesspal_sync.sync import (
    MealSyncResult,
    _aggregate_nutrition,
    _is_same_nutrition,
    run_sync,
    sync_meals,
)

_DATE = date(2024, 3, 15)

_BREAKFAST = MealNutrition(MealType.BREAKFAST, 400.0, 20.0, 10.0, 60.0)
_LUNCH = MealNutrition(MealType.LUNCH, 600.0, 30.0, 15.0, 80.0)
_DINNER = MealNutrition(MealType.DINNER, 500.0, 25.0, 12.0, 70.0)
_SNACK = MealNutrition(MealType.SNACKS, 200.0, 5.0, 8.0, 30.0)


def _make_credentials(
    asken_email: str = "a@example.com",
    asken_password: str = "apass",
    mfp_session_cookie: str = "test_session_token",
):
    from asken_myfitnesspal_sync.config import Credentials

    return Credentials(asken_email, asken_password, mfp_session_cookie)


class TestAggregateNutrition:
    def test_single_entry_returned_as_is(self):
        result = _aggregate_nutrition([_BREAKFAST], MealType.BREAKFAST)
        assert result.calories == pytest.approx(_BREAKFAST.calories)
        assert result.protein_g == pytest.approx(_BREAKFAST.protein_g)

    def test_multiple_entries_summed(self):
        e1 = MealNutrition(MealType.SNACKS, 100.0, 5.0, 3.0, 15.0)
        e2 = MealNutrition(MealType.SNACKS, 150.0, 7.0, 4.0, 20.0)
        result = _aggregate_nutrition([e1, e2], MealType.SNACKS)
        assert result.calories == pytest.approx(250.0)
        assert result.protein_g == pytest.approx(12.0)
        assert result.fat_g == pytest.approx(7.0)
        assert result.carbs_g == pytest.approx(35.0)


class TestIsSameNutrition:
    def test_empty_mfp_returns_false(self):
        assert _is_same_nutrition(_BREAKFAST, []) is False

    def test_single_matching_entry_returns_true(self):
        assert _is_same_nutrition(_BREAKFAST, [_BREAKFAST]) is True

    def test_single_different_entry_returns_false(self):
        different = MealNutrition(MealType.BREAKFAST, 999.0, 20.0, 10.0, 60.0)
        assert _is_same_nutrition(_BREAKFAST, [different]) is False

    def test_multiple_entries_summed_and_matched(self):
        e1 = MealNutrition(MealType.SNACKS, 100.0, 2.5, 4.0, 15.0)
        e2 = MealNutrition(MealType.SNACKS, 100.0, 2.5, 4.0, 15.0)
        combined = MealNutrition(MealType.SNACKS, 200.0, 5.0, 8.0, 30.0)
        assert _is_same_nutrition(combined, [e1, e2]) is True


class TestSyncMeals:
    def _make_mfp_mock(self) -> MagicMock:
        mock = MagicMock()
        mock.get_meal_entries.return_value = []
        return mock

    def _patch_clients(self, asken_mock: MagicMock, mfp_mock: MagicMock):
        return (
            patch(
                "asken_myfitnesspal_sync.sync.AskenClient",
                return_value=asken_mock,
            ),
            patch(
                "asken_myfitnesspal_sync.sync.MyFitnessPalClient",
                return_value=mfp_mock,
            ),
        )

    def test_new_registration_when_mfp_empty(self):
        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(
            date=_DATE, meals=[_BREAKFAST]
        )
        mfp_mock = self._make_mfp_mock()
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp:
            result = sync_meals(_DATE, _make_credentials())

        mfp_mock.add_meal_entry.assert_called_once_with(_DATE, _BREAKFAST)
        mfp_mock.delete_meal_entries.assert_not_called()
        assert result.registered == 1
        assert result.skipped == 0
        assert result.error_count == 0

    def test_skip_when_nutrition_identical(self):
        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(
            date=_DATE, meals=[_BREAKFAST]
        )
        mfp_mock = self._make_mfp_mock()
        mfp_mock.get_meal_entries.side_effect = lambda d, mt: (
            [_BREAKFAST] if mt == MealType.BREAKFAST else []
        )
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp:
            result = sync_meals(_DATE, _make_credentials())

        mfp_mock.add_meal_entry.assert_not_called()
        mfp_mock.delete_meal_entries.assert_not_called()
        assert result.skipped == 1
        assert result.registered == 0

    def test_overwrite_when_nutrition_differs(self):
        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(
            date=_DATE, meals=[_BREAKFAST]
        )
        old_entry = MealNutrition(MealType.BREAKFAST, 999.0, 20.0, 10.0, 60.0)
        mfp_mock = self._make_mfp_mock()
        mfp_mock.get_meal_entries.side_effect = lambda d, mt: (
            [old_entry] if mt == MealType.BREAKFAST else []
        )
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp:
            result = sync_meals(_DATE, _make_credentials())

        mfp_mock.delete_meal_entries.assert_called_once_with(_DATE, MealType.BREAKFAST)
        mfp_mock.add_meal_entry.assert_called_once_with(_DATE, _BREAKFAST)
        assert result.registered == 1

    def test_skip_meal_type_when_asken_has_no_data(self):
        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(date=_DATE, meals=[])
        mfp_mock = self._make_mfp_mock()
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp:
            result = sync_meals(_DATE, _make_credentials())

        mfp_mock.add_meal_entry.assert_not_called()
        mfp_mock.get_meal_entries.assert_not_called()
        assert result.registered == 0
        assert result.skipped == 0

    def test_mfp_client_not_constructed_when_asken_empty(self):
        """欠食日には MyFitnessPalClient を構築しない（MFP 認証 GET を発生させない）."""
        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(date=_DATE, meals=[])
        mfp_class_mock = MagicMock()

        with (
            patch(
                "asken_myfitnesspal_sync.sync.AskenClient",
                return_value=asken_mock,
            ),
            patch(
                "asken_myfitnesspal_sync.sync.MyFitnessPalClient",
                mfp_class_mock,
            ),
        ):
            sync_meals(_DATE, _make_credentials())

        mfp_class_mock.assert_not_called()

    def test_mfp_client_constructed_with_target_date(self):
        """MyFitnessPalClient は (cookie, target_date) で構築される."""
        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(
            date=_DATE, meals=[_BREAKFAST]
        )
        mfp_mock = self._make_mfp_mock()
        mfp_class_mock = MagicMock(return_value=mfp_mock)

        with (
            patch(
                "asken_myfitnesspal_sync.sync.AskenClient",
                return_value=asken_mock,
            ),
            patch(
                "asken_myfitnesspal_sync.sync.MyFitnessPalClient",
                mfp_class_mock,
            ),
        ):
            sync_meals(_DATE, _make_credentials(mfp_session_cookie="cookieA"))

        mfp_class_mock.assert_called_once_with("cookieA", _DATE)

    def test_no_error_when_asken_has_no_data_and_mfp_would_error(self):
        """欠食時は MFP API を叩かないため MfpError が発生しても error_count に影響しない."""
        from asken_myfitnesspal_sync.myfitnesspal_client import MfpError

        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(date=_DATE, meals=[])
        mfp_mock = self._make_mfp_mock()
        mfp_mock.get_meal_entries.side_effect = MfpError("should not be called")
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp:
            result = sync_meals(_DATE, _make_credentials())

        mfp_mock.get_meal_entries.assert_not_called()
        assert result.error_count == 0

    def test_mfp_error_recorded_as_warning_and_continues(self):
        from asken_myfitnesspal_sync.myfitnesspal_client import MfpError

        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(
            date=_DATE, meals=[_BREAKFAST, _LUNCH]
        )
        mfp_mock = self._make_mfp_mock()

        def _get_entries(d, mt):
            if mt == MealType.BREAKFAST:
                raise MfpError("API error")
            return []

        mfp_mock.get_meal_entries.side_effect = _get_entries
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp:
            result = sync_meals(_DATE, _make_credentials())

        assert result.error_count == 1
        assert result.registered == 1  # Lunch was registered
        assert "Breakfast" in result.errors[0]

    def test_mfp_auth_error_propagates(self):
        from asken_myfitnesspal_sync.myfitnesspal_client import MfpAuthError

        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(
            date=_DATE, meals=[_BREAKFAST]
        )
        mfp_mock = self._make_mfp_mock()
        mfp_mock.get_meal_entries.side_effect = MfpAuthError("auth error")
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp, pytest.raises(MfpAuthError):
            sync_meals(_DATE, _make_credentials())

    def test_asken_auth_error_propagates(self):
        from asken_myfitnesspal_sync.asken_client import AskenAuthError

        asken_mock = MagicMock()
        asken_mock.get_daily_meals.side_effect = AskenAuthError("auth error")
        mfp_mock = self._make_mfp_mock()
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp, pytest.raises(AskenAuthError):
            sync_meals(_DATE, _make_credentials())

    def test_asken_error_propagates(self):
        """HTML 構造変更等による AskenError（非認証）が sync_meals から伝播する."""
        from asken_myfitnesspal_sync.asken_client import AskenError

        asken_mock = MagicMock()
        asken_mock.get_daily_meals.side_effect = AskenError("parse error")
        mfp_mock = self._make_mfp_mock()
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp, pytest.raises(AskenError):
            sync_meals(_DATE, _make_credentials())

    def test_multiple_meal_types_processed(self):
        asken_mock = MagicMock()
        asken_mock.get_daily_meals.return_value = DailyMeals(
            date=_DATE, meals=[_BREAKFAST, _LUNCH, _DINNER, _SNACK]
        )
        mfp_mock = self._make_mfp_mock()
        p_asken, p_mfp = self._patch_clients(asken_mock, mfp_mock)

        with p_asken, p_mfp:
            result = sync_meals(_DATE, _make_credentials())

        assert result.registered == 4
        assert result.skipped == 0


class TestRunSync:
    def test_success_returns_summary(self):
        creds = _make_credentials()
        sync_result = MealSyncResult(registered=2, skipped=1, errors=[])

        with (
            patch(
                "asken_myfitnesspal_sync.sync.get_credentials",
                return_value=creds,
            ),
            patch(
                "asken_myfitnesspal_sync.sync.get_target_date",
                return_value=_DATE,
            ),
            patch(
                "asken_myfitnesspal_sync.sync.sync_meals",
                return_value=sync_result,
            ) as mock_sync,
        ):
            summary = run_sync()

        mock_sync.assert_called_once_with(_DATE, creds)
        assert summary["date"] == "2024-03-15"
        assert summary["registered"] == 2
        assert summary["skipped"] == 1
        assert summary["errors"] == 0

    def test_explicit_target_date_used(self):
        creds = _make_credentials()
        explicit_date = date(2024, 1, 10)

        with (
            patch(
                "asken_myfitnesspal_sync.sync.get_credentials",
                return_value=creds,
            ),
            patch(
                "asken_myfitnesspal_sync.sync.sync_meals",
                return_value=MealSyncResult(),
            ) as mock_sync,
        ):
            run_sync(target_date=explicit_date)

        mock_sync.assert_called_once_with(explicit_date, creds)

    def test_auth_error_propagates(self):
        from asken_myfitnesspal_sync.myfitnesspal_client import MfpAuthError

        creds = _make_credentials()

        with (
            patch(
                "asken_myfitnesspal_sync.sync.get_credentials",
                return_value=creds,
            ),
            patch(
                "asken_myfitnesspal_sync.sync.get_target_date",
                return_value=_DATE,
            ),
            patch(
                "asken_myfitnesspal_sync.sync.sync_meals",
                side_effect=MfpAuthError("auth failed"),
            ),
            pytest.raises(MfpAuthError),
        ):
            run_sync()
