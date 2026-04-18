"""MyFitnessPal クライアント - セッション認証・食事エントリ操作."""
from __future__ import annotations

import logging
from datetime import date

from .models import MealNutrition, MealType

logger = logging.getLogger(__name__)


class MfpAuthError(Exception):
    """MyFitnessPal 認証エラー（リトライ不可）."""


class MfpError(Exception):
    """MyFitnessPal 操作エラー."""


class MyFitnessPalClient:
    """MyFitnessPal 内部 API クライアント."""

    def __init__(self, email: str, password: str) -> None:
        raise NotImplementedError

    def get_meal_entries(self, target_date: date, meal_type: MealType) -> list[MealNutrition]:
        """指定日・食事区分の既存エントリを取得する.

        Raises:
            MfpAuthError: 認証エラー
            MfpError: API エラー
        """
        raise NotImplementedError

    def add_meal_entry(self, target_date: date, nutrition: MealNutrition) -> None:
        """食事エントリを登録する.

        Raises:
            MfpAuthError: 認証エラー
            MfpError: API エラー
        """
        raise NotImplementedError

    def delete_meal_entries(self, target_date: date, meal_type: MealType) -> None:
        """指定日・食事区分のエントリをすべて削除する.

        Raises:
            MfpAuthError: 認証エラー
            MfpError: API エラー
        """
        raise NotImplementedError
