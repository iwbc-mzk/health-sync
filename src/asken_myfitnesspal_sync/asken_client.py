"""あすけんクライアント - ログイン・食事データ取得."""
from __future__ import annotations

import logging
from datetime import date

from .models import DailyMeals

logger = logging.getLogger(__name__)


class AskenAuthError(Exception):
    """あすけん認証エラー（リトライ不可）."""


class AskenError(Exception):
    """あすけん操作エラー."""


class AskenClient:
    """あすけんスクレイピングクライアント（食事データ取得）."""

    def __init__(self, email: str, password: str) -> None:
        raise NotImplementedError

    def get_daily_meals(self, target_date: date) -> DailyMeals:
        """対象日の食事データ（朝食・昼食・夕食・間食）を取得する.

        Returns:
            DailyMeals: 食事区分ごとの栄養データ（欠食区分は含まない）

        Raises:
            AskenAuthError: 認証エラー
            AskenError: ページ取得・パース失敗
        """
        raise NotImplementedError
