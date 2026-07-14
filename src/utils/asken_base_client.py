"""あすけん共通クライアント - ログイン・セッション管理・リトライ処理."""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.asken.jp"
_LOGIN_URL = f"{_BASE_URL}/login/"

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# リトライ設定（認証エラーはリトライしない）
# max_retries=2 は「初回1回 + 最大2回リトライ = 合計最大3回試行」を意味する
_MAX_RETRIES: int = 2
_RETRY_BASE_DELAY: float = 1.0


class AskenAuthError(Exception):
    """あすけん認証エラー（リトライ不可）."""


class AskenError(Exception):
    """あすけん操作エラー."""


def request_with_retry(
    fn: Any,
    *args: Any,
    max_retries: int = _MAX_RETRIES,
    check_session_redirect: bool = True,
    **kwargs: Any,
) -> requests.Response:
    """接続エラー時に最大 max_retries 回指数バックオフでリトライする.

    - 認証エラー (401/403) はリトライせず即座に AskenAuthError を送出する
    - check_session_redirect=True のとき、ログインページへのリダイレクト（セッション切れ）
      も認証エラーとして扱う。ログインページ自体への GET/POST では False を指定すること
    """
    if max_retries < 0:
        raise ValueError(f"max_retries は 0 以上である必要があります: {max_retries}")

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp: requests.Response = fn(*args, **kwargs)
            if resp.status_code in (401, 403):
                raise AskenAuthError(
                    f"あすけんへのアクセスが拒否されました (HTTP {resp.status_code})"
                )
            resp.raise_for_status()
            if check_session_redirect and resp.url.startswith(_LOGIN_URL):
                raise AskenAuthError(
                    "セッションが切れています。再ログインが必要です。"
                )
            return resp
        except AskenAuthError:
            raise
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "リクエスト失敗 (attempt %d/%d): %s — %.1f秒後にリトライ",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
            else:
                raise AskenError(
                    f"リクエストが {max_retries + 1} 回失敗しました"
                ) from last_exc
    raise AskenError("リトライ上限に達しました")  # unreachable


class AskenBaseClient:
    """あすけんスクレイピングの共通基底クラス.

    ログイン・セッション管理を担う。各機能モジュールはこのクラスを継承して
    機能固有のスクレイピングメソッドを追加する。
    """

    def __init__(self, email: str, password: str) -> None:
        self._session: requests.Session = self._login(email, password)

    def _login(self, email: str, password: str) -> requests.Session:
        """ログインページから CSRF hidden input を取得してフォームログインする.

        CakePHP 3.x+ のフォーム保護には _csrfToken が必要。フォーム内の
        すべての hidden input を収集して payload に含める。

        Raises:
            AskenAuthError: 認証失敗（リトライ不可）
        """
        session = requests.Session()

        get_resp = request_with_retry(
            session.get,
            _LOGIN_URL,
            headers=_HEADERS,
            timeout=30,
            check_session_redirect=False,
        )

        soup = BeautifulSoup(get_resp.text, "lxml")
        login_container = soup.find("div", {"id": "login"})
        login_form = login_container.find("form") if login_container else None
        if login_form is None:
            raise AskenAuthError("ログインフォームが見つかりません")

        payload: dict[str, Any] = {}
        for hidden in login_form.find_all("input", {"type": "hidden"}):
            name = hidden.get("name")
            value = hidden.get("value", "")
            if isinstance(name, str) and name:
                payload[name] = value

        if "_csrfToken" not in payload:
            raise AskenAuthError("ログインページの CSRF トークンが見つかりません")
        if not payload["_csrfToken"]:
            raise AskenAuthError("CSRF トークンが空です")

        payload.update(
            {
                "CustomerMember[email]": email,
                "CustomerMember[passwd_plain]": password,
                "CustomerMember[autologin]": "1",
            }
        )

        action = login_form.get("action")
        action_path = action if isinstance(action, str) and action else "/login"
        post_url = urljoin(_LOGIN_URL, action_path)

        post_resp = request_with_retry(
            session.post,
            post_url,
            headers=_HEADERS,
            data=payload,
            timeout=30,
            check_session_redirect=False,
        )

        if "ログアウト" not in post_resp.text:
            raise AskenAuthError(
                "あすけんのログインに失敗しました（メールアドレスまたはパスワードを確認してください）"
            )

        logger.info("あすけんにログインしました")
        return session
