"""あすけんクライアント - 食事データ取得."""
from __future__ import annotations

import logging
import re
from datetime import date

from bs4 import BeautifulSoup

from utils.asken_base_client import (
    _BASE_URL,
    _HEADERS,
    AskenAuthError,
    AskenBaseClient,
    AskenError,
    request_with_retry,
)

from .models import DailyMeals, MealNutrition, MealType

logger = logging.getLogger(__name__)

# 食事区分ごとのアドバイスページ（朝食/昼食/夕食）
_ADVICE_URL = f"{_BASE_URL}/wsp/advice/{{date}}/{{meal_id}}"
# 1日合計のアドバイスページ（間食算出に使用）
_ADVICE_DAILY_URL = f"{_BASE_URL}/wsp/advice/{{date}}"

# あすけんの食事区分 ID（アドバイス URL のパス末尾）
_MEAL_ADVICE_IDS: dict[MealType, int] = {
    MealType.BREAKFAST: 3,
    MealType.LUNCH: 4,
    MealType.DINNER: 5,
}

# アドバイスページの栄養素名（日本語）→ MealNutrition フィールド名
_NUTRITION_FIELDS: dict[str, str] = {
    "エネルギー": "calories",
    "タンパク質": "protein_g",
    "脂質": "fat_g",
    "炭水化物": "carbs_g",
}

# 欠食時のメッセージ（このテキストが含まれる場合はデータなし）
_NO_FOOD_LOG_TEXT = "食事記録が無いため"

# re-export for backward compatibility with existing tests / callers
__all__ = ["AskenAuthError", "AskenError", "AskenClient"]


def _parse_nutrition_value(text: str) -> float:
    """栄養値テキストから数値を抽出する（単位を除去）.

    例: "500kcal" → 500.0、"20.5g" → 20.5
    """
    match = re.search(r"[\d.]+", text)
    if match is None:
        raise ValueError(f"栄養値の数値が見つかりません: {text!r}")
    return float(match.group())


class AskenClient(AskenBaseClient):
    """あすけんスクレイピングクライアント（食事データ取得）."""

    def get_daily_meals(self, target_date: date) -> DailyMeals:
        """対象日の食事データ（朝食・昼食・夕食・間食）を取得する.

        朝食・昼食・夕食は食事区分ごとのアドバイスページから取得する。
        間食は1日合計から朝昼夕を差し引いて算出する。

        Returns:
            DailyMeals: 食事区分ごとの栄養データ（欠食区分は含まない）

        Raises:
            AskenAuthError: 認証エラー
            AskenError: ページ取得・パース失敗
        """
        meals: list[MealNutrition] = []
        fetched: dict[MealType, MealNutrition] = {}

        for meal_type in (MealType.BREAKFAST, MealType.LUNCH, MealType.DINNER):
            nutrition = self._fetch_meal_nutrition(target_date, meal_type)
            if nutrition is not None:
                meals.append(nutrition)
                fetched[meal_type] = nutrition

        snack = self._fetch_snack_nutrition(target_date, fetched)
        if snack is not None:
            meals.append(snack)

        logger.info("食事データ取得: %s %d区分", target_date, len(meals))
        return DailyMeals(date=target_date, meals=meals)

    def _fetch_meal_nutrition(
        self, target_date: date, meal_type: MealType
    ) -> MealNutrition | None:
        """朝食・昼食・夕食のアドバイスページから栄養データを取得する.

        Returns:
            MealNutrition: データが存在する場合
            None: 欠食（"食事記録が無いため" のテキストが含まれる場合）

        Raises:
            AskenError: ページ取得失敗、またはHTML構造の変更によるパース失敗
        """
        meal_id = _MEAL_ADVICE_IDS[meal_type]
        url = _ADVICE_URL.format(date=target_date.isoformat(), meal_id=meal_id)
        resp = request_with_retry(
            self._session.get, url, headers=_HEADERS, timeout=30
        )

        if _NO_FOOD_LOG_TEXT in resp.text:
            logger.debug("欠食のためスキップ: %s %s", meal_type.value, target_date)
            return None

        values = self._parse_advice_html(resp.text)
        if values is None:
            raise AskenError(
                f"{meal_type.value} の栄養データをパースできませんでした。"
                f"あすけんのHTML構造が変更された可能性があります: {target_date}"
            )

        return MealNutrition(meal_type=meal_type, **values)

    def _fetch_snack_nutrition(
        self,
        target_date: date,
        meal_nutritions: dict[MealType, MealNutrition],
    ) -> MealNutrition | None:
        """間食の栄養データを「1日合計 - 朝食 - 昼食 - 夕食」で算出する.

        Returns:
            MealNutrition: 間食のデータが存在する場合
            None: 欠食または1日合計がない場合

        Raises:
            AskenError: ページ取得失敗、またはHTML構造の変更によるパース失敗
        """
        url = _ADVICE_DAILY_URL.format(date=target_date.isoformat())
        resp = request_with_retry(
            self._session.get, url, headers=_HEADERS, timeout=30
        )

        if _NO_FOOD_LOG_TEXT in resp.text:
            return None

        daily = self._parse_advice_html(resp.text)
        if daily is None:
            raise AskenError(
                f"1日合計の栄養データをパースできませんでした。"
                f"あすけんのHTML構造が変更された可能性があります: {target_date}"
            )

        snack_calories = daily["calories"]
        snack_protein = daily["protein_g"]
        snack_fat = daily["fat_g"]
        snack_carbs = daily["carbs_g"]

        for nutrition in meal_nutritions.values():
            snack_calories -= nutrition.calories
            snack_protein -= nutrition.protein_g
            snack_fat -= nutrition.fat_g
            snack_carbs -= nutrition.carbs_g

        # あすけんの表示丸めにより差分が微小な負の値になることがある
        snack_calories = max(0.0, snack_calories)
        snack_protein = max(0.0, snack_protein)
        snack_fat = max(0.0, snack_fat)
        snack_carbs = max(0.0, snack_carbs)

        if not (snack_calories or snack_protein or snack_fat or snack_carbs):
            return None

        return MealNutrition(
            meal_type=MealType.SNACKS,
            calories=snack_calories,
            protein_g=snack_protein,
            fat_g=snack_fat,
            carbs_g=snack_carbs,
        )

    def _parse_advice_html(self, html: str) -> dict[str, float] | None:
        """アドバイスページの HTML から栄養値を解析する.

        HTML 構造: <li class="line_left"> 内の <li class="title"> と <li class="val">

        Returns:
            dict: calories/protein_g/fat_g/carbs_g の数値マップ（4フィールドすべて揃った場合）
            None: 関連する栄養素が1つも見つからない、または必須フィールドが欠損している場合

        Raises:
            なし（パース失敗は None で返し、呼び出し元が AskenError を送出する）
        """
        soup = BeautifulSoup(html, "lxml")
        result: dict[str, float] = {}

        for item in soup.find_all("li", class_="line_left"):
            title_el = item.find("li", class_="title")
            val_el = item.find("li", class_="val")
            if title_el is None or val_el is None:
                continue

            name = title_el.get_text(strip=True)
            field = _NUTRITION_FIELDS.get(name)
            if field is None:
                continue

            try:
                result[field] = _parse_nutrition_value(val_el.get_text(strip=True))
            except ValueError:
                logger.warning(
                    "栄養値のパースに失敗しました: %r = %r",
                    name,
                    val_el.get_text(strip=True),
                )

        required = {"calories", "protein_g", "fat_g", "carbs_g"}
        if not required.issubset(result.keys()):
            return None

        return result
