"""MyFitnessPal クライアント - セッション認証・食事エントリ操作.

## 調査済み API 仕様 (Phase 5.1, 更新)

### 認証フロー (クッキーベース)
1. Secrets Manager から取得した __Secure-next-auth.session-token を session に設定
2. GET  https://www.myfitnesspal.com/user/auth_token?refresh=true
   → JSON: {"access_token": "...", "user_id": 12345}
   → 認証失敗時は空レスポンスまたは非 JSON（クッキー期限切れ等）

### 食事エントリ取得 API (GET)
GET https://api.myfitnesspal.com/v2/diary
Params:
  entry_date=YYYY-MM-DD
  types=food_entry
  fields[]=nutritional_contents
  user_id=USER_ID
Headers:
  Authorization: Bearer {access_token}
  mfp-client-id: mfp-main-js
  mfp-user-id: {user_id}
Response: {"items": [{"id": "...", "meal_position": 0-3, "nutritional_contents": {...}}]}
meal_position: 0=Breakfast, 1=Lunch, 2=Dinner, 3=Snacks

### カスタム食品作成 (POST)
POST https://api.myfitnesspal.com/v2/foods
Body:
  {"public": false, "description": "...", "nutritional_contents": {"energy": {"unit": "calories", "value": N},
   "protein": N, "fat": N, "carbohydrates": N},
   "serving_sizes": [{"value": 1.0, "unit": "serving", "nutrition_multiplier": 1.0}]}
Response: {"item": {"id": "...", "version": "..."}}

### 食事エントリ登録 API (POST)
POST https://api.myfitnesspal.com/v2/diary
Body:
  {"items": [{"type": "food_entry", "date": "YYYY-MM-DD", "meal_position": 0-3,
   "food": {"id": "...", "version": "..."}, "servings": 1.0,
   "serving_size": {"value": 1.0, "unit": "serving", "nutrition_multiplier": 1.0}}]}
Response: 200 or 201

### 食事エントリ削除 API (DELETE)
DELETE https://api.myfitnesspal.com/v2/diary/{entry_id}
Response: 200 or 204

### CSRF トークン
- クッキーベース認証では不要（Bearer トークン認証）

### 注意事項
- 2022年8月以降、ログインページに CAPTCHA が追加された
  → 自動ログインが失敗する場合は MfpAuthError を送出（Lambda を失敗扱いにする）
- カスタム食品名に日付を含めることで重複登録を防止する
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any, cast

import requests

from .models import MealNutrition, MealType

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.myfitnesspal.com"
_API_URL = "https://api.myfitnesspal.com"

_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_MEAL_POSITIONS: dict[MealType, int] = {
    MealType.BREAKFAST: 0,
    MealType.LUNCH: 1,
    MealType.DINNER: 2,
    MealType.SNACKS: 3,
}

_CUSTOM_FOOD_PREFIX = "あすけん同期"

# リトライ設定（認証エラーはリトライしない）
# max_retries=3 は「初回1回 + 最大3回リトライ = 合計最大4回試行」を意味する
_MFP_MAX_RETRIES: int = 3
_MFP_RETRY_BASE_DELAY: float = 1.0
# HTTP 429（レート制限）と 5xx（サーバーエラー）はリトライ対象
_MFP_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


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
    - 429/5xx: 指数バックオフでリトライ（Retry-After ヘッダーを尊重）
    - 接続エラー/タイムアウト: 指数バックオフでリトライ
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
            raise MfpAuthError("MFP 認証エラーが発生しました")

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


class MyFitnessPalClient:
    """MyFitnessPal 内部 API クライアント."""

    def __init__(self, session_cookie: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._access_token: str = ""
        self._user_id: str = ""
        self._authenticate(session_cookie)

    def _authenticate(self, session_cookie: str) -> None:
        """セッションクッキーをセットして Bearer トークンを取得する."""
        self._session.cookies.set(
            "__Secure-next-auth.session-token",
            session_cookie,
            domain="www.myfitnesspal.com",
            secure=True,
        )

        auth_resp = _mfp_request_with_retry(
            self._session.get,
            f"{_BASE_URL}/user/auth_token",
            params={"refresh": "true"},
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
        except json.JSONDecodeError as exc:
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

        self._access_token = str(access_token)
        self._user_id = str(user_id)

        logger.info("MyFitnessPal にログインしました", extra={"user_id": self._user_id})

    def _api_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "mfp-client-id": "mfp-main-js",
            "mfp-user-id": self._user_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get_diary_items(self, target_date: date) -> list[dict[str, Any]]:
        """指定日の全食事エントリを取得する（内部用）."""
        resp = _mfp_request_with_retry(
            self._session.get,
            f"{_API_URL}/v2/diary",
            headers=self._api_headers(),
            params={
                "entry_date": target_date.isoformat(),
                "types": "food_entry",
                "fields[]": "nutritional_contents",
                "user_id": self._user_id,
            },
            timeout=30,
        )
        if not resp.ok:
            raise MfpError(f"日記データの取得に失敗しました: HTTP {resp.status_code}")

        try:
            return cast(list[dict[str, Any]], resp.json().get("items") or [])
        except (json.JSONDecodeError, ValueError) as exc:
            raise MfpError(f"日記取得レスポンスの JSON パースに失敗しました: {exc}") from exc

    def get_meal_entries(self, target_date: date, meal_type: MealType) -> list[MealNutrition]:
        """指定日・食事区分の既存エントリを取得する.

        Raises:
            MfpAuthError: 認証エラー
            MfpError: API エラー
        """
        meal_position = _MEAL_POSITIONS[meal_type]
        items = self._get_diary_items(target_date)
        entries = []
        for item in items:
            if item.get("meal_position") != meal_position:
                continue
            nc = item.get("nutritional_contents", {})
            energy = nc.get("energy", {})
            entries.append(
                MealNutrition(
                    meal_type=meal_type,
                    calories=float(energy.get("value", 0)),
                    protein_g=float(nc.get("protein", 0)),
                    fat_g=float(nc.get("fat", 0)),
                    carbs_g=float(nc.get("carbohydrates", 0)),
                )
            )
        return entries

    def _create_custom_food(self, nutrition: MealNutrition, target_date: date) -> tuple[str, str]:
        """カスタム食品を作成して (id, version) を返す.

        食品名に日付を含めることで同一日内の重複を防止する。
        """
        food_name = (
            f"{_CUSTOM_FOOD_PREFIX} {nutrition.meal_type.value} {target_date.isoformat()}"
        )
        food_payload = {
            "public": False,
            "description": food_name,
            "nutritional_contents": {
                "energy": {"unit": "calories", "value": round(nutrition.calories)},
                "protein": round(nutrition.protein_g, 1),
                "fat": round(nutrition.fat_g, 1),
                "carbohydrates": round(nutrition.carbs_g, 1),
            },
            "serving_sizes": [
                {"value": 1.0, "unit": "serving", "nutrition_multiplier": 1.0}
            ],
        }

        resp = _mfp_request_with_retry(
            self._session.post,
            f"{_API_URL}/v2/foods",
            headers=self._api_headers(),
            json=food_payload,
            timeout=30,
        )
        if not resp.ok:
            raise MfpError(
                f"カスタム食品の作成に失敗しました: HTTP {resp.status_code} - {resp.text[:200]}"
            )

        try:
            item = resp.json().get("item") or {}
        except (json.JSONDecodeError, ValueError) as exc:
            raise MfpError(
                f"カスタム食品作成レスポンスの JSON パースに失敗しました: {exc}"
            ) from exc

        food_id = item.get("id")
        if not food_id:
            raise MfpError(
                "カスタム食品作成レスポンスに id が含まれていません"
            )
        return str(food_id), item.get("version", "")

    def add_meal_entry(self, target_date: date, nutrition: MealNutrition) -> None:
        """食事エントリを登録する（カスタム食品として1エントリ登録）.

        Raises:
            MfpAuthError: 認証エラー
            MfpError: API エラー
        """
        food_id, food_version = self._create_custom_food(nutrition, target_date)
        meal_position = _MEAL_POSITIONS[nutrition.meal_type]

        entry: dict[str, Any] = {
            "type": "food_entry",
            "date": target_date.isoformat(),
            "meal_position": meal_position,
            "food": {"id": food_id, "version": food_version},
            "servings": 1.0,
            "serving_size": {
                "value": 1.0,
                "unit": "serving",
                "nutrition_multiplier": 1.0,
            },
        }

        resp = _mfp_request_with_retry(
            self._session.post,
            f"{_API_URL}/v2/diary",
            headers=self._api_headers(),
            json={"items": [entry]},
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise MfpError(
                f"日記エントリの登録に失敗しました: HTTP {resp.status_code} - {resp.text[:200]}"
            )

        logger.info(
            "MyFitnessPal に食事エントリを登録しました",
            extra={
                "meal_type": nutrition.meal_type.value,
                "date": target_date.isoformat(),
                "food_id": food_id,
            },
        )

    def delete_meal_entries(self, target_date: date, meal_type: MealType) -> None:
        """指定日・食事区分のエントリをすべて削除する（上書き時に使用）.

        Raises:
            MfpAuthError: 認証エラー
            MfpError: API エラー
        """
        meal_position = _MEAL_POSITIONS[meal_type]
        items = self._get_diary_items(target_date)

        entry_ids = [
            item["id"]
            for item in items
            if item.get("meal_position") == meal_position and "id" in item
        ]

        # 途中失敗時は MfpError を送出する（一部削除済みの可能性あり）。
        # 呼び出し元 sync.py は食事区分単位でエラーを WARNING に留め、
        # 当該区分のみ登録をスキップするため不整合は最小限に抑えられる。
        for entry_id in entry_ids:
            resp = _mfp_request_with_retry(
                self._session.delete,
                f"{_API_URL}/v2/diary/{entry_id}",
                headers=self._api_headers(),
                timeout=30,
            )
            if resp.status_code not in (200, 204):
                raise MfpError(
                    f"日記エントリの削除に失敗しました: HTTP {resp.status_code} - {resp.text[:200]}"
                )

        logger.info(
            "MyFitnessPal から食事エントリを削除しました",
            extra={
                "meal_type": meal_type.value,
                "date": target_date.isoformat(),
                "count": len(entry_ids),
            },
        )
