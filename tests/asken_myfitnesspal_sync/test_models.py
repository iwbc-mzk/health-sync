"""データモデルのユニットテスト."""
from __future__ import annotations

from datetime import date

import pytest

from asken_myfitnesspal_sync.models import DailyMeals, MealNutrition, MealType


class TestMealType:
    def test_values(self) -> None:
        assert MealType.BREAKFAST.value == "Breakfast"
        assert MealType.LUNCH.value == "Lunch"
        assert MealType.DINNER.value == "Dinner"
        assert MealType.SNACKS.value == "Snacks"

    def test_all_members(self) -> None:
        assert set(MealType) == {MealType.BREAKFAST, MealType.LUNCH, MealType.DINNER, MealType.SNACKS}

    def test_from_value(self) -> None:
        assert MealType("Breakfast") is MealType.BREAKFAST
        assert MealType("Snacks") is MealType.SNACKS

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            MealType("Invalid")


class TestMealNutrition:
    def test_instantiation(self) -> None:
        meal = MealNutrition(
            meal_type=MealType.BREAKFAST,
            calories=500.0,
            protein_g=20.0,
            fat_g=15.0,
            carbs_g=60.0,
        )
        assert meal.meal_type is MealType.BREAKFAST
        assert meal.calories == 500.0
        assert meal.protein_g == 20.0
        assert meal.fat_g == 15.0
        assert meal.carbs_g == 60.0

    def test_zero_values(self) -> None:
        meal = MealNutrition(meal_type=MealType.SNACKS, calories=0.0, protein_g=0.0, fat_g=0.0, carbs_g=0.0)
        assert meal.calories == 0.0

    def test_equality(self) -> None:
        a = MealNutrition(MealType.LUNCH, 300.0, 10.0, 8.0, 45.0)
        b = MealNutrition(MealType.LUNCH, 300.0, 10.0, 8.0, 45.0)
        assert a == b

    def test_inequality(self) -> None:
        a = MealNutrition(MealType.LUNCH, 300.0, 10.0, 8.0, 45.0)
        b = MealNutrition(MealType.LUNCH, 301.0, 10.0, 8.0, 45.0)
        assert a != b

    def test_is_nutritionally_equal_same_values(self) -> None:
        a = MealNutrition(MealType.LUNCH, 300.0, 10.0, 8.0, 45.0)
        b = MealNutrition(MealType.LUNCH, 300.0, 10.0, 8.0, 45.0)
        assert a.is_nutritionally_equal(b)

    def test_is_nutritionally_equal_within_tolerance(self) -> None:
        a = MealNutrition(MealType.SNACKS, 250.0, 5.0, 8.0, 0.3)
        b = MealNutrition(MealType.SNACKS, 250.0, 5.0, 8.0, 0.1 + 0.2)
        assert a.is_nutritionally_equal(b)

    def test_is_nutritionally_equal_different_values(self) -> None:
        a = MealNutrition(MealType.BREAKFAST, 400.0, 15.0, 12.0, 55.0)
        b = MealNutrition(MealType.BREAKFAST, 401.0, 15.0, 12.0, 55.0)
        assert not a.is_nutritionally_equal(b)

    def test_is_nutritionally_equal_different_meal_type(self) -> None:
        a = MealNutrition(MealType.BREAKFAST, 300.0, 10.0, 8.0, 45.0)
        b = MealNutrition(MealType.LUNCH, 300.0, 10.0, 8.0, 45.0)
        assert not a.is_nutritionally_equal(b)

    def test_is_nutritionally_equal_zero_values(self) -> None:
        a = MealNutrition(MealType.SNACKS, 0.0, 0.0, 0.0, 0.0)
        b = MealNutrition(MealType.SNACKS, 0.0, 0.0, 0.0, 0.0)
        assert a.is_nutritionally_equal(b)


class TestDailyMeals:
    def test_instantiation_with_meals(self) -> None:
        target = date(2026, 4, 17)
        meals = [
            MealNutrition(MealType.BREAKFAST, 400.0, 15.0, 12.0, 55.0),
            MealNutrition(MealType.LUNCH, 600.0, 25.0, 18.0, 80.0),
        ]
        daily = DailyMeals(date=target, meals=meals)
        assert daily.date == target
        assert len(daily.meals) == 2

    def test_default_meals_empty(self) -> None:
        daily = DailyMeals(date=date(2026, 4, 17))
        assert daily.meals == []

    def test_meals_default_not_shared(self) -> None:
        a = DailyMeals(date=date(2026, 4, 17))
        b = DailyMeals(date=date(2026, 4, 18))
        a.meals.append(MealNutrition(MealType.DINNER, 700.0, 30.0, 20.0, 90.0))
        assert b.meals == []
