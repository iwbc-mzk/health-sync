"""MyFitnessPal クライアントのユニットテスト."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import requests
import requests as _requests_module
import responses as responses_lib

from asken_myfitnesspal_sync.models import MealNutrition, MealType
from asken_myfitnesspal_sync.myfitnesspal_client import (
    _API_URL,
    _BASE_URL,
    _DIARY_WEB_URL,
    MfpAuthError,
    MfpError,
    MyFitnessPalClient,
    _mfp_request_with_retry,
)

_AUTH_TOKEN_URL = f"{_BASE_URL}/user/auth_token"
_QUICK_ADD_URL = f"{_BASE_URL}/api/services/diary"
_FIXTURES_DIR = Path(__file__).parent / "fixtures"

TARGET_DATE = date(2026, 4, 19)
_DIARY_PAGE_URL = f"{_DIARY_WEB_URL}?date={TARGET_DATE.isoformat()}"

_TEST_SESSION_COOKIE = "test_session_token_value"
_AUTH_TOKEN_JSON = json.dumps({"access_token": "test_bearer_token", "user_id": 99999})


def _build_diary_page_html(
    sections: dict[str, list[dict[str, Any]]] | None = None,
    csrf_token: str = "test_csrf_token",
) -> str:
    """テスト用の MFP 日記ページ HTML を生成する."""
    if sections is None:
        sections = {}

    tables = []
    for meal_name, entries in sections.items():
        rows = ""
        for e in entries:
            rows += f"""
    <tr>
      <td class="delete">
        <a rel="nofollow" data-method="delete" href="/ja/food/remove/{e['id']}">
          <i class="icon-minus-sign"></i>
        </a>
      </td>
      <td class="main-title-2 first">Quick Add</td>
      <td class="calories">{e['calories']}</td>
      <td class="protein">{e['protein']}</td>
      <td class="fat">{e['fat']}</td>
      <td class="carbohydrates">{e['carbs']}</td>
    </tr>"""
        tables.append(f"""
<table class="main-title-2">
  <thead>
    <tr>
      <td class="first alt" colspan="2">{meal_name}</td>
    </tr>
  </thead>
  <tbody>{rows}
  </tbody>
</table>""")

    return f"""<!DOCTYPE html>
<html><head>
  <meta name="csrf-token" content="{csrf_token}">
</head><body>
{''.join(tables)}
</body></html>"""


def _add_diary_page_mock(
    html: str,
    status: int = 200,
) -> None:
    """日記ページ GET モックを登録する."""
    responses_lib.add(
        responses_lib.GET,
        _DIARY_WEB_URL,
        body=html.encode("utf-8"),
        status=status,
        content_type="text/html; charset=utf-8",
    )


def _add_auth_token_mock() -> None:
    """auth_token GET の成功モックを登録する."""
    responses_lib.add(
        responses_lib.GET,
        _AUTH_TOKEN_URL,
        body=_AUTH_TOKEN_JSON,
        status=200,
        content_type="application/json",
    )


def _add_init_mocks(diary_html: str | None = None) -> None:
    """構築時に必要なモック (diary 訪問 → auth_token GET) を登録する.

    認証フローは「diary GET → auth_token GET」の順なので、モックも同順で登録する。
    """
    if diary_html is None:
        diary_html = _build_diary_page_html()
    _add_diary_page_mock(diary_html)
    _add_auth_token_mock()


def _make_client(diary_html: str | None = None) -> MyFitnessPalClient:
    """テスト用に認証成功状態の MyFitnessPalClient を構築する."""
    _add_init_mocks(diary_html)
    return MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)


class TestAuthenticate:
    @responses_lib.activate
    def test_authenticate_success(self) -> None:
        client = _make_client()
        assert client._access_token == "test_bearer_token"
        assert client._user_id == "99999"

    @responses_lib.activate
    def test_authenticate_visits_diary_before_auth_token(self) -> None:
        """ブラウザ的フロー: diary ページを最初に訪問してから auth_token を取得する."""
        _add_init_mocks()
        MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)
        # 最初の呼び出しは diary、次が auth_token
        assert _DIARY_WEB_URL in responses_lib.calls[0].request.url
        assert _AUTH_TOKEN_URL in responses_lib.calls[1].request.url

    @responses_lib.activate
    def test_authenticate_auth_token_no_refresh_query(self) -> None:
        """auth_token 取得時に ?refresh=true を付与しないこと."""
        _add_init_mocks()
        MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)
        auth_call_url = responses_lib.calls[1].request.url
        assert "refresh" not in auth_call_url

    @responses_lib.activate
    def test_diary_get_connection_error_retries(self, mock_mfp_sleep) -> None:
        """diary GET で接続エラー時にリトライすること."""
        responses_lib.add(
            responses_lib.GET,
            _DIARY_WEB_URL,
            body=requests.exceptions.ConnectionError("Connection error"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_diary_get_401_raises_auth_error(self) -> None:
        """diary GET で 401 の場合 MfpAuthError を送出すること."""
        _add_diary_page_mock("", status=401)
        with pytest.raises(MfpAuthError):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_diary_get_403_raises_auth_error(self) -> None:
        """diary GET で 403（クッキー失効 / Cloudflare bot 検出の典型）は MfpAuthError とする."""
        _add_diary_page_mock("Forbidden", status=403)
        with pytest.raises(MfpAuthError, match="403"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_diary_get_other_4xx_raises_mfp_error(self) -> None:
        """diary GET で 401/403 以外の 4xx (404 等) は MfpError を送出する."""
        _add_diary_page_mock("Not Found", status=404)
        with pytest.raises(MfpError, match="日記ページの取得に失敗しました"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_diary_login_redirect_raises_auth_error(self) -> None:
        """diary GET の最終 URL が /login（クッキー失効時のリダイレクト先）なら MfpAuthError."""
        # 302 を返して /login にリダイレクト → requests が追跡し最終 resp.url が /login
        responses_lib.add(
            responses_lib.GET,
            _DIARY_WEB_URL,
            status=302,
            headers={"Location": f"{_BASE_URL}/login"},
        )
        responses_lib.add(
            responses_lib.GET,
            f"{_BASE_URL}/login",
            body="<html><body>Sign in</body></html>",
            status=200,
            content_type="text/html",
        )
        with pytest.raises(MfpAuthError, match="ログイン画面"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_diary_cloudflare_challenge_raises_auth_error(self) -> None:
        """200 で Cloudflare チャレンジ HTML が返ってきたら MfpAuthError を送出する."""
        cf_html = (
            "<!DOCTYPE html><html><head>"
            "<title>Just a moment...</title></head>"
            "<body>Checking your browser before accessing myfitnesspal.com</body></html>"
        )
        _add_diary_page_mock(cf_html)
        with pytest.raises(MfpAuthError, match="Cloudflare"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_auth_token_connection_error_retries(self, mock_mfp_sleep) -> None:
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            body=requests.exceptions.ConnectionError("Connection error"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_auth_token_empty_response_raises_auth_error(self) -> None:
        """空レスポンスはセッションクッキーが無効または期限切れ."""
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            body="",
            status=200,
        )
        with pytest.raises(MfpAuthError, match="レスポンスが空です"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_auth_token_non_json_response_raises_auth_error(self) -> None:
        """非 JSON レスポンスはクッキー期限切れ等を示す."""
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            body="<html>Login required</html>",
            status=200,
        )
        with pytest.raises(MfpAuthError, match="認証トークンの解析に失敗しました"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_session_cookie_sent_in_request(self) -> None:
        """セッションクッキーが diary GET と auth_token GET の両方に含まれること."""
        _make_client()
        diary_cookie = responses_lib.calls[0].request.headers.get("Cookie", "")
        auth_cookie = responses_lib.calls[1].request.headers.get("Cookie", "")
        assert _TEST_SESSION_COOKIE in diary_cookie
        assert _TEST_SESSION_COOKIE in auth_cookie

    @responses_lib.activate
    def test_browser_headers_sent(self) -> None:
        """ブラウザ標準ヘッダー (Sec-Fetch-*, Sec-Ch-Ua-*) が付与されること."""
        _make_client()
        diary_headers = responses_lib.calls[0].request.headers
        assert diary_headers.get("Sec-Fetch-Mode") == "navigate"
        assert diary_headers.get("Sec-Fetch-Dest") == "document"
        assert "Chrome" in diary_headers.get("User-Agent", "")
        assert diary_headers.get("Sec-Ch-Ua-Platform") == '"Windows"'
        assert diary_headers.get("Accept-Language", "").startswith("ja")

    @responses_lib.activate
    def test_auth_token_request_includes_referer(self) -> None:
        """auth_token GET に diary ページの Referer が付与されること."""
        _make_client()
        auth_headers = responses_lib.calls[1].request.headers
        assert TARGET_DATE.isoformat() in auth_headers.get("Referer", "")

    @responses_lib.activate
    def test_auth_token_missing_key_raises_auth_error(self) -> None:
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"something_else": "value"},
            status=200,
        )
        with pytest.raises(MfpAuthError, match="access_token"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_auth_token_null_access_token_raises_auth_error(self) -> None:
        """access_token が null の場合は MfpAuthError."""
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"access_token": None, "user_id": 99999},
            status=200,
        )
        with pytest.raises(MfpAuthError, match="access_token"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_auth_token_null_user_id_raises_auth_error(self) -> None:
        """user_id が null の場合は MfpAuthError."""
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"access_token": "tok", "user_id": None},
            status=200,
        )
        with pytest.raises(MfpAuthError, match="user_id"):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_auth_token_user_id_zero_is_valid(self) -> None:
        """user_id=0 は有効な値であり MfpAuthError を送出しないこと."""
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"access_token": "tok", "user_id": 0},
            status=200,
        )
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)
        assert client._user_id == "0"

    @responses_lib.activate
    def test_auth_token_401_raises_auth_error(self) -> None:
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            json={"access_token": "fake", "user_id": 99},
            status=401,
        )
        with pytest.raises(MfpAuthError):
            MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)

    @responses_lib.activate
    def test_auth_token_429_retries_then_succeeds(self, mock_mfp_sleep) -> None:
        """auth_token GET で 429 を受けた場合、リトライして成功すること."""
        _add_diary_page_mock(_build_diary_page_html())
        responses_lib.add(responses_lib.GET, _AUTH_TOKEN_URL, status=429)
        responses_lib.add(
            responses_lib.GET,
            _AUTH_TOKEN_URL,
            body=_AUTH_TOKEN_JSON,
            status=200,
            content_type="application/json",
        )
        client = MyFitnessPalClient(_TEST_SESSION_COOKIE, TARGET_DATE)
        assert client._access_token == "test_bearer_token"
        mock_mfp_sleep.assert_called_once()


class TestScrapeDiaryPage:
    """_scrape_diary_page のテスト. 認証時取得 HTML のキャッシュ動作も確認."""

    @responses_lib.activate
    def test_scrape_returns_all_sections(self, fixture_html) -> None:
        """全食事セクションのエントリが取得されること."""
        client = _make_client(fixture_html("mfp_diary_page.html"))

        entries, csrf_token = client._scrape_diary_page(TARGET_DATE)

        assert csrf_token == "test_csrf_token_abc123"
        assert len(entries) == 4  # Breakfast×2 + Lunch×1 + Snacks×1
        meal_positions = [e.meal_position for e in entries]
        assert meal_positions.count(0) == 2  # Breakfast
        assert meal_positions.count(1) == 1  # Lunch
        assert meal_positions.count(2) == 0  # Dinner (empty)
        assert meal_positions.count(3) == 1  # Snacks

    @responses_lib.activate
    def test_scrape_parses_nutritional_values(self) -> None:
        """栄養値が正しくパースされること."""
        html = _build_diary_page_html({
            "Breakfast": [
                {"id": "101", "calories": 450, "protein": 22, "fat": 11, "carbs": 55}
            ]
        })
        client = _make_client(html)

        entries, _ = client._scrape_diary_page(TARGET_DATE)

        assert len(entries) == 1
        e = entries[0]
        assert e.meal_position == 0
        assert e.calories == 450.0
        assert e.protein == 22.0
        assert e.fat == 11.0
        assert e.carbs == 55.0
        assert e.remove_path == "/ja/food/remove/101"

    @responses_lib.activate
    def test_scrape_extracts_csrf_token(self) -> None:
        """CSRF トークンが正しく取得されること."""
        html = _build_diary_page_html(csrf_token="my_special_csrf")
        client = _make_client(html)

        _, csrf_token = client._scrape_diary_page(TARGET_DATE)
        assert csrf_token == "my_special_csrf"

    @responses_lib.activate
    def test_scrape_no_csrf_meta_returns_empty_string(self) -> None:
        """CSRF meta タグがない場合は空文字列を返すこと."""
        html = "<html><head></head><body></body></html>"
        client = _make_client(html)

        _, csrf_token = client._scrape_diary_page(TARGET_DATE)
        assert csrf_token == ""

    @responses_lib.activate
    def test_scrape_empty_diary_returns_empty_list(self) -> None:
        """エントリがない日記は空リストを返すこと."""
        html = _build_diary_page_html({
            "Breakfast": [],
            "Lunch": [],
        })
        client = _make_client(html)

        entries, _ = client._scrape_diary_page(TARGET_DATE)
        assert entries == []

    @responses_lib.activate
    def test_scrape_japanese_section_headers(self) -> None:
        """日本語セクション名（朝食/昼食/夕食/間食）が正しくマッピングされること."""
        html = _build_diary_page_html({
            "朝食": [{"id": "1", "calories": 300, "protein": 15, "fat": 8, "carbs": 40}],
            "昼食": [{"id": "2", "calories": 500, "protein": 25, "fat": 12, "carbs": 60}],
            "夕食": [{"id": "3", "calories": 700, "protein": 35, "fat": 20, "carbs": 80}],
            "間食": [{"id": "4", "calories": 200, "protein": 5, "fat": 6, "carbs": 25}],
        })
        client = _make_client(html)

        entries, _ = client._scrape_diary_page(TARGET_DATE)
        assert len(entries) == 4
        positions = {e.remove_path.split("/")[-1]: e.meal_position for e in entries}
        assert positions["1"] == 0  # 朝食
        assert positions["2"] == 1  # 昼食
        assert positions["3"] == 2  # 夕食
        assert positions["4"] == 3  # 間食

    @responses_lib.activate
    def test_scrape_unknown_section_is_ignored(self) -> None:
        """未知のセクション名は無視されること."""
        html = _build_diary_page_html({
            "Breakfast": [{"id": "1", "calories": 300, "protein": 15, "fat": 8, "carbs": 40}],
            "Unknown Meal": [{"id": "99", "calories": 100, "protein": 5, "fat": 2, "carbs": 10}],
        })
        client = _make_client(html)

        entries, _ = client._scrape_diary_page(TARGET_DATE)
        assert len(entries) == 1
        assert entries[0].meal_position == 0

    @responses_lib.activate
    def test_scrape_row_without_delete_link_is_ignored(self) -> None:
        """削除リンクのない行（合計行等）は無視されること."""
        html = """<!DOCTYPE html>
<html><head><meta name="csrf-token" content="tok"></head>
<body>
<table class="main-title-2">
  <thead><tr><td class="first alt">Breakfast</td></tr></thead>
  <tbody>
    <tr>
      <td class="delete">
        <a rel="nofollow" data-method="delete" href="/ja/food/remove/10">
          <i class="icon-minus-sign"></i>
        </a>
      </td>
      <td class="calories">300</td>
      <td class="protein">15</td>
      <td class="fat">8</td>
      <td class="carbohydrates">40</td>
    </tr>
    <tr class="total">
      <td colspan="2">Total</td>
      <td class="calories">300</td>
    </tr>
  </tbody>
</table>
</body></html>"""
        client = _make_client(html)

        entries, _ = client._scrape_diary_page(TARGET_DATE)
        assert len(entries) == 1
        assert entries[0].remove_path == "/ja/food/remove/10"

    @responses_lib.activate
    def test_scrape_uses_cache_for_same_date(self) -> None:
        """初回構築時にフェッチした HTML がキャッシュされ、同一日付の再呼び出しでは再フェッチしない."""
        html = _build_diary_page_html({
            "Breakfast": [{"id": "1", "calories": 300, "protein": 15, "fat": 8, "carbs": 40}]
        })
        client = _make_client(html)

        # init で 2 calls (diary + auth_token)
        assert len(responses_lib.calls) == 2

        client._scrape_diary_page(TARGET_DATE)
        client._scrape_diary_page(TARGET_DATE)

        # キャッシュ済みなので追加 GET は発生しない
        assert len(responses_lib.calls) == 2

    @responses_lib.activate
    def test_scrape_different_date_triggers_fresh_fetch(self) -> None:
        """異なる日付でスクレイピングするとキャッシュが効かず再フェッチすること."""
        client = _make_client(_build_diary_page_html())

        other_date = date(2026, 5, 1)
        other_html = _build_diary_page_html({
            "Lunch": [{"id": "x", "calories": 500, "protein": 25, "fat": 12, "carbs": 60}]
        })
        _add_diary_page_mock(other_html)

        entries, _ = client._scrape_diary_page(other_date)
        assert len(entries) == 1
        assert entries[0].meal_position == 1

    @responses_lib.activate
    def test_scrape_comma_separated_values_parsed(self) -> None:
        """カンマ区切りの数値（1,234）が正しくパースされること."""
        html = _build_diary_page_html({
            "Breakfast": [{"id": "1", "calories": "1,234", "protein": 20, "fat": 10, "carbs": 50}]
        })
        client = _make_client(html)

        entries, _ = client._scrape_diary_page(TARGET_DATE)
        assert entries[0].calories == 1234.0


class TestGetMealEntries:
    @responses_lib.activate
    def test_get_meal_entries_returns_matching_entries(self, fixture_html) -> None:
        """指定食事区分のエントリが正しく返されること（フィクスチャHTMLを使用）."""
        client = _make_client(fixture_html("mfp_diary_page.html"))

        result = client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        assert len(result) == 2
        assert result[0].calories == 400.0
        assert result[0].meal_type is MealType.BREAKFAST
        assert result[1].calories == 100.0

    @responses_lib.activate
    def test_get_meal_entries_returns_empty_for_no_match(self) -> None:
        """対象食事区分にエントリがない場合は空リストを返すこと."""
        html = _build_diary_page_html({
            "Lunch": [{"id": "1", "calories": 500, "protein": 25, "fat": 12, "carbs": 60}]
        })
        client = _make_client(html)

        result = client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        assert result == []

    @responses_lib.activate
    def test_get_meal_entries_maps_nutritional_contents(self) -> None:
        """栄養値が MealNutrition に正しくマッピングされること."""
        html = _build_diary_page_html({
            "Snacks": [{"id": "1", "calories": 150, "protein": 8, "fat": 4, "carbs": 20}]
        })
        client = _make_client(html)

        result = client.get_meal_entries(TARGET_DATE, MealType.SNACKS)
        assert len(result) == 1
        assert result[0].protein_g == 8.0
        assert result[0].fat_g == 4.0
        assert result[0].carbs_g == 20.0

    @responses_lib.activate
    def test_get_meal_entries_includes_quick_add_entries(self) -> None:
        """quick_add エントリも含めて取得されること（Web スクレイピング経由）."""
        html = _build_diary_page_html({
            "Breakfast": [
                {"id": "q1", "calories": 300, "protein": 15, "fat": 8, "carbs": 40},
                {"id": "f1", "calories": 200, "protein": 10, "fat": 5, "carbs": 25},
            ],
            "Lunch": [
                {"id": "q2", "calories": 500, "protein": 25, "fat": 12, "carbs": 60}
            ],
        })
        client = _make_client(html)

        result = client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        assert len(result) == 2
        assert result[0].calories == 300.0
        assert result[1].calories == 200.0

    @responses_lib.activate
    def test_get_meal_entries_empty_diary_returns_empty(self) -> None:
        """エントリが一切ない日記は空リストを返すこと."""
        client = _make_client()

        result = client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        assert result == []

    @responses_lib.activate
    def test_get_meal_entries_uses_cached_diary_no_extra_request(self) -> None:
        """get_meal_entries はキャッシュ済み HTML を再利用し追加 GET を発生させない."""
        html = _build_diary_page_html({
            "Breakfast": [{"id": "1", "calories": 300, "protein": 15, "fat": 8, "carbs": 40}]
        })
        client = _make_client(html)
        baseline_calls = len(responses_lib.calls)

        client.get_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        client.get_meal_entries(TARGET_DATE, MealType.LUNCH)

        assert len(responses_lib.calls) == baseline_calls


class TestAddMealEntry:
    def _nutrition(self, meal_type: MealType = MealType.BREAKFAST) -> MealNutrition:
        return MealNutrition(
            meal_type=meal_type, calories=500.0, protein_g=25.0, fat_g=12.0, carbs_g=60.0
        )

    @responses_lib.activate
    def test_add_meal_entry_success_201(self) -> None:
        client = _make_client()
        responses_lib.add(responses_lib.POST, _QUICK_ADD_URL, json={}, status=201)
        client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_success_200(self) -> None:
        client = _make_client()
        responses_lib.add(responses_lib.POST, _QUICK_ADD_URL, json={}, status=200)
        client.add_meal_entry(TARGET_DATE, self._nutrition(MealType.LUNCH))

    @responses_lib.activate
    def test_add_meal_entry_401_raises_auth_error(self) -> None:
        client = _make_client()
        responses_lib.add(responses_lib.POST, _QUICK_ADD_URL, status=401)
        with pytest.raises(MfpAuthError):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_403_raises_auth_error(self) -> None:
        """登録 POST で 403（クッキー失効 / bot 検出）は MfpAuthError として送出される."""
        client = _make_client()
        responses_lib.add(responses_lib.POST, _QUICK_ADD_URL, status=403)
        with pytest.raises(MfpAuthError, match="403"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_5xx_raises_mfp_error(self, mock_mfp_sleep) -> None:
        client = _make_client()
        responses_lib.add(responses_lib.POST, _QUICK_ADD_URL, status=503, body="Service Unavailable")
        with pytest.raises(MfpError, match="HTTP 503"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_add_meal_entry_non_ok_raises_mfp_error(self) -> None:
        """400 等のリトライ対象外エラーは即座に MfpError を送出すること."""
        client = _make_client()
        responses_lib.add(responses_lib.POST, _QUICK_ADD_URL, status=400, body='{"error":"bad_request"}')
        with pytest.raises(MfpError, match="HTTP 400"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_network_error_raises_mfp_error(self) -> None:
        client = _make_client()
        responses_lib.add(
            responses_lib.POST,
            _QUICK_ADD_URL,
            body=requests.exceptions.ConnectionError("Network down"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            client.add_meal_entry(TARGET_DATE, self._nutrition())

    @responses_lib.activate
    def test_add_meal_entry_maps_meal_name_correctly(self) -> None:
        """食事区分が正しい meal_name にマッピングされることを確認."""
        client = _make_client()

        posted_body: list[dict[str, Any]] = []

        def _capture(request):  # type: ignore[no-untyped-def]
            posted_body.append(json.loads(request.body))
            return 201, {}, "{}"

        responses_lib.add_callback(responses_lib.POST, _QUICK_ADD_URL, callback=_capture)

        client.add_meal_entry(
            TARGET_DATE,
            MealNutrition(meal_type=MealType.DINNER, calories=700, protein_g=35, fat_g=20, carbs_g=80),
        )
        item = posted_body[0]["items"][0]
        assert item["meal_name"] == "Dinner"
        assert item["type"] == "quick_add"
        assert item["date"] == TARGET_DATE.isoformat()

    @responses_lib.activate
    def test_add_meal_entry_payload_structure(self) -> None:
        """クイックツールのペイロードに必要なフィールドがすべて含まれることを確認."""
        client = _make_client()

        posted_body: list[dict[str, Any]] = []

        def _capture(request):  # type: ignore[no-untyped-def]
            posted_body.append(json.loads(request.body))
            return 201, {}, "{}"

        responses_lib.add_callback(responses_lib.POST, _QUICK_ADD_URL, callback=_capture)

        client.add_meal_entry(
            TARGET_DATE,
            MealNutrition(meal_type=MealType.BREAKFAST, calories=500, protein_g=25, fat_g=12, carbs_g=60),
        )
        item = posted_body[0]["items"][0]
        nc = item["nutritional_contents"]
        assert nc["energy"]["value"] == "500"
        assert nc["energy"]["unit"] == "calories"
        assert nc["protein"] == 25.0
        assert nc["fat"] == 12.0
        assert nc["carbohydrates"] == 60.0

    @responses_lib.activate
    def test_add_meal_entry_includes_browser_headers(self) -> None:
        """登録 POST に Origin / Referer / Sec-Fetch-* が付与されること."""
        client = _make_client()

        sent_headers: list[dict] = []

        def _capture(request):  # type: ignore[no-untyped-def]
            sent_headers.append(dict(request.headers))
            return 201, {}, "{}"

        responses_lib.add_callback(responses_lib.POST, _QUICK_ADD_URL, callback=_capture)

        client.add_meal_entry(TARGET_DATE, self._nutrition())

        h = sent_headers[0]
        assert h.get("Origin") == _BASE_URL
        assert TARGET_DATE.isoformat() in h.get("Referer", "")
        assert h.get("Sec-Fetch-Site") == "same-origin"
        assert h.get("Sec-Fetch-Mode") == "cors"
        assert h.get("Authorization", "").startswith("Bearer ")

    @responses_lib.activate
    def test_add_meal_entry_referer_uses_request_target_date(self) -> None:
        """`add_meal_entry` の Referer は引数の target_date を使い、init 時の日付に依存しないこと."""
        client = _make_client()

        sent_headers: list[dict] = []

        def _capture(request):  # type: ignore[no-untyped-def]
            sent_headers.append(dict(request.headers))
            return 201, {}, "{}"

        responses_lib.add_callback(responses_lib.POST, _QUICK_ADD_URL, callback=_capture)

        other_date = date(2026, 5, 1)
        client.add_meal_entry(other_date, self._nutrition())

        assert other_date.isoformat() in sent_headers[0].get("Referer", "")
        assert TARGET_DATE.isoformat() not in sent_headers[0].get("Referer", "")

    @responses_lib.activate
    def test_add_meal_entry_invalidates_diary_cache(self) -> None:
        """登録成功後に対象日のキャッシュが破棄され、次回スクレイピングで再フェッチされること."""
        html = _build_diary_page_html({
            "Breakfast": [{"id": "1", "calories": 300, "protein": 15, "fat": 8, "carbs": 40}]
        })
        client = _make_client(html)

        responses_lib.add(responses_lib.POST, _QUICK_ADD_URL, json={}, status=201)
        # 登録後の再フェッチ用の diary レスポンスを追加登録しておく
        new_html = _build_diary_page_html({
            "Breakfast": [
                {"id": "1", "calories": 300, "protein": 15, "fat": 8, "carbs": 40},
                {"id": "2", "calories": 500, "protein": 25, "fat": 12, "carbs": 60},
            ]
        })
        _add_diary_page_mock(new_html)

        client.add_meal_entry(TARGET_DATE, self._nutrition())

        # キャッシュが破棄されたので次回 _scrape_diary_page は新しい HTML を取得する
        entries, _ = client._scrape_diary_page(TARGET_DATE)
        assert len(entries) == 2

    @responses_lib.activate
    def test_add_meal_entry_all_meal_names(self) -> None:
        """全食事区分が正しい meal_name にマッピングされることを確認."""
        expected = {
            MealType.BREAKFAST: "Breakfast",
            MealType.LUNCH: "Lunch",
            MealType.DINNER: "Dinner",
            MealType.SNACKS: "Snacks",
        }
        for meal_type, expected_name in expected.items():
            responses_lib.reset()
            client = _make_client()

            posted_body: list[dict[str, Any]] = []

            def _capture(request, _expected=expected_name):  # type: ignore[no-untyped-def]
                posted_body.append(json.loads(request.body))
                return 201, {}, "{}"

            responses_lib.add_callback(responses_lib.POST, _QUICK_ADD_URL, callback=_capture)

            client.add_meal_entry(
                TARGET_DATE,
                MealNutrition(meal_type=meal_type, calories=100, protein_g=5, fat_g=2, carbs_g=10),
            )
            assert posted_body[0]["items"][0]["meal_name"] == expected_name


class TestDeleteMealEntries:
    @responses_lib.activate
    def test_delete_meal_entries_success(self) -> None:
        """対象食事区分の全エントリが削除されること."""
        html = _build_diary_page_html({
            "Breakfast": [
                {"id": "e1", "calories": 400, "protein": 20, "fat": 10, "carbs": 50},
                {"id": "e2", "calories": 100, "protein": 5, "fat": 2, "carbs": 15},
            ],
            "Lunch": [
                {"id": "e3", "calories": 600, "protein": 30, "fat": 15, "carbs": 70}
            ],
        })
        client = _make_client(html)
        responses_lib.add(responses_lib.POST, f"{_BASE_URL}/ja/food/remove/e1", status=200)
        responses_lib.add(responses_lib.POST, f"{_BASE_URL}/ja/food/remove/e2", status=200)

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)

        # init: GET diary + GET auth_token + POST×2 = 4
        assert len(responses_lib.calls) == 4

    @responses_lib.activate
    def test_delete_meal_entries_no_matching_entries_does_nothing(self) -> None:
        """対象食事区分にエントリがない場合は削除リクエストを送らないこと."""
        html = _build_diary_page_html({
            "Lunch": [{"id": "e1", "calories": 500, "protein": 25, "fat": 12, "carbs": 60}]
        })
        client = _make_client(html)

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)

        # init のみ（DELETE なし） = 2
        assert len(responses_lib.calls) == 2

    @responses_lib.activate
    def test_delete_meal_entries_401_on_remove_raises_auth_error(self) -> None:
        """削除 POST で 401 の場合 MfpAuthError を送出すること."""
        html = _build_diary_page_html({
            "Dinner": [{"id": "e99", "calories": 800, "protein": 40, "fat": 25, "carbs": 90}]
        })
        client = _make_client(html)
        responses_lib.add(responses_lib.POST, f"{_BASE_URL}/ja/food/remove/e99", status=401)

        with pytest.raises(MfpAuthError):
            client.delete_meal_entries(TARGET_DATE, MealType.DINNER)

    @responses_lib.activate
    def test_delete_meal_entries_403_on_remove_raises_auth_error(self) -> None:
        """削除 POST で 403 はクッキー失効 / bot 検出として MfpAuthError を送出する."""
        html = _build_diary_page_html({
            "Dinner": [{"id": "e98", "calories": 800, "protein": 40, "fat": 25, "carbs": 90}]
        })
        client = _make_client(html)
        responses_lib.add(responses_lib.POST, f"{_BASE_URL}/ja/food/remove/e98", status=403)

        with pytest.raises(MfpAuthError, match="403"):
            client.delete_meal_entries(TARGET_DATE, MealType.DINNER)

    @responses_lib.activate
    def test_delete_meal_entries_5xx_raises_mfp_error(self, mock_mfp_sleep) -> None:
        """削除 POST で 5xx の場合 MfpError を送出すること."""
        html = _build_diary_page_html({
            "Snacks": [{"id": "e77", "calories": 200, "protein": 10, "fat": 5, "carbs": 25}]
        })
        client = _make_client(html)
        responses_lib.add(responses_lib.POST, f"{_BASE_URL}/ja/food/remove/e77", status=500)

        with pytest.raises(MfpError):
            client.delete_meal_entries(TARGET_DATE, MealType.SNACKS)
        assert mock_mfp_sleep.call_count == 3

    @responses_lib.activate
    def test_delete_meal_entries_302_is_accepted(self) -> None:
        """削除 POST の 302 リダイレクト（成功後の日記ページへのリダイレクト）は成功として扱うこと.

        MFP は削除成功時に 302 で日記ページへリダイレクトする。
        allow_redirects=False によりリダイレクトを追跡せず 302 を直接受け取る。
        """
        html = _build_diary_page_html({
            "Breakfast": [{"id": "e10", "calories": 300, "protein": 15, "fat": 8, "carbs": 35}]
        })
        client = _make_client(html)
        responses_lib.add(
            responses_lib.POST,
            f"{_BASE_URL}/ja/food/remove/e10",
            status=302,
            headers={"Location": f"{_BASE_URL}/ja/food/diary"},
        )

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)

    @responses_lib.activate
    def test_delete_meal_entries_sends_csrf_and_referer_headers(self) -> None:
        """削除リクエストに X-CSRF-Token と Referer ヘッダーが含まれること."""
        html = _build_diary_page_html(
            {"Breakfast": [{"id": "e5", "calories": 400, "protein": 20, "fat": 10, "carbs": 50}]},
            csrf_token="my_csrf_abc",
        )
        client = _make_client(html)

        sent_headers: list[dict] = []

        def _capture(request):  # type: ignore[no-untyped-def]
            sent_headers.append(dict(request.headers))
            return 200, {}, ""

        responses_lib.add_callback(
            responses_lib.POST, f"{_BASE_URL}/ja/food/remove/e5", callback=_capture
        )

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        h = sent_headers[0]
        assert h.get("X-CSRF-Token") == "my_csrf_abc"
        assert TARGET_DATE.isoformat() in h.get("Referer", "")
        assert h.get("Origin") == _BASE_URL
        assert h.get("Sec-Fetch-Site") == "same-origin"

    @responses_lib.activate
    def test_delete_meal_entries_no_csrf_sends_no_header(self) -> None:
        """CSRF トークンがない場合は X-CSRF-Token ヘッダーを送らないこと."""
        html = _build_diary_page_html(
            {"Breakfast": [{"id": "e6", "calories": 400, "protein": 20, "fat": 10, "carbs": 50}]},
            csrf_token="",
        )
        client = _make_client(html)

        sent_headers: list[dict] = []

        def _capture(request):  # type: ignore[no-untyped-def]
            sent_headers.append(dict(request.headers))
            return 200, {}, ""

        responses_lib.add_callback(
            responses_lib.POST, f"{_BASE_URL}/ja/food/remove/e6", callback=_capture
        )

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)
        assert "X-CSRF-Token" not in sent_headers[0]

    @responses_lib.activate
    def test_delete_meal_entries_uses_correct_remove_url(self) -> None:
        """削除 POST が正しい remove URL に送られること."""
        html = _build_diary_page_html({
            "Lunch": [{"id": "12682780266", "calories": 600, "protein": 30, "fat": 15, "carbs": 70}]
        })
        client = _make_client(html)

        posted_urls: list[str] = []

        def _capture(request):  # type: ignore[no-untyped-def]
            posted_urls.append(request.url)
            return 200, {}, ""

        responses_lib.add_callback(
            responses_lib.POST,
            f"{_BASE_URL}/ja/food/remove/12682780266",
            callback=_capture,
        )

        client.delete_meal_entries(TARGET_DATE, MealType.LUNCH)
        assert len(posted_urls) == 1
        assert "/ja/food/remove/12682780266" in posted_urls[0]

    @responses_lib.activate
    def test_delete_meal_entries_network_error_raises_mfp_error(self, mock_mfp_sleep) -> None:
        html = _build_diary_page_html({
            "Lunch": [{"id": "e55", "calories": 500, "protein": 25, "fat": 12, "carbs": 60}]
        })
        client = _make_client(html)
        responses_lib.add(
            responses_lib.POST,
            f"{_BASE_URL}/ja/food/remove/e55",
            body=requests.exceptions.ConnectionError("Network down"),
        )
        with pytest.raises(MfpError, match="MFP リクエストが"):
            client.delete_meal_entries(TARGET_DATE, MealType.LUNCH)

    @responses_lib.activate
    def test_delete_meal_entries_invalidates_diary_cache(self) -> None:
        """削除成功後にキャッシュが破棄され、再スクレイピングで新しい HTML を取得すること."""
        html = _build_diary_page_html({
            "Breakfast": [
                {"id": "e1", "calories": 400, "protein": 20, "fat": 10, "carbs": 50}
            ]
        })
        client = _make_client(html)
        responses_lib.add(
            responses_lib.POST, f"{_BASE_URL}/ja/food/remove/e1", status=200
        )
        new_html = _build_diary_page_html()  # 削除後は空
        _add_diary_page_mock(new_html)

        client.delete_meal_entries(TARGET_DATE, MealType.BREAKFAST)

        entries, _ = client._scrape_diary_page(TARGET_DATE)
        assert entries == []


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
    def test_403_does_not_retry_raises_auth_error(self, mock_mfp_sleep) -> None:
        """403 は一切リトライせず即座に MfpAuthError を送出すること（クッキー失効 / bot 検出）."""
        responses_lib.add(responses_lib.GET, _FAKE_URL, status=403)

        with pytest.raises(MfpAuthError, match="403"):
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
