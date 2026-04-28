"""MyFitnessPal クライアント - セッション認証・食事エントリ操作.

## 調査済み API 仕様

### 認証フロー (クッキーベース、ブラウザ模倣)
1. Secrets Manager から取得した __Secure-next-auth.session-token を session に設定
2. GET https://www.myfitnesspal.com/ja/food/diary?date=YYYY-MM-DD
   - 実ブラウザ的なナビゲーションで認証状態を確立しつつ HTML/CSRF を取得
   - 返却 HTML を Lambda 実行内でキャッシュし、後続のスクレイピングで再利用
3. GET https://www.myfitnesspal.com/user/auth_token
   → JSON: {"access_token": "...", "user_id": 12345}
   - 注: `?refresh=true` は付与しない（毎回トークンをリフレッシュさせる挙動を避けて
     bot 検出シグナルを減らす）
   - 認証失敗時は空レスポンスまたは非 JSON（クッキー期限切れ等）

### 食事エントリ取得 (Web スクレイピング、認証時取得 HTML を再利用)
GET https://www.myfitnesspal.com/ja/food/diary?date=YYYY-MM-DD
- 各食事セクション（Breakfast/Lunch/Dinner/Snacks）の table.main-title-2 を解析
- 削除ボタン: <a data-method="delete" href="/ja/food/remove/{id}">
- 栄養値: <td class="calories">, <td class="protein">, <td class="fat">, <td class="carbohydrates">

### 食事エントリ登録 API (クイックツール, POST)
POST https://www.myfitnesspal.com/api/services/diary
Headers: Authorization: Bearer ..., Origin / Referer / Sec-Fetch-* (ブラウザと整合)
Body:
  {"items": [{"type": "quick_add", "meal_name": "Breakfast", "date": "YYYY-MM-DD",
   "nutritional_contents": {"energy": {"value": "N", "unit": "calories"},
   "protein": N, "fat": N, "carbohydrates": N}}]}
Response: 200 or 201

### 食事エントリ削除 (Web フォーム, POST)
POST https://www.myfitnesspal.com/ja/food/remove/{id}
Headers: X-CSRF-Token, X-Requested-With, Origin / Referer / Sec-Fetch-*
Response: 200, 204, or 302

### 注意事項
- 全 HTTP リクエストに実 Chrome (Windows) の標準ヘッダー (Sec-Fetch-*, Sec-Ch-Ua-*,
  Accept-Language 等) を付与し、bot 検出を回避する
- 自動アクセス検知でセッションクッキーが無効化されることがある。
  認証失敗時は MfpAuthError を送出し、handler 側で SNS 通知する
- /v2/diary GET API は quick_add タイプのエントリを返さないため Web スクレイピングを使用
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests
from bs4 import BeautifulSoup

from .models import MealNutrition, MealType

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.myfitnesspal.com"
# 内部 API は同一ホストを使う（テスト互換のため別名で公開）
_API_URL = _BASE_URL
_DIARY_WEB_URL = f"{_BASE_URL}/ja/food/diary"

# 実 Chrome 131 (Windows) と整合するベースヘッダー — 全リクエストに付与する
# 注: Accept-Encoding は明示しない。requests/urllib3 が対応可能な値（gzip, deflate）を
#     自動設定する。ブラウザに合わせて br や zstd を明示すると、当該パッケージ
#     （brotli / zstandard）未導入のため requests が復号できず、response.text が
#     圧縮バイナリ由来のゴミになり JSON パースに失敗する（cookie 失効と誤判定される）。
_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# ナビゲーション GET 用ヘッダー（diary ページなど）
_NAVIGATE_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Bearer 認証 API 用ヘッダー（fetch() からの呼び出しを模倣）
_API_FETCH_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "Origin": _BASE_URL,
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# Rails 系フォーム POST（/food/remove）用ヘッダー
_FORM_POST_HEADERS: dict[str, str] = {
    "Accept": "*/*",
    "Origin": _BASE_URL,
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "X-Requested-With": "XMLHttpRequest",
}

_MEAL_POSITIONS: dict[MealType, int] = {
    MealType.BREAKFAST: 0,
    MealType.LUNCH: 1,
    MealType.DINNER: 2,
    MealType.SNACKS: 3,
}

# 食事セクションヘッダーテキスト → meal_position マッピング（英語・日本語両対応）
_MEAL_POSITION_FROM_HEADER: dict[str, int] = {
    "Breakfast": 0,
    "朝食": 0,
    "Lunch": 1,
    "昼食": 1,
    "Dinner": 2,
    "夕食": 2,
    "Snacks": 3,
    "間食": 3,
}


# リトライ設定（認証エラーはリトライしない）
# max_retries=3 は「初回1回 + 最大3回リトライ = 合計最大4回試行」を意味する
_MFP_MAX_RETRIES: int = 3
_MFP_RETRY_BASE_DELAY: float = 1.0
# HTTP 429（レート制限）と 5xx（サーバーエラー）はリトライ対象
_MFP_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


@dataclass
class _DiaryWebEntry:
    """食事日記ページからスクレイピングした1エントリ."""

    remove_path: str
    meal_position: int
    calories: float
    protein: float
    fat: float
    carbs: float


class MfpAuthError(Exception):
    """MyFitnessPal 認証エラー（リトライ不可）."""


class MfpError(Exception):
    """MyFitnessPal 操作エラー."""


def _mfp_request_with_retry(
    fn: Callable[..., requests.Response],
    *args: Any,
    max_retries: int = _MFP_MAX_RETRIES,
    **kwargs: Any,
) -> requests.Response:
    """HTTP 429/5xx および接続エラー時に指数バックオフでリトライする.

    - 401: 即座に MfpAuthError を送出（リトライなし）
    - 403: 即座に MfpAuthError を送出（クッキー失効 / Cloudflare bot 検出の典型シグナル）
    - 429/5xx: 指数バックオフでリトライ（Retry-After ヘッダーを尊重）
    - 接続エラー/タイムアウト: 指数バックオフでリトライ
      （SSLError は ConnectionError 派生のためここに含まれる）
    - その他の HTTP エラー: MfpError として即座に失敗
    """
    for attempt in range(max_retries + 1):
        try:
            resp = fn(*args, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < max_retries:
                delay = _MFP_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "MFP 接続エラー (attempt %d/%d): %s — %.1f秒後にリトライ",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
                continue
            raise MfpError(
                f"MFP リクエストが {max_retries + 1} 回失敗しました: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise MfpError(f"MFP リクエストに失敗しました: {exc}") from exc

        if resp.status_code == 401:
            raise MfpAuthError("MFP 認証エラーが発生しました（HTTP 401）")

        if resp.status_code == 403:
            raise MfpAuthError(
                "MFP がアクセスを拒否しました（HTTP 403 — "
                "セッションクッキー失効または bot 検出の可能性があります）"
            )

        if resp.status_code in _MFP_RETRYABLE_STATUS:
            if attempt < max_retries:
                retry_after = resp.headers.get("Retry-After")
                delay = _MFP_RETRY_BASE_DELAY * (2**attempt)
                if retry_after and retry_after.isdigit() and int(retry_after) > 0:
                    delay = float(retry_after)
                logger.warning(
                    "MFP HTTP %d (attempt %d/%d) — %.1f秒後にリトライ",
                    resp.status_code,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                )
                time.sleep(delay)
                continue
            raise MfpError(
                f"MFP リクエストが {max_retries + 1} 回失敗しました: HTTP {resp.status_code}"
            )

        return resp

    raise MfpError("リトライ上限に達しました")  # unreachable


def _cell_float(row: Any, css_class: str) -> float:
    """テーブル行から指定クラスの td の数値を取得する."""
    td = row.select_one(f"td.{css_class}")
    if not td:
        return 0.0
    text = td.get_text(strip=True).replace(",", "").strip()
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def _diary_referer(target_date: date) -> str:
    return f"{_DIARY_WEB_URL}?date={target_date.isoformat()}"


class MyFitnessPalClient:
    """MyFitnessPal 内部 API クライアント.

    実ブラウザに近い体裁を維持して bot 検出を回避するため:
    - 全リクエストに Chrome 標準ヘッダー (Sec-Fetch-*, Sec-Ch-Ua-*, Accept-Language 等) を付与
    - 認証時は実ブラウザと同様にまず diary ページを訪問し、続いて API を叩く
    - POST には Referer / Origin を付与
    - 取得した diary HTML は Lambda 単一実行内でキャッシュし、無駄なリクエストを削減
    """

    def __init__(self, session_cookie: str, target_date: date) -> None:
        self._session = requests.Session()
        self._session.headers.update(_BROWSER_HEADERS)
        self._access_token: str = ""
        self._user_id: str = ""
        # date -> (html, csrf_token) — 同一実行内で再利用。書き込み後は破棄してドリフトを防ぐ。
        self._diary_cache: dict[date, tuple[str, str]] = {}
        self._authenticate(session_cookie, target_date)

    def _authenticate(self, session_cookie: str, target_date: date) -> None:
        """セッションクッキーで認証する（ブラウザ的フロー）.

        1. Cookie をセット
        2. diary ページを GET（ナビゲーション模倣 + CSRF/HTML キャッシュ）
        3. /user/auth_token を GET（?refresh=true は付与せず）

        Raises:
            MfpAuthError: クッキー無効・期限切れ・bot 検出
            MfpError: ページ取得失敗
        """
        self._session.cookies.set(
            "__Secure-next-auth.session-token",
            session_cookie,
            domain="www.myfitnesspal.com",
            secure=True,
        )

        diary_html, csrf_token = self._fetch_diary_page(target_date)
        self._diary_cache[target_date] = (diary_html, csrf_token)

        self._access_token, self._user_id = self._fetch_auth_token(target_date)

        logger.info(
            "MyFitnessPal にログインしました",
            extra={"user_id": self._user_id},
        )

    def _fetch_diary_page(self, target_date: date) -> tuple[str, str]:
        """日記ページを取得し HTML と CSRF トークンを返す.

        Raises:
            MfpAuthError: 401/403、ログインリダイレクト、Cloudflare チャレンジ等
                （いずれもクッキー失効または bot 検出を示す）
            MfpError: その他の HTTP エラーまたは想定外の HTML 構造
        """
        resp = _mfp_request_with_retry(
            self._session.get,
            _DIARY_WEB_URL,
            params={"date": target_date.isoformat()},
            headers=_NAVIGATE_HEADERS,
            timeout=30,
        )

        # 401/403 は _mfp_request_with_retry 内で MfpAuthError として送出済み
        if not resp.ok:
            raise MfpError(f"日記ページの取得に失敗しました: HTTP {resp.status_code}")

        # 200 でもログインリダイレクトや Cloudflare チャレンジページの可能性を判別する
        self._raise_if_unauthenticated_html(resp)

        soup = BeautifulSoup(resp.text, "lxml")
        csrf_meta = soup.select_one("meta[name='csrf-token']")
        csrf_token = ""
        if csrf_meta:
            content = csrf_meta.get("content")
            if content:
                csrf_token = str(content)

        return resp.text, csrf_token

    @staticmethod
    def _raise_if_unauthenticated_html(resp: requests.Response) -> None:
        """200 OK だが内容が認証エラー相当（ログイン画面・Cloudflare チャレンジ）なら MfpAuthError."""
        from urllib.parse import urlparse

        final_path = urlparse(resp.url or "").path.lower().rstrip("/")
        # 部分一致だと /login-history 等の誤検知が起きるため、パス末尾で判定
        login_paths = (
            "/login",
            "/signin",
            "/sign-in",
            "/sign_in",
            "/users/sign_in",
            "/account/sign_in",
        )
        if any(final_path.endswith(p) for p in login_paths):
            raise MfpAuthError(
                "日記ページへのアクセスがログイン画面へリダイレクトされました"
                "（セッションクッキーが無効または期限切れの可能性があります）"
            )

        # Cloudflare チャレンジ検出は誤検知を避けるため複合条件で判定する。
        # MFP 通常レスポンスは Cloudflare 経由でも /cdn-cgi/challenge-platform を含まないため、
        # チャレンジ専用パスとチャレンジ専用 title マーカーの両方を見る。
        sample = resp.text[:5000].lower() if resp.text else ""
        cloudflare_path_markers = ("/cdn-cgi/challenge", "cf-challenge")
        cloudflare_title_markers = (
            "<title>just a moment",
            "<title>attention required",
            "<title>checking your browser",
        )
        if any(marker in sample for marker in cloudflare_path_markers) or \
                any(marker in sample for marker in cloudflare_title_markers):
            raise MfpAuthError(
                "Cloudflare の bot 検出によりブロックされた可能性があります"
                "（HTML にチャレンジページのマーカーを検出）"
            )

    def _fetch_auth_token(self, target_date: date) -> tuple[str, str]:
        """/user/auth_token を呼び出して access_token / user_id を取得する.

        ?refresh=true は付与しない（毎回リフレッシュさせる挙動が bot 検出シグナルになるため）.
        """
        headers = {
            **_API_FETCH_HEADERS,
            "Referer": _diary_referer(target_date),
        }
        auth_resp = _mfp_request_with_retry(
            self._session.get,
            f"{_BASE_URL}/user/auth_token",
            headers=headers,
            timeout=30,
        )
        if not auth_resp.ok:
            raise MfpAuthError(
                f"認証トークンの取得に失敗しました: HTTP {auth_resp.status_code}"
            )

        if not auth_resp.text:
            raise MfpAuthError(
                "認証トークンのレスポンスが空です（セッションクッキーが無効または期限切れの可能性があります）"
            )

        try:
            auth_data = auth_resp.json()
            access_token = auth_data.get("access_token")
            user_id = auth_data.get("user_id")
        except requests.exceptions.JSONDecodeError as exc:
            raise MfpAuthError(
                f"認証トークンの解析に失敗しました（セッションクッキーが無効または期限切れの可能性があります）: {exc}"
            ) from exc

        if not access_token:
            raise MfpAuthError(
                "認証トークンに access_token が含まれていません"
                "（セッションクッキーが無効または期限切れの可能性があります）"
            )
        if user_id is None:
            raise MfpAuthError(
                "認証トークンに user_id が含まれていません"
                "（セッションクッキーが無効または期限切れの可能性があります）"
            )

        return str(access_token), str(user_id)

    def _api_headers(self, target_date: date) -> dict[str, str]:
        return {
            **_API_FETCH_HEADERS,
            "Authorization": f"Bearer {self._access_token}",
            "mfp-client-id": "mfp-main-js",
            "mfp-user-id": self._user_id,
            "Content-Type": "application/json",
            "Referer": _diary_referer(target_date),
        }

    def _form_post_headers(self, target_date: date) -> dict[str, str]:
        return {
            **_FORM_POST_HEADERS,
            "Referer": _diary_referer(target_date),
        }

    def _scrape_diary_page(self, target_date: date) -> tuple[list[_DiaryWebEntry], str]:
        """食事日記ページからエントリと CSRF を抽出する.

        認証時に取得済みなら HTML/CSRF キャッシュを再利用。それ以外は GET し直す。

        Raises:
            MfpAuthError: 認証エラー（401）
            MfpError: ページ取得失敗
        """
        if target_date in self._diary_cache:
            html, csrf_token = self._diary_cache[target_date]
        else:
            html, csrf_token = self._fetch_diary_page(target_date)
            self._diary_cache[target_date] = (html, csrf_token)

        soup = BeautifulSoup(html, "lxml")

        entries: list[_DiaryWebEntry] = []
        tables = soup.select("table.main-title-2")

        if not tables:
            logger.warning(
                "日記ページに食事セクション（table.main-title-2）が見つかりませんでした。"
                "MFP の HTML 構造が変更された可能性があります: %s",
                target_date,
            )
            return entries, csrf_token

        for table in tables:
            header_td = table.select_one("thead td.first, thead th.first")
            if not header_td:
                continue
            header_text = header_td.get_text(strip=True)
            meal_pos = _MEAL_POSITION_FROM_HEADER.get(header_text)
            if meal_pos is None:
                logger.debug("未知の食事セクション: %s", header_text)
                continue

            for row in table.select("tbody tr"):
                delete_link = row.select_one("a[data-method='delete']")
                if not delete_link:
                    continue
                href = delete_link.get("href", "")
                if not href or "/food/remove/" not in str(href):
                    continue

                entries.append(
                    _DiaryWebEntry(
                        remove_path=str(href),
                        meal_position=meal_pos,
                        calories=_cell_float(row, "calories"),
                        protein=_cell_float(row, "protein"),
                        fat=_cell_float(row, "fat"),
                        carbs=_cell_float(row, "carbohydrates"),
                    )
                )

        if not entries:
            logger.debug("日記ページにエントリが見つかりませんでした: %s", target_date)

        return entries, csrf_token

    def get_meal_entries(self, target_date: date, meal_type: MealType) -> list[MealNutrition]:
        """指定日・食事区分の既存エントリを日記ページのスクレイピングで取得する.

        Raises:
            MfpAuthError: 認証エラー
            MfpError: ページ取得・パースエラー
        """
        meal_position = _MEAL_POSITIONS[meal_type]
        entries, _ = self._scrape_diary_page(target_date)
        return [
            MealNutrition(
                meal_type=meal_type,
                calories=e.calories,
                protein_g=e.protein,
                fat_g=e.fat,
                carbs_g=e.carbs,
            )
            for e in entries
            if e.meal_position == meal_position
        ]

    def add_meal_entry(self, target_date: date, nutrition: MealNutrition) -> None:
        """食事エントリをクイックツールで登録する.

        Raises:
            MfpAuthError: 認証エラー
            MfpError: API エラー
        """
        meal_name = nutrition.meal_type.value

        entry: dict[str, Any] = {
            "type": "quick_add",
            "meal_name": meal_name,
            "date": target_date.isoformat(),
            "nutritional_contents": {
                "energy": {"value": str(round(nutrition.calories)), "unit": "calories"},
                "protein": round(nutrition.protein_g, 1),
                "fat": round(nutrition.fat_g, 1),
                "carbohydrates": round(nutrition.carbs_g, 1),
            },
        }

        # 部分書き込み（5xx リトライ後の最終失敗等）でも後続スクレイピングがドリフトしないよう
        # try/finally でキャッシュを必ず破棄する。
        try:
            resp = _mfp_request_with_retry(
                self._session.post,
                f"{_BASE_URL}/api/services/diary",
                headers=self._api_headers(target_date),
                json={"items": [entry]},
                timeout=30,
            )
            if resp.status_code not in (200, 201):
                raise MfpError(
                    f"日記エントリの登録に失敗しました: HTTP {resp.status_code} - {resp.text[:200]}"
                )
        finally:
            self._diary_cache.pop(target_date, None)

        logger.info(
            "MyFitnessPal に食事エントリを登録しました",
            extra={
                "meal_type": nutrition.meal_type.value,
                "date": target_date.isoformat(),
            },
        )

    def delete_meal_entries(self, target_date: date, meal_type: MealType) -> None:
        """指定日・食事区分のエントリをすべて削除する（上書き時に使用）.

        日記ページをスクレイピングして削除リンクを取得し、POST で削除する。

        Raises:
            MfpAuthError: 認証エラー
            MfpError: 削除失敗
        """
        meal_position = _MEAL_POSITIONS[meal_type]
        entries, csrf_token = self._scrape_diary_page(target_date)

        remove_paths = [
            e.remove_path for e in entries if e.meal_position == meal_position]

        # 途中失敗時は MfpError を送出する（一部削除済みの可能性あり）。
        # 呼び出し元 sync.py は食事区分単位でエラーを WARNING に留め、
        # 当該区分のみ登録をスキップするため不整合は最小限に抑えられる。
        # try/finally で部分削除でもキャッシュを必ず破棄し、後続スクレイピングのドリフトを防ぐ。
        try:
            for remove_path in remove_paths:
                headers = self._form_post_headers(target_date)
                if csrf_token:
                    headers["X-CSRF-Token"] = csrf_token

                resp = _mfp_request_with_retry(
                    self._session.post,
                    f"{_BASE_URL}{remove_path}",
                    headers=headers,
                    allow_redirects=False,
                    timeout=30,
                )
                if resp.status_code not in (200, 204, 302):
                    raise MfpError(
                        f"日記エントリの削除に失敗しました: HTTP {resp.status_code} - {resp.text[:200]}"
                    )
        finally:
            if remove_paths:
                self._diary_cache.pop(target_date, None)

        logger.info(
            "MyFitnessPal から食事エントリを削除しました",
            extra={
                "meal_type": meal_type.value,
                "date": target_date.isoformat(),
                "count": len(remove_paths),
            },
        )
