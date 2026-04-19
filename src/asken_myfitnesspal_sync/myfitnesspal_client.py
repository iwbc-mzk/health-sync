"""MyFitnessPal クライアント - セッション認証・食事エントリ操作.

## 調査済み API 仕様 (Phase 5.1)

### 認証フロー
1. GET  https://www.myfitnesspal.com/user/login
   → HTML ページから `authenticity_token` (hidden input) を取得
2. POST https://www.myfitnesspal.com/user/login
   body: user[email], user[password], authenticity_token
   → セッションクッキーが発行される
3. GET  https://www.myfitnesspal.com/user/auth_token?refresh=true
   → JSON: {"access_token": "...", "user_id": 12345}
   → 認証失敗時は非 JSON レスポンス（ログインページへリダイレクト等）

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
- ログインフォームの authenticity_token のみ必要
- API 呼び出しでは不要（Bearer トークン認証）

### 注意事項
- 2022年8月以降、ログインページに CAPTCHA が追加された
  → 自動ログインが失敗する場合は MfpAuthError を送出（Lambda を失敗扱いにする）
- カスタム食品名に日付を含めることで重複登録を防止する
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, cast

import requests
from bs4 import BeautifulSoup

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


class MfpAuthError(Exception):
    """MyFitnessPal 認証エラー（リトライ不可）."""


class MfpError(Exception):
    """MyFitnessPal 操作エラー."""


class MyFitnessPalClient:
    """MyFitnessPal 内部 API クライアント."""

    def __init__(self, email: str, password: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._access_token: str = ""
        self._user_id: str = ""
        self._login(email, password)

    def _login(self, email: str, password: str) -> None:
        """フォームベースのログインと Bearer トークン取得."""
        try:
            login_page = self._session.get(f"{_BASE_URL}/user/login", timeout=30)
            login_page.raise_for_status()
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise MfpError(f"ログインページへの接続に失敗しました: {exc}") from exc
        except requests.HTTPError as exc:
            raise MfpAuthError(f"ログインページの取得に失敗しました: {exc}") from exc
        except requests.RequestException as exc:
            raise MfpError(f"ログインページの取得に失敗しました: {exc}") from exc

        soup = BeautifulSoup(login_page.text, "lxml")
        token_input = soup.find("input", {"name": "authenticity_token"})
        if not token_input:
            raise MfpAuthError(
                "ログインページの CSRF トークン (authenticity_token) が見つかりません"
            )
        authenticity_token = token_input.get("value")
        if not authenticity_token:
            raise MfpAuthError(
                "ログインページの authenticity_token に value 属性がありません"
            )

        try:
            resp = self._session.post(
                f"{_BASE_URL}/user/login",
                data={
                    "user[email]": email,
                    "user[password]": password,
                    "authenticity_token": authenticity_token,
                },
                timeout=30,
                allow_redirects=True,
            )
            resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise MfpError(f"ログインリクエストへの接続に失敗しました: {exc}") from exc
        except requests.HTTPError as exc:
            raise MfpAuthError(f"ログインリクエストに失敗しました: {exc}") from exc
        except requests.RequestException as exc:
            raise MfpError(f"ログインリクエストに失敗しました: {exc}") from exc

        try:
            auth_resp = self._session.get(
                f"{_BASE_URL}/user/auth_token",
                params={"refresh": "true"},
                timeout=30,
            )
            auth_resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise MfpError(f"認証トークン取得への接続に失敗しました: {exc}") from exc
        except requests.HTTPError as exc:
            raise MfpAuthError(f"認証トークンの取得に失敗しました: {exc}") from exc
        except requests.RequestException as exc:
            raise MfpError(f"認証トークンの取得に失敗しました: {exc}") from exc

        try:
            auth_data = auth_resp.json()
            self._access_token = auth_data["access_token"]
            self._user_id = str(auth_data["user_id"])
        except (json.JSONDecodeError, KeyError) as exc:
            raise MfpAuthError(
                f"認証トークンの解析に失敗しました（ログイン失敗または CAPTCHA が発生した可能性があります）: {exc}"
            ) from exc

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
        try:
            resp = self._session.get(
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
        except requests.RequestException as exc:
            raise MfpError(f"日記データの取得に失敗しました: {exc}") from exc

        if resp.status_code == 401:
            raise MfpAuthError("日記取得で認証エラーが発生しました")
        if not resp.ok:
            raise MfpError(f"日記取得に失敗しました: HTTP {resp.status_code}")

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

        try:
            resp = self._session.post(
                f"{_API_URL}/v2/foods",
                headers=self._api_headers(),
                json=food_payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise MfpError(f"カスタム食品の作成に失敗しました: {exc}") from exc

        if resp.status_code == 401:
            raise MfpAuthError("カスタム食品作成で認証エラーが発生しました")
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

        try:
            resp = self._session.post(
                f"{_API_URL}/v2/diary",
                headers=self._api_headers(),
                json={"items": [entry]},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise MfpError(f"日記エントリの登録に失敗しました: {exc}") from exc

        if resp.status_code == 401:
            raise MfpAuthError("日記エントリ登録で認証エラーが発生しました")
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
            try:
                resp = self._session.delete(
                    f"{_API_URL}/v2/diary/{entry_id}",
                    headers=self._api_headers(),
                    timeout=30,
                )
            except requests.RequestException as exc:
                raise MfpError(f"日記エントリの削除に失敗しました: {exc}") from exc

            if resp.status_code == 401:
                raise MfpAuthError("日記エントリ削除で認証エラーが発生しました")
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
