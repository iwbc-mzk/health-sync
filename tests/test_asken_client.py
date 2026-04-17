"""asken_client のユニットテスト."""
from __future__ import annotations

import json
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from asken_garmin_sync.asken_client import (
    AskenAuthError,
    AskenClient,
    AskenError,
    _request_with_retry,
)
from asken_garmin_sync.models import BodyComposition


# ─── _request_with_retry ────────────────────────────────────────────────────


class TestRequestWithRetry:
    def test_success_on_first_attempt(self):
        mock_fn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://www.asken.jp/wsp/comment/2026-04-13"
        mock_fn.return_value = mock_resp

        result = _request_with_retry(mock_fn, "url")
        assert result is mock_resp
        mock_fn.assert_called_once_with("url")

    def test_raises_auth_error_on_403(self):
        mock_fn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.url = "https://www.asken.jp/wsp/comment/"
        mock_fn.return_value = mock_resp

        with pytest.raises(AskenAuthError, match="403"):
            _request_with_retry(mock_fn, "url")

    def test_raises_auth_error_on_session_expired(self):
        """ログインページへのリダイレクトをセッション切れとして検出する."""
        mock_fn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://www.asken.jp/login/?redirect=..."
        mock_fn.return_value = mock_resp

        with pytest.raises(AskenAuthError, match="セッション"):
            _request_with_retry(mock_fn, "url")

    def test_retries_on_connection_error(self):
        import requests

        mock_fn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://www.asken.jp/"
        mock_fn.side_effect = [
            requests.ConnectionError("connection reset"),
            mock_resp,
        ]

        with patch("asken_garmin_sync.asken_client.time.sleep"):
            result = _request_with_retry(mock_fn, "url", max_retries=2)
        assert result is mock_resp
        assert mock_fn.call_count == 2

    def test_raises_after_max_retries(self):
        import requests

        mock_fn = MagicMock(side_effect=requests.ConnectionError("timeout"))

        with patch("asken_garmin_sync.asken_client.time.sleep"):
            with pytest.raises(AskenError, match="失敗しました"):
                _request_with_retry(mock_fn, "url", max_retries=2)
        assert mock_fn.call_count == 3

    def test_auth_error_not_retried(self):
        """認証エラーはリトライせず即座に例外を送出する."""
        mock_fn = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.url = "https://www.asken.jp/"
        mock_fn.return_value = mock_resp

        with pytest.raises(AskenAuthError):
            _request_with_retry(mock_fn, "url", max_retries=2)
        mock_fn.assert_called_once()

    def test_invalid_max_retries_raises(self):
        with pytest.raises(ValueError, match="max_retries"):
            _request_with_retry(MagicMock(), "url", max_retries=-1)


# ─── AskenClient._login ──────────────────────────────────────────────────────


class TestAskenClientLogin:
    def _make_session_mock(self, get_html: str, post_html: str, post_url: str):
        session = MagicMock()
        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.text = get_html
        get_resp.raise_for_status = MagicMock()

        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.text = post_html
        post_resp.url = post_url
        post_resp.raise_for_status = MagicMock()

        session.get.return_value = get_resp
        session.post.return_value = post_resp
        return session

    def test_login_success(self, fixture_html):
        login_page = fixture_html("login_page.html")
        success_page = fixture_html("login_success.html")

        with patch("asken_garmin_sync.asken_client.requests.Session") as mock_session_cls:
            session = self._make_session_mock(
                login_page, success_page, "https://www.asken.jp/"
            )
            mock_session_cls.return_value = session
            client = AskenClient("test@example.com", "password")
            assert client._session is session

    def test_login_fails_when_redirected_to_login(self, fixture_html):
        login_page = fixture_html("login_page.html")

        with patch("asken_garmin_sync.asken_client.requests.Session") as mock_session_cls:
            session = self._make_session_mock(
                login_page,
                "<html>ログインに失敗</html>",
                "https://www.asken.jp/login/",
            )
            mock_session_cls.return_value = session
            # POST 応答に「ログアウト」テキストがない → AskenAuthError（パスワード確認要）
            with pytest.raises(AskenAuthError, match="パスワード"):
                AskenClient("bad@example.com", "wrong")

    def test_login_fails_when_no_logout_link(self, fixture_html):
        login_page = fixture_html("login_page.html")

        with patch("asken_garmin_sync.asken_client.requests.Session") as mock_session_cls:
            session = self._make_session_mock(
                login_page,
                "<html><body>エラー</body></html>",
                "https://www.asken.jp/",
            )
            mock_session_cls.return_value = session
            with pytest.raises(AskenAuthError, match="パスワード"):
                AskenClient("test@example.com", "wrong_pw")

    def test_login_get_network_error_raises_asken_error(self, fixture_html):
        """ログインページ取得時のネットワーク障害は AskenError（認証失敗ではない）.

        _request_with_retry が最大リトライ後に "N 回失敗しました" メッセージで AskenError を送出。
        """
        import requests as req

        with patch("asken_garmin_sync.asken_client.requests.Session") as mock_session_cls:
            with patch("asken_garmin_sync.asken_client.time.sleep"):
                session = MagicMock()
                session.get.side_effect = req.ConnectionError("connection refused")
                mock_session_cls.return_value = session

                with pytest.raises(AskenError, match="失敗しました"):
                    AskenClient("test@example.com", "password")

    def test_login_post_network_error_raises_asken_error(self, fixture_html):
        """ログイン POST 時のネットワーク障害は AskenError（認証失敗ではない）.

        _request_with_retry が最大リトライ後に "N 回失敗しました" メッセージで AskenError を送出。
        """
        import requests as req

        with patch("asken_garmin_sync.asken_client.requests.Session") as mock_session_cls:
            with patch("asken_garmin_sync.asken_client.time.sleep"):
                session = MagicMock()
                get_resp = MagicMock()
                get_resp.status_code = 200
                get_resp.url = "https://www.asken.jp/login/"
                get_resp.text = fixture_html("login_page.html")
                get_resp.raise_for_status = MagicMock()
                session.get.return_value = get_resp
                session.post.side_effect = req.ConnectionError("connection refused")
                mock_session_cls.return_value = session

                with pytest.raises(AskenError, match="失敗しました"):
                    AskenClient("test@example.com", "password")

    def test_login_fails_when_csrf_token_missing(self):
        html_no_token = "<html><body><form id='indexForm'></form></body></html>"

        with patch("asken_garmin_sync.asken_client.requests.Session") as mock_session_cls:
            session = MagicMock()
            get_resp = MagicMock()
            get_resp.status_code = 200
            get_resp.text = html_no_token
            get_resp.raise_for_status = MagicMock()
            session.get.return_value = get_resp
            mock_session_cls.return_value = session

            with pytest.raises(AskenAuthError, match="CSRF"):
                AskenClient("test@example.com", "password")


# ─── AskenClient.get_body_composition ───────────────────────────────────────


class TestGetBodyComposition:
    def _make_client(self):
        with patch.object(AskenClient, "_login", return_value=MagicMock()):
            client = AskenClient.__new__(AskenClient)
            client._session = MagicMock()
            return client

    def _make_resp(self, html: str, url: str = "https://www.asken.jp/wsp/comment/2026-04-13"):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.url = url
        resp.raise_for_status = MagicMock()
        return resp

    def test_returns_body_composition(self, fixture_html):
        client = self._make_client()
        client._session.get.return_value = self._make_resp(fixture_html("comment_with_body.html"))

        result = client.get_body_composition(date(2026, 4, 13))

        assert result is not None
        assert result.weight_kg == 66.3
        assert result.body_fat_percent == 20.8
        assert result.date == date(2026, 4, 13)

    def test_returns_none_when_weight_empty(self, fixture_html):
        client = self._make_client()
        client._session.get.return_value = self._make_resp(fixture_html("comment_no_body.html"))

        result = client.get_body_composition(date(2026, 4, 13))
        assert result is None

    def test_returns_none_when_weight_field_missing(self):
        client = self._make_client()
        html = "<html><body><p>フィールドなし</p></body></html>"
        client._session.get.return_value = self._make_resp(html)

        result = client.get_body_composition(date(2026, 4, 13))
        assert result is None

    def test_network_error_raises_asken_error(self):
        """get_body_composition のネットワーク障害は AskenError。"""
        import requests as req

        client = self._make_client()
        client._session.get.side_effect = req.ConnectionError("timeout")

        with patch("asken_garmin_sync.asken_client.time.sleep"):
            with pytest.raises(AskenError):
                client.get_body_composition(date(2026, 4, 13))

    def test_raises_on_invalid_weight(self):
        client = self._make_client()
        html = """
        <html><body>
        <input name="data[Body][weight]" value="invalid" type="text"/>
        </body></html>
        """
        client._session.get.return_value = self._make_resp(html)

        with pytest.raises(AskenError, match="体重の解析"):
            client.get_body_composition(date(2026, 4, 13))

    def test_body_fat_parse_failure_returns_none_fat(self):
        """体脂肪率パース失敗時は body_fat_percent=None で BodyComposition を返す."""
        client = self._make_client()
        html = """
        <html><body>
        <input name="data[Body][weight]" value="65.0" type="text"/>
        <input name="data[Body][body_fat]" value="N/A" type="text"/>
        </body></html>
        """
        client._session.get.return_value = self._make_resp(html)

        result = client.get_body_composition(date(2026, 4, 13))
        assert result is not None
        assert result.weight_kg == 65.0
        assert result.body_fat_percent is None


# ─── AskenClient._get_exercise_entries ──────────────────────────────────────


class TestGetExerciseEntries:
    def _make_client(self):
        client = AskenClient.__new__(AskenClient)
        client._session = MagicMock()
        return client

    def _make_resp(self, html: str):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.url = "https://www.asken.jp/wsp/exercise/2026-04-13"
        resp.raise_for_status = MagicMock()
        return resp

    def test_parses_entries(self, fixture_html):
        client = self._make_client()
        client._session.get.return_value = self._make_resp(
            fixture_html("exercise_with_entries.html")
        )
        entries = client._get_exercise_entries(date(2026, 4, 13))
        assert entries == [("0", "authcode_abc123", "1061")]

    def test_parses_multiple_entries(self):
        """複数エントリ（異なる code を含む）を正しく取得する."""
        client = self._make_client()
        html = """
        <html><body>
        <script type="text/javascript">
        WspExerciseV2.exeDatas = {"do":"1","total":"58","menus":[
            {"item_type":"0","authcode":"authcode_aaa","amount":"5","code":"1061"},
            {"item_type":"0","authcode":"authcode_bbb","amount":"5","code":"2000"}
        ]};
        WspExerciseV2.view_list();
        </script>
        </body></html>
        """
        resp = self._make_resp(html)
        client._session.get.return_value = resp
        entries = client._get_exercise_entries(date(2026, 4, 13))
        assert entries == [("0", "authcode_aaa", "1061"), ("0", "authcode_bbb", "2000")]

    def test_returns_empty_when_no_script_data(self):
        """exeDatas が存在しない場合は空リストを返す."""
        client = self._make_client()
        html = "<html><body><p>運動なし</p></body></html>"
        resp = self._make_resp(html)
        client._session.get.return_value = resp
        entries = client._get_exercise_entries(date(2026, 4, 13))
        assert entries == []

    def test_empty_page_returns_empty_list(self, fixture_html):
        client = self._make_client()
        client._session.get.return_value = self._make_resp(
            fixture_html("exercise_empty.html")
        )
        entries = client._get_exercise_entries(date(2026, 4, 13))
        assert entries == []



# ─── AskenClient._delete_exercise_entry ─────────────────────────────────────


class TestDeleteExerciseEntry:
    def _make_client(self):
        client = AskenClient.__new__(AskenClient)
        client._session = MagicMock()
        return client

    def _make_get_resp(self, text: str = "") -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.url = "https://www.asken.jp/exercise/delete_v2/0/authcode_abc"
        resp.raise_for_status = MagicMock()
        resp.text = text
        return resp

    def test_success_with_empty_body(self):
        """削除 API が空ボディで 200 を返す場合は成功とみなす."""
        client = self._make_client()
        client._session.get.return_value = self._make_get_resp()
        client._delete_exercise_entry(date(2026, 4, 13), "0", "authcode_abc")

    def test_success_with_html_body(self):
        """削除 API が HTML 等の非 JSON ボディで 200 を返す場合も成功とみなす."""
        client = self._make_client()
        client._session.get.return_value = self._make_get_resp("<html>OK</html>")
        client._delete_exercise_entry(date(2026, 4, 13), "0", "authcode_abc")

    def test_http_403_raises_auth_error(self):
        """削除 API が 403 を返す場合は AskenAuthError を送出する（リトライしない）."""
        resp = MagicMock()
        resp.status_code = 403
        resp.url = "https://www.asken.jp/exercise/delete_v2/0/authcode_abc"
        resp.raise_for_status = MagicMock()
        client = self._make_client()
        client._session.get.return_value = resp
        with pytest.raises(AskenAuthError):
            client._delete_exercise_entry(date(2026, 4, 13), "0", "authcode_abc")
        client._session.get.assert_called_once()

    def test_network_error_raises_asken_error(self):
        import requests as req

        client = self._make_client()
        client._session.get.side_effect = req.ConnectionError("connection reset")

        with patch("asken_garmin_sync.asken_client.time.sleep"):
            with pytest.raises(AskenError, match="失敗しました"):
                client._delete_exercise_entry(date(2026, 4, 13), "0", "authcode_abc")

        assert client._session.get.call_count == 3


# ─── AskenClient._add_exercise_entry ────────────────────────────────────────


class TestAddExerciseEntry:
    def _make_client(self):
        client = AskenClient.__new__(AskenClient)
        client._session = MagicMock()
        return client

    def _make_post_resp(self, body: dict) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.url = "https://www.asken.jp/exercise/add/1"
        resp.raise_for_status = MagicMock()
        resp.json.return_value = body
        return resp

    def test_success(self):
        client = self._make_client()
        client._session.post.return_value = self._make_post_resp({"result": "OK"})
        # 例外が発生しないことを確認
        client._add_exercise_entry(date(2026, 4, 13), exercise_id=1, amount=30)

    def test_raises_on_non_ok_result(self):
        client = self._make_client()
        client._session.post.return_value = self._make_post_resp({"result": "NG", "message": "error"})
        with pytest.raises(AskenError, match="失敗"):
            client._add_exercise_entry(date(2026, 4, 13), exercise_id=1, amount=30)

    def test_network_error_raises_asken_error(self):
        """POST ネットワーク障害: ConnectionError → _request_with_retry → AskenError."""
        import requests as req

        client = self._make_client()
        client._session.post.side_effect = req.ConnectionError("connection reset")

        with patch("asken_garmin_sync.asken_client.time.sleep"):
            with pytest.raises(AskenError, match="失敗しました"):
                client._add_exercise_entry(date(2026, 4, 13), exercise_id=1, amount=30)

        # _MAX_RETRIES=2 なので合計3回呼ばれる
        assert client._session.post.call_count == 3


# ─── register_activity_calories ─────────────────────────────────────────────


class TestRegisterActivityCalories:
    def _make_client(self):
        client = AskenClient.__new__(AskenClient)
        client._session = MagicMock()
        return client

    def test_network_error_on_get_entries_raises(self):
        """_get_exercise_entries の GET ネットワーク障害は AskenError。"""
        import requests as req

        client = self._make_client()
        client._session.get.side_effect = req.ConnectionError("timeout")

        with patch("asken_garmin_sync.asken_client.time.sleep"):
            with pytest.raises(AskenError):
                client.register_activity_calories(date(2026, 4, 13), calories=100)

    def test_network_error_on_add_entry_raises(self):
        """_add_exercise_entry の POST ネットワーク障害は AskenError。"""
        import requests as req

        client = self._make_client()
        with (
            patch.object(client, "_get_exercise_entries", return_value=[]),
            patch.object(client, "_add_exercise_entry", side_effect=AskenError("POST 失敗")),
        ):
            with pytest.raises(AskenError, match="POST 失敗"):
                client.register_activity_calories(date(2026, 4, 13), calories=100)

    def test_skips_when_calories_zero(self):
        client = self._make_client()
        with patch.object(client, "_get_exercise_entries") as mock_get:
            client.register_activity_calories(date(2026, 4, 13), calories=0)
            mock_get.assert_not_called()

    def test_skips_when_calories_negative(self):
        client = self._make_client()
        with patch.object(client, "_get_exercise_entries") as mock_get:
            client.register_activity_calories(date(2026, 4, 13), calories=-10)
            mock_get.assert_not_called()

    def test_deletes_existing_and_adds_new(self):
        """exercise_id が一致するエントリを削除して新規追加する."""
        client = self._make_client()
        # code="1061" が DEFAULT_EXERCISE_ID=1061 と一致するため削除対象
        with (
            patch.object(client, "_get_exercise_entries", return_value=[("0", "auth1", "1061")]),
            patch.object(client, "_delete_exercise_entry") as mock_del,
            patch.object(client, "_add_exercise_entry") as mock_add,
            patch("asken_garmin_sync.asken_client.time.sleep"),
        ):
            client.register_activity_calories(
                date(2026, 4, 13), calories=120, cal_per_min=4.0
            )
            mock_del.assert_called_once_with(date(2026, 4, 13), "0", "auth1")
            # 120 kcal ÷ 4.0 kcal/分 = 30分、exercise_id はデフォルト 1061
            mock_add.assert_called_once_with(date(2026, 4, 13), 1061, 30)

    def test_does_not_delete_manual_entries(self):
        """手動追加エントリ（code が exercise_id=1061 と不一致）は削除しない."""
        client = self._make_client()
        # code="9999" は DEFAULT_EXERCISE_ID=1061 と不一致 → 削除しない
        with (
            patch.object(client, "_get_exercise_entries", return_value=[("0", "auth_manual", "9999")]),
            patch.object(client, "_delete_exercise_entry") as mock_del,
            patch.object(client, "_add_exercise_entry"),
        ):
            client.register_activity_calories(date(2026, 4, 13), calories=120, cal_per_min=4.0)
            mock_del.assert_not_called()

    def test_deletes_only_script_entries_when_mixed(self):
        """スクリプト登録エントリと手動エントリが混在する場合、スクリプト登録分のみ削除する."""
        client = self._make_client()
        entries = [
            ("0", "auth_script", "1061"),  # スクリプト登録（DEFAULT_EXERCISE_ID=1061 と一致）
            ("0", "auth_manual", "9999"),  # 手動追加
        ]
        with (
            patch.object(client, "_get_exercise_entries", return_value=entries),
            patch.object(client, "_delete_exercise_entry") as mock_del,
            patch.object(client, "_add_exercise_entry"),
            patch("asken_garmin_sync.asken_client.time.sleep"),
        ):
            client.register_activity_calories(date(2026, 4, 13), calories=120, cal_per_min=4.0)
            mock_del.assert_called_once_with(date(2026, 4, 13), "0", "auth_script")

    def test_entry_with_empty_code_is_not_deleted(self):
        """code フィールドが空のエントリは安全のため削除しない（手動エントリとして保持）."""
        client = self._make_client()
        with (
            patch.object(client, "_get_exercise_entries", return_value=[("0", "auth_unknown", "")]),
            patch.object(client, "_delete_exercise_entry") as mock_del,
            patch.object(client, "_add_exercise_entry"),
        ):
            client.register_activity_calories(date(2026, 4, 13), calories=120, cal_per_min=4.0)
            mock_del.assert_not_called()

    def test_no_delete_when_no_existing_entries(self):
        client = self._make_client()
        with (
            patch.object(client, "_get_exercise_entries", return_value=[]),
            patch.object(client, "_delete_exercise_entry") as mock_del,
            patch.object(client, "_add_exercise_entry") as mock_add,
        ):
            client.register_activity_calories(date(2026, 4, 13), calories=80, cal_per_min=4.0)
            mock_del.assert_not_called()
            # 80 ÷ 4.0 = 20分、exercise_id はデフォルト 1061
            mock_add.assert_called_once_with(date(2026, 4, 13), 1061, 20)

    @pytest.mark.parametrize(
        "calories, cal_per_min, expected_minutes",
        [
            (120, 4.0, 30),   # 30.0 → 30
            (100, 4.0, 25),   # 25.0 → 25
            (110, 4.0, 30),   # 27.5 → 四捨五入で 30 (int(5.5+0.5)*5=int(6.0)*5=30)
            (90, 4.0, 25),    # 22.5/5=4.5 → int(4.5+0.5)*5=int(5.0)*5=25（round(4.5)=4の銀行家丸めを回避）
            (5, 4.0, 5),      # 1.25 → 最小5分
            (10, 4.0, 5),     # 2.5 → int(0.5+0.5)*5=int(1.0)*5=5
        ],
    )
    def test_calorie_to_minutes_conversion(
        self, calories: int, cal_per_min: float, expected_minutes: int
    ):
        client = self._make_client()
        with (
            patch.object(client, "_get_exercise_entries", return_value=[]),
            patch.object(client, "_add_exercise_entry") as mock_add,
        ):
            client.register_activity_calories(
                date(2026, 4, 13), calories=calories, cal_per_min=cal_per_min
            )
            mock_add.assert_called_once()
            actual_minutes = mock_add.call_args[0][2]
            assert actual_minutes == expected_minutes, (
                f"{calories}kcal ÷ {cal_per_min}kcal/分 → {actual_minutes}分 (期待: {expected_minutes}分)"
            )
