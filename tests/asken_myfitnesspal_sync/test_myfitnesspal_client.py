"""MyFitnessPal クライアントのユニットテスト."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
import requests
import requests as _requests_module
import responses as responses_lib

from asken_myfitnesspal_sync.models import MealNutrition, MealType
from asken_myfitnesspal_sync.myfitnesspal_client import (
    _API_URL,
    _BASE_URL,
    MfpAuthError,
    MfpError,
    MyFitnessPalClient,
    _mfp_request_with_retry,
)

_AUTH_TOKEN_URL = f"{_BASE_URL}/user/auth_token"
_DIARY_URL = f"{_API_URL}/v2/diary"
_FOODS_URL = f"{_API_URL}/v2/foods"

TARGET_DATE = date(2026, 4, 19)

_TEST_SESSION_COOKIE = "test_session_token_value"
_AUTH_TOKEN_JSON = json.dumps({"access_token": "test_bearer_token", "user_id": 99999})


def _add_auth_mocks() -> None:
    """認証成功用の responses モックを登録する（クッキーベース）."""
    responses_lib.add(
        responses_lib.GET,
        _AUTH_TOKEN_URL,
        body=_AUTH_TOKEN_JSON,
        status=200,
        content_type="application/json",
    )


def _build_diary_item(
    entry_id: str,
    meal_position: int,
    calories: float,
    protein: float,
    fat: float,
    carbs: float,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "type": "food_entry",
        "meal_position": meal_position,
        "nutritional_contents": {
            "energy": {"unit": "calories", "value": calories},
            "protein": protein,
            "fat": fat,
            "carbohydrates": carbs,
        },
    }


class TestAuthenticate:
    @responses_lib.activate
    def test_authenticate_success(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)
        assert client._access_token == "test_bearer_token"
        assert client._user_id == "99999"

    @responses_lib.activate
    def test_auth_token_connection_error_retries(self, mock_mfp_sleep) -> None:
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            body=requests.exceptions.ConnectionError("Connection error"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE)
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_auth_token_empty_response_raises_auth_error(self) -> None:
        """空レスポンスはセッションクッキーが無効または期限切れ."""
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            body="",
            status=200,
        )
        with pytest.raises(MfpAuthError, match="レスポンスが空です"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE)

    @responses_lib.activate
    def test_auth_token_non_json_response_raises_auth_error(self) -> None:
        """非 JSON レスポンスはクッキー期限切れ等を示す."""
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            body="<html>Login required</html>",
            status=200,
        )
        with pytest.raises(MfpAuthError, match="認証トークンの解析に失敗しました"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE)

    @responses_lib.activate
    def test_session_cookie_sent_in_request(self) -> None:
        """セッションクッキーが auth_token リクエストに含まれること."""
        _add_auth_mocks()
        MyFitnessPalClient(_TEST_SESSION_COOKIE)
        sent_cookie = responses_lib.calls[0].request.headers.get("Cookie", "")
        assert _TEST_SESSION_COOKIE in sent_cookie

    @responses_lib.activate
    def test_auth_token_missing_key_raises_auth_error(self) -> None:
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"something_else": "value"},
            status=200,
        )
        with pytest.raises(MfpAuthError, match="access_token"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE)

    @responses_lib.activate
    def test_auth_token_null_access_token_raises_auth_error(self) -> None:
        """access_token が null の場合は MfpAuthError."""
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"access_token": None, "user_id": 99999},
            status=200,
        )
        with pytest.raises(MfpAuthError, match="access_token"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE)

    @responses_lib.activate
    def test_auth_token_null_user_id_raises_auth_error(self) -> None:
        """user_id が null の場合は MfpAuthError."""
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"access_token": "tok", "user_id": None},
            status=200,
        )
        with pytest.raises(MfpAuthError, match="user_id"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE)

    @responses_lib.activate
    def test_auth_token_user_id_zero_is_valid(self) -> None:
        """user_id=0 は有効な値であり MfpAuthError を送出しないこと."""
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"access_token": "tok", "user_id": 0},
            status=200,
        )
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)
        assert client._user_id == "0"

    @responses_lib.activate
    def test_auth_token_401_raises_auth_error(self) -> None:
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"access_token": "fake", "user_id": 99},
            status=401,
        )
        with pytest.raises(MfpAuthError):
            MyFitnessPalClient(_TEST_SESSION_COOKIE)

    @responses_lib.activate
    def test_auth_token_429_retries_then_succeeds(self, mock_mfp_sleep) -> None:
        """auth_token GET で 429 を受けた場合、リトライして成功すること."""
        responses_lib.add(responses_lib.GET, _AUTH_TOKEN_URL, status=429)
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            body=_AUTH_TOKEN_JSON,
            status=200,
            content_type="application/json",
        )
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)
        assert client._access_token == "test_bearer_token"
        mock_mfp_sleep.assert_called_once()


class TestGetMealEntries:
    @responses_lib.activate
    def test_get_meal_entries_returns_matching_entries(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        items = [
            _build_diary_item("e1", meal_position=0, calories=400, protein=20, fat=10, carbs=50),
            _build_diary_item("e2", meal_position=1, calories=600, protein=30, fat=15, carbs=70),
            _build_diary_item("e3", meal_position=0, calories=100, protein=5, fat=2, carbs=15),
        ]
        responses_lib.add(
            responses_lib.GET, _DIARY_URL, json={"items": items}, status=200
        )

        result = client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        assert len(result) == 2
        assert result[0].calories == 400.0
        assert result[0].meal_type is MealType.BREAKFAST
        assert result[1].calories == 100.0

    @responses_lib.activate
    def test_get_meal_entries_returns_empty_for_no_match(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.GET,
            _DIARY_URL,
            json={"items": [_build_diary_item("e1", 1, 500, 25, 12, 60)]},
            status=200,
        )
        result = client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        assert result == []

    @responses_lib.activate
    def test_get_meal_entries_401_raises_auth_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(responses_lib.GET, _DIARY_URL, status=401)
        with pytest.raises(MfpAuthError):
            client.get_meal_entries(TARGET_DATE, MealType.LUNCH)

    @responses_lib.activate
    def test_get_meal_entries_5xx_raises_mfp_error(self, mock_mfp_sleep) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(responses_lib.GET, _DIARY_URL, status=500)
        with pytest.raises(MfpError, match="HTTP 500"):
            client.get_meal_entries(TARGET_DATE, MealType.DINNER)
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_get_meal_entries_non_json_raises_mfp_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.GET,
            _DIARY_URL,
            body="<html>Maintenance</html>",
            status=200,
        )
        with pytest.raises(MfpError, match="JSON パース"):
            client.get_meal_entries(TARGET_DATE, MealType.SNACKS)

    @responses_lib.activate
    def test_get_meal_entries_request_error_raises_mfp_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.GET,
            _DIARY_URL,
            body=requests.exceptions.Timeout("Timeout"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)

    @responses_lib.activate
    def test_get_meal_entries_items_null_returns_empty_list(self) -> None:
        """{"items": null} レスポンスで空リストを返すこと."""
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(responses_lib.GET, _DIARY_URL, json={"items": None}, status=200)
        result = client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        assert result == []

    @responses_lib.activate
    def test_get_meal_entries_maps_nutritional_contents(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        item = _build_diary_item("e1", meal_position=3, calories=150, protein=8, fat=4, carbs=20)
        responses_lib.add(responses_lib.GET, _DIARY_URL, json={"items": [item]}, status=200)

        result = client.get_meal_entries(TARGET_DATE, MealType.SNACKS)
        assert len(result) == 1
        assert result[0].protein_g == 8.0
        assert result[0].fat_g == 4.0
        assert result[0].carbs_g == 20.0


class TestAddMealEntry:
    def _nutrition(self, meal_type: MealType = MealType.BREAKFAST) -> MealNutrition:
        return MealNutrition(
            meal_type=meal_type, calories=500.0, protein_g=25.0, fat_g=12.0, carbs_g=60.0
        )

    @responses_lib.activate
    def test_add_meal_entry_success(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            json={"item": {"id": "food123", "version": "v1"}},
            status=201,
        )
        responses_lib.add(responses_lib.POST, _DIARY_URL, json={}, status=201)

        client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_diary_200_also_succeeds(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            json={"item": {"id": "food456", "version": ""}},
            status=200,
        )
        responses_lib.add(responses_lib.POST, _DIARY_URL, json={}, status=200)

        client.add_meal_entry(TARGET_DATE, self._nutrition(MealType.LUNCH))

    @responses_lib.activate
    def test_add_meal_entry_food_creation_401_raises_auth_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(responses_lib.POST, _FOODS_URL, status=401)
        with pytest.raises(MfpAuthError):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_food_creation_non_json_raises_mfp_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            body="<html>Error</html>",
            status=200,
        )
        with pytest.raises(MfpError, match="JSON パース"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_food_item_null_raises_mfp_error(self) -> None:
        """{"item": null} レスポンス時に MfpError を送出すること."""
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            json={"item": None},
            status=201,
        )
        with pytest.raises(MfpError, match="id が含まれていません"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_food_creation_missing_id_raises_mfp_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            json={"item": {}},
            status=201,
        )
        with pytest.raises(MfpError, match="id が含まれていません"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_food_creation_5xx_raises_mfp_error(self, mock_mfp_sleep) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(responses_lib.POST, _FOODS_URL, status=500, body="Internal Error")
        with pytest.raises(MfpError, match="HTTP 500"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_add_meal_entry_diary_401_raises_auth_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            json={"item": {"id": "food789", "version": "v2"}},
            status=201,
        )
        responses_lib.add(responses_lib.POST, _DIARY_URL, status=401)

        with pytest.raises(MfpAuthError):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_diary_5xx_raises_mfp_error(self, mock_mfp_sleep) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            json={"item": {"id": "foodabc", "version": ""}},
            status=201,
        )
        responses_lib.add(responses_lib.POST, _DIARY_URL, status=503, body="Service Unavailable")

        with pytest.raises(MfpError, match="HTTP 503"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_add_meal_entry_food_creation_network_error_raises_mfp_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            body=requests.exceptions.ConnectionError("Network down"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_diary_network_error_raises_mfp_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            json={"item": {"id": "foodNet", "version": ""}},
            status=201,
        )
        responses_lib.add(
            responses_lib.POST,
            _DIARY_URL,
            body=requests.exceptions.Timeout("Timeout"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_maps_meal_position_correctly(self) -> None:
        """食事区分が正しい meal_position にマッピングされることを確認."""
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.POST,
            _FOODS_URL,
            json={"item": {"id": "foodXYZ", "version": ""}},
            status=201,
        )
        posted_body: list[dict[str, Any]] = []

        def _capture(request):  # type: ignore[no-untyped-def]
            posted_body.append(json.loads(request.body))
            return 201, {}, "{}"

        responses_lib.add_callback(responses_lib.POST, _DIARY_URL, callback=_capture)

        client.add_meal_entry(
            TARGET_DATE,
            MealNutrition(meal_type=MealType.DINNER, calories=700, protein_g=35, fat_g=20, carbs_g=80),
        )
        assert posted_body[0]["items"][0]["meal_position"] == 2  # DINNER = 2


class TestDeleteMealEntries:
    @responses_lib.activate
    def test_delete_meal_entries_success(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        items = [
            _build_diary_item("e1", meal_position=0, calories=400, protein=20, fat=10, carbs=50),
            _build_diary_item("e2", meal_position=0, calories=100, protein=5, fat=2, carbs=15),
            _build_diary_item("e3", meal_position=1, calories=600, protein=30, fat=15, carbs=70),
        ]
        responses_lib.add(responses_lib.GET, _DIARY_URL, json={"items": items}, status=200)
        responses_lib.add(
            responses_lib.DELETE, f"{_DIARY_URL}/e1", status=204
        )
        responses_lib.add(
            responses_lib.DELETE, f"{_DIARY_URL}/e2", status=204
        )

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)

    @responses_lib.activate
    def test_delete_meal_entries_no_matching_entries_does_nothing(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.GET,
            _DIARY_URL,
            json={"items": [_build_diary_item("e1", 1, 500, 25, 12, 60)]},
            status=200,
        )

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)

    @responses_lib.activate
    def test_delete_meal_entries_401_on_get_raises_auth_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(responses_lib.GET, _DIARY_URL, status=401)
        with pytest.raises(MfpAuthError):
            client.delete_meal_entries(TARGET_DATE, MealType.LUNCH)

    @responses_lib.activate
    def test_delete_meal_entries_401_on_delete_raises_auth_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.GET,
            _DIARY_URL,
            json={"items": [_build_diary_item("e99", 2, 800, 40, 25, 90)]},
            status=200,
        )
        responses_lib.add(responses_lib.DELETE, f"{_DIARY_URL}/e99", status=401)

        with pytest.raises(MfpAuthError):
            client.delete_meal_entries(TARGET_DATE, MealType.DINNER)

    @responses_lib.activate
    def test_delete_meal_entries_5xx_raises_mfp_error(self, mock_mfp_sleep) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.GET,
            _DIARY_URL,
            json={"items": [_build_diary_item("e77", 3, 200, 10, 5, 25)]},
            status=200,
        )
        responses_lib.add(responses_lib.DELETE, f"{_DIARY_URL}/e77", status=500)

        with pytest.raises(MfpError, match="HTTP 500"):
            client.delete_meal_entries(TARGET_DATE, MealType.SNACKS)
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_delete_meal_entries_skips_items_without_id(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        items = [
            {"type": "food_entry", "meal_position": 0, "nutritional_contents": {}},
            _build_diary_item("e10", 0, 300, 15, 8, 35),
        ]
        responses_lib.add(responses_lib.GET, _DIARY_URL, json={"items": items}, status=200)
        responses_lib.add(responses_lib.DELETE, f"{_DIARY_URL}/e10", status=200)

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)

    @responses_lib.activate
    def test_delete_meal_entries_delete_network_error_raises_mfp_error(self) -> None:
        _add_auth_mocks()
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE)

        responses_lib.add(
            responses_lib.GET,
            _DIARY_URL,
            json={"items": [_build_diary_item("e55", 1, 500, 25, 12, 60)]},
            status=200,
        )
        responses_lib.add(
            responses_lib.DELETE,
            f"{_DIARY_URL}/e55",
            body=requests.exceptions.ConnectionError("Network down"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            client.delete_meal_entries(TARGET_DATE, MealType.LUNCH)


_FAKE_URL = f"{_API_URL}/v2/test_retry"


class TestMfpRequestWithRetry:
    """_mfp_request_with_retry のリトライ戦略を直接テストする."""

    @responses_lib.activate
    def test_429_retries_up_to_3_times_then_raises(self, mock_mfp_sleep) -> None:
        """429 が4回連続で返されたとき MfpError を送出し、3回スリープすること."""
        for _ in range(4):
            responses_lib.add(responses_lib.GET, _FAKE_URL, status=429)

        with pytest.raises(MfpError, match="MFP リクエストが 4 回失敗しました"):
            _mfp_request_with_retry(_requests_module.get, _FAKE_URL, timeout=5)

        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_5xx_retries_with_exponential_backoff(self, mock_mfp_sleep) -> None:
        """5xx が続いた後に成功する場合、バックオフ遅延でリトライしてレスポンスを返すこと."""
        responses_lib.add(responses_lib.GET, _FAKE_URL, status=503)
        responses_lib.add(responses_lib.GET, _FAKE_URL, status=503)
        responses_lib.add(responses_lib.GET, _FAKE_URL, status=200)

        resp = _mfp_request_with_retry(_requests_module.get, _FAKE_URL, timeout=5)
        assert resp.status_code == 200
        assert mock_mfp_sleep.call_count == 2
        # 指数バックオフ: 1.0, 2.0
        mock_mfp_sleep.assert_any_call(1.0)
        mock_mfp_sleep.assert_any_call(2.0)

    @responses_lib.activate
    def test_retry_after_header_respected(self, mock_mfp_sleep) -> None:
        """429 に Retry-After ヘッダーが付いている場合、その値でスリープすること."""
        responses_lib.add(
            responses_lib.GET,
            _FAKE_URL,
            status=429,
            headers={"Retry-After": "5"},
        )
        responses_lib.add(responses_lib.GET, _FAKE_URL, status=200)

        resp = _mfp_request_with_retry(_requests_module.get, _FAKE_URL, timeout=5)
        assert resp.status_code == 200
        mock_mfp_sleep.assert_called_once_with(5.0)

    @responses_lib.activate
    def test_retry_after_zero_falls_back_to_exponential_backoff(self, mock_mfp_sleep) -> None:
        """Retry-After: 0 は無効値として指数バックオフにフォールバックすること."""
        responses_lib.add(
            responses_lib.GET,
            _FAKE_URL,
            status=429,
            headers={"Retry-After": "0"},
        )
        responses_lib.add(responses_lib.GET, _FAKE_URL, status=200)

        _mfp_request_with_retry(_requests_module.get, _FAKE_URL, timeout=5)
        mock_mfp_sleep.assert_called_once_with(1.0)  # 指数バックオフの1回目

    @responses_lib.activate
    def test_401_does_not_retry_raises_immediately(self, mock_mfp_sleep) -> None:
        """401 は一切リトライせず即座に MfpAuthError を送出すること."""
        responses_lib.add(responses_lib.GET, _FAKE_URL, status=401)

        with pytest.raises(MfpAuthError):
            _mfp_request_with_retry(_requests_module.get, _FAKE_URL, timeout=5)

        mock_mfp_sleep.assert_not_called()

    @responses_lib.activate
    def test_connection_error_retries_then_raises(self, mock_mfp_sleep) -> None:
        """接続エラーが4回続いたとき MfpError を送出し、3回スリープすること."""
        for _ in range(4):
            responses_lib.add(
                responses_lib.GET,
                _FAKE_URL,
                body=_requests_module.exceptions.ConnectionError("Network down"),
            )

        with pytest.raises(MfpError, match="MFP リクエストが 4 回失敗しました"):
            _mfp_request_with_retry(_requests_module.get, _FAKE_URL, timeout=5)

        assert mock_mfp_sleep.call_count == 3
