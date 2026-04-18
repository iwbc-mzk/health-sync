"""同期ロジック - あすけん → MyFitnessPal 食事データ同期."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from .asken_client import AskenAuthError
from .config import Credentials
from .myfitnesspal_client import MfpAuthError

logger = logging.getLogger(__name__)


@dataclass
class MealSyncResult:
    registered: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def sync_meals(target_date: date, credentials: Credentials) -> MealSyncResult:
    """あすけんから食事データを取得し MyFitnessPal に同期する.

    食事区分ごとに重複チェックを行い、差分がある場合のみ登録・上書きする。
    個別区分のエラーは WARNING ログに記録して継続する。

    Returns:
        MealSyncResult: 登録・スキップ・エラー件数のサマリー

    Raises:
        AskenAuthError: あすけん認証エラー
        MfpAuthError: MyFitnessPal 認証エラー
    """
    raise NotImplementedError


def run_sync(target_date: date, secret_name: str | None = None) -> dict[str, object]:
    """シークレット取得から同期実行までをまとめたエントリーポイント.

    Returns:
        同期結果を含む辞書

    Raises:
        Exception: 認証エラー等の致命的エラー
    """
    raise NotImplementedError
