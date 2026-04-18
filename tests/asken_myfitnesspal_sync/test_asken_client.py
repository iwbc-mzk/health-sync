"""あすけんクライアントのユニットテスト."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
import responses as responses_lib

from asken_myfitnesspal_sync.asken_client import (
    _ADVICE_DAILY_URL,
    _ADVICE_URL,
    AskenAuthError,
    AskenClient,
    AskenError,
)
from asken_myfitnesspal_sync.models import MealType
from utils.asken_base_client import _LOGIN_URL

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SHARED_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

TARGET_DATE = date(2026, 4, 18)
BREAKFAST_URL = _ADVICE_URL.format(date=TARGET_DATE.isoformat(), meal_id=1)
LUNCH_URL = _ADVICE_URL.format(date=TARGET_DATE.isoformat(), meal_id=2)
DINNER_URL = _ADVICE_URL.format(date=TARGET_DATE.isoformat(), meal_id=3)
DAILY_URL = _ADVICE_DAILY_URL.format(date=TARGET_DATE.isoformat())


def _load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _load_shared(name: str) -> str:
    return (SHARED_FIXTURES_DIR / name).read_text(encoding="utf-8")


def _add_login_mocks() -> None:
    """ログイン成功用の responses モックを登録する."""
    responses_lib.add(
        responses_lib.GET,
        _LOGIN_URL,
        body=_load_shared("login_page.html"),
        status=200,
    )
    responses_lib.add(
        responses_lib.POST,
        _LOGIN_URL,
        body=_load_shared("login_success.html"),
        status=200,
    )


class TestLogin:
    @responses_lib.activate
    def test_login_success(self) -> None:
        _add_login_mocks()
        client = AskenClient("user@example.com", "password")
        assert client._session is not None

    @responses_lib.activate
    def test_login_wrong_credentials(self) -> None:
        """ログイン失敗（ログアウトリンクなし）は AskenAuthError."""
        responses_lib.add(
            responses_lib.GET,
            _LOGIN_URL,
            body=_load_shared("login_page.html"),
            status=200,
        )
        responses_lib.add(
            responses_lib.POST,
            _LOGIN_URL,
            body="<html><body>ログインに失敗しました</body></html>",
            status=200,
        )
        with pytest.raises(AskenAuthError, match="ログインに失敗しました"):
            AskenClient("bad@example.com", "wrong")

    @responses_lib.activate
    def test_login_no_form(self) -> None:
        """ログインフォームが見つからない場合は AskenAuthError."""
        responses_lib.add(
            responses_lib.GET,
            _LOGIN_URL,
            body="<html><body>Maintenance</body></html>",
            status=200,
        )
        with pytest.raises(AskenAuthError, match="ログインフォームが見つかりません"):
            AskenClient("user@example.com", "password")

    @responses_lib.activate
    def test_login_no_csrf_token(self) -> None:
        """CSRF トークンが空の場合は AskenAuthError."""
        html = (
            "<html><body>"
            '<form id="indexForm">'
            '<input type="hidden" name="data[_Token][key]" value=""/>'
            "</form>"
            "</body></html>"
        )
        responses_lib.add(
            responses_lib.GET,
            _LOGIN_URL,
            body=html,
            status=200,
        )
        with pytest.raises(AskenAuthError, match="CSRF トークンが空です"):
            AskenClient("user@example.com", "password")

    @responses_lib.activate
    def test_login_http_403(self) -> None:
        """HTTP 403 は AskenAuthError."""
        responses_lib.add(
            responses_lib.GET,
            _LOGIN_URL,
            status=403,
        )
        with pytest.raises(AskenAuthError, match="HTTP 403"):
            AskenClient("user@example.com", "password")

    @responses_lib.activate
    @patch("utils.asken_base_client.time.sleep")
    def test_login_network_error_retries(self, _mock_sleep: MagicMock) -> None:
        """ネットワークエラーは AskenError（リトライ後）."""
        for _ in range(3):
            responses_lib.add(
                responses_lib.GET,
                _LOGIN_URL,
                body=requests.exceptions.ConnectionError("connection refused"),
            )
        with pytest.raises(AskenError, match="3 回失敗しました"):
            AskenClient("user@example.com", "password")


class TestGetDailyMeals:
    @responses_lib.activate
    def test_all_meals_present(self) -> None:
        """朝食・昼食・夕食・間食（差分計算）がすべて返される."""
        _add_login_mocks()
        responses_lib.add(responses_lib.GET, BREAKFAST_URL, body=_load("asken_advice_breakfast.html"), status=200)
        responses_lib.add(responses_lib.GET, LUNCH_URL, body=_load("asken_advice_lunch.html"), status=200)
        responses_lib.add(responses_lib.GET, DINNER_URL, body=_load("asken_advice_dinner.html"), status=200)
        responses_lib.add(responses_lib.GET, DAILY_URL, body=_load("asken_advice_daily.html"), status=200)

        client = AskenClient("user@example.com", "password")
        daily = client.get_daily_meals(TARGET_DATE)

        assert daily.date == TARGET_DATE
        assert len(daily.meals) == 4

        meal_map = {m.meal_type: m for m in daily.meals}

        breakfast = meal_map[MealType.BREAKFAST]
        assert breakfast.calories == 500.0
        assert breakfast.protein_g == 20.5
        assert breakfast.fat_g == 15.0
        assert breakfast.carbs_g == 70.0

        lunch = meal_map[MealType.LUNCH]
        assert lunch.calories == 700.0
        assert lunch.protein_g == 30.0

        dinner = meal_map[MealType.DINNER]
        assert dinner.calories == 650.0

        # 間食 = 1日合計 - 朝食 - 昼食 - 夕食
        snacks = meal_map[MealType.SNACKS]
        assert snacks.calories == pytest.approx(250.0)
        assert snacks.protein_g == pytest.approx(5.0)
        assert snacks.fat_g == pytest.approx(8.0)
        assert snacks.carbs_g == pytest.approx(40.0)

    @responses_lib.activate
    def test_missing_breakfast_skipped(self) -> None:
        """欠食（食事記録なし）の食事区分はスキップされる."""
        _add_login_mocks()
        responses_lib.add(responses_lib.GET, BREAKFAST_URL, body=_load("asken_advice_empty.html"), status=200)
        responses_lib.add(responses_lib.GET, LUNCH_URL, body=_load("asken_advice_lunch.html"), status=200)
        responses_lib.add(responses_lib.GET, DINNER_URL, body=_load("asken_advice_dinner.html"), status=200)
        responses_lib.add(responses_lib.GET, DAILY_URL, body=_load("asken_advice_empty.html"), status=200)

        client = AskenClient("user@example.com", "password")
        daily = client.get_daily_meals(TARGET_DATE)

        assert len(daily.meals) == 2
        meal_types = {m.meal_type for m in daily.meals}
        assert MealType.BREAKFAST not in meal_types
        assert MealType.SNACKS not in meal_types
        assert MealType.LUNCH in meal_types
        assert MealType.DINNER in meal_types

    @responses_lib.activate
    def test_no_snack_when_daily_empty(self) -> None:
        """1日合計が欠食の場合は間食もスキップされる."""
        _add_login_mocks()
        responses_lib.add(responses_lib.GET, BREAKFAST_URL, body=_load("asken_advice_breakfast.html"), status=200)
        responses_lib.add(responses_lib.GET, LUNCH_URL, body=_load("asken_advice_lunch.html"), status=200)
        responses_lib.add(responses_lib.GET, DINNER_URL, body=_load("asken_advice_dinner.html"), status=200)
        responses_lib.add(responses_lib.GET, DAILY_URL, body=_load("asken_advice_empty.html"), status=200)

        client = AskenClient("user@example.com", "password")
        daily = client.get_daily_meals(TARGET_DATE)

        meal_types = {m.meal_type for m in daily.meals}
        assert MealType.SNACKS not in meal_types
        assert len(daily.meals) == 3

    @responses_lib.activate
    def test_no_snack_when_meals_equal_daily(self) -> None:
        """間食なし（朝昼夕の合計 = 1日合計）の場合、間食はスキップされる."""
        _add_login_mocks()
        responses_lib.add(responses_lib.GET, BREAKFAST_URL, body=_load("asken_advice_breakfast.html"), status=200)
        responses_lib.add(responses_lib.GET, LUNCH_URL, body=_load("asken_advice_lunch.html"), status=200)
        responses_lib.add(responses_lib.GET, DINNER_URL, body=_load("asken_advice_dinner.html"), status=200)
        no_snack_daily = (
            '<html><body><a href="/login/logout">ログアウト</a>'
            '<ul class="nutrient_list">'
            '<li class="line_left"><ul><li class="title">エネルギー</li><li class="val">1850kcal</li></ul></li>'
            '<li class="line_left"><ul><li class="title">たんぱく質</li><li class="val">85.5g</li></ul></li>'
            '<li class="line_left"><ul><li class="title">脂質</li><li class="val">53.0g</li></ul></li>'
            '<li class="line_left"><ul><li class="title">炭水化物</li><li class="val">240.0g</li></ul></li>'
            "</ul></body></html>"
        )
        responses_lib.add(responses_lib.GET, DAILY_URL, body=no_snack_daily, status=200)

        client = AskenClient("user@example.com", "password")
        daily = client.get_daily_meals(TARGET_DATE)

        meal_types = {m.meal_type for m in daily.meals}
        assert MealType.SNACKS not in meal_types
        assert len(daily.meals) == 3

    @responses_lib.activate
    def test_parse_failure_raises_asken_error(self) -> None:
        """HTML構造変更（li.line_left なし）は AskenError を送出する."""
        _add_login_mocks()
        invalid_html = "<html><body><a href='/login/logout'>ログアウト</a><p>構造変更後のページ</p></body></html>"
        responses_lib.add(responses_lib.GET, BREAKFAST_URL, body=invalid_html, status=200)

        client = AskenClient("user@example.com", "password")
        with pytest.raises(AskenError, match="HTML構造が変更された可能性"):
            client.get_daily_meals(TARGET_DATE)

    @responses_lib.activate
    def test_partial_parse_failure_raises_asken_error(self) -> None:
        """エネルギーのみ取得できPFCが欠損している場合は AskenError を送出する."""
        _add_login_mocks()
        partial_html = (
            '<html><body><a href="/login/logout">ログアウト</a>'
            '<ul class="nutrient_list">'
            '<li class="line_left"><ul><li class="title">エネルギー</li><li class="val">500kcal</li></ul></li>'
            "</ul></body></html>"
        )
        responses_lib.add(responses_lib.GET, BREAKFAST_URL, body=partial_html, status=200)

        client = AskenClient("user@example.com", "password")
        with pytest.raises(AskenError, match="HTML構造が変更された可能性"):
            client.get_daily_meals(TARGET_DATE)

    @responses_lib.activate
    def test_daily_parse_failure_raises_asken_error(self) -> None:
        """1日合計ページのHTML構造変更は AskenError を送出する."""
        _add_login_mocks()
        responses_lib.add(responses_lib.GET, BREAKFAST_URL, body=_load("asken_advice_breakfast.html"), status=200)
        responses_lib.add(responses_lib.GET, LUNCH_URL, body=_load("asken_advice_lunch.html"), status=200)
        responses_lib.add(responses_lib.GET, DINNER_URL, body=_load("asken_advice_dinner.html"), status=200)
        invalid_html = "<html><body><a href='/login/logout'>ログアウト</a><p>構造変更後のページ</p></body></html>"
        responses_lib.add(responses_lib.GET, DAILY_URL, body=invalid_html, status=200)

        client = AskenClient("user@example.com", "password")
        with pytest.raises(AskenError, match="HTML構造が変更された可能性"):
            client.get_daily_meals(TARGET_DATE)

    @responses_lib.activate
    def test_session_expired_raises_auth_error(self) -> None:
        """食事ページ取得時にセッション切れ（ログインページへリダイレクト）は AskenAuthError."""
        _add_login_mocks()
        client = AskenClient("user@example.com", "password")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = _LOGIN_URL
        mock_resp.raise_for_status.return_value = None

        with patch.object(client._session, "get", return_value=mock_resp):
            with pytest.raises(AskenAuthError, match="セッションが切れています"):
                client.get_daily_meals(TARGET_DATE)

    @responses_lib.activate
    @patch("utils.asken_base_client.time.sleep")
    def test_http_error_raises_asken_error(self, _mock_sleep: MagicMock) -> None:
        """HTTP エラーは AskenError（リトライ後）."""
        _add_login_mocks()
        for _ in range(3):
            responses_lib.add(responses_lib.GET, BREAKFAST_URL, status=500)

        client = AskenClient("user@example.com", "password")
        with pytest.raises(AskenError):
            client.get_daily_meals(TARGET_DATE)
