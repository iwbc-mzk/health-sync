"""データモデル - 食事データ等のデータクラス定義."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class MealType(Enum):
    BREAKFAST = "Breakfast"
    LUNCH = "Lunch"
    DINNER = "Dinner"
    SNACKS = "Snacks"


@dataclass
class MealNutrition:
    meal_type: MealType
    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float

    def is_nutritionally_equal(self, other: MealNutrition, rel_tol: float = 1e-5, abs_tol: float = 1e-9) -> bool:
        """栄養値が実質的に同一かを浮動小数点誤差を許容して比較する（重複チェック用）."""
        if self.meal_type is not other.meal_type:
            return False
        return (
            math.isclose(self.calories, other.calories, rel_tol=rel_tol, abs_tol=abs_tol)
            and math.isclose(self.protein_g, other.protein_g, rel_tol=rel_tol, abs_tol=abs_tol)
            and math.isclose(self.fat_g, other.fat_g, rel_tol=rel_tol, abs_tol=abs_tol)
            and math.isclose(self.carbs_g, other.carbs_g, rel_tol=rel_tol, abs_tol=abs_tol)
        )


@dataclass
class DailyMeals:
    date: date
    meals: list[MealNutrition] = field(default_factory=list)
