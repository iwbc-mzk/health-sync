"""同期ロジック - あすけん → MyFitnessPal 食事データ同期."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from .asken_client import AskenClient
from .config import Credentials, get_credentials, get_target_date
from .models import MealNutrition, MealType
from .myfitnesspal_client import MfpAuthError, MyFitnessPalClient

logger = logging.getLogger(__name__)


@dataclass
class MealSyncResult:
    registered: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def _aggregate_nutrition(entries: list[MealNutrition], meal_type: MealType) -> MealNutrition:
    """複数エントリの栄養値を合算して単一の MealNutrition として返す."""
    return MealNutrition(
        meal_type=meal_type,
        calories=sum(e.calories for e in entries),
        protein_g=sum(e.protein_g for e in entries),
        fat_g=sum(e.fat_g for e in entries),
        carbs_g=sum(e.carbs_g for e in entries),
    )


def _is_same_nutrition(asken: MealNutrition, mfp_entries: list[MealNutrition]) -> bool:
    """あすけんデータと MFP 既存エントリが栄養値として同一かを判定する.

    MFP に複数エントリがある場合は合算して比較する。
    エントリが 0 件の場合は False を返す。
    """
    if not mfp_entries:
        return False
    if len(mfp_entries) == 1:
        return asken.is_nutritionally_equal(mfp_entries[0])
    aggregated = _aggregate_nutrition(mfp_entries, asken.meal_type)
    return asken.is_nutritionally_equal(aggregated)


def sync_meals(target_date: date, credentials: Credentials) -> MealSyncResult:
    """あすけんから食事データを取得し MyFitnessPal に同期する.

    食事区分ごとに重複チェックを行い、差分がある場合のみ登録・上書きする。
    個別区分のエラーは WARNING ログに記録して継続する。

    Returns:
        MealSyncResult: 登録・スキップ・エラー件数のサマリー

    Raises:
        AskenAuthError: あすけん認証エラー（get_daily_meals が失敗した場合）
        AskenError: あすけんデータ取得・パースエラー
        MfpAuthError: MyFitnessPal 認証エラー
    """
    result = MealSyncResult()

    asken_client = AskenClient(credentials.asken_email, credentials.asken_password)
    daily_meals = asken_client.get_daily_meals(target_date)

    if not daily_meals.meals:
        logger.info(
            "あすけんに食事データがないため MyFitnessPal 連携をスキップします: %s",
            target_date,
        )
        return result

    # MFP 認証は必要時のみ行う（毎呼び出しの auth_token 取得が bot 検出を誘発するため）
    mfp_client = MyFitnessPalClient(
        credentials.myfitnesspal_session_cookie, target_date
    )

    asken_by_type: dict[MealType, MealNutrition] = {m.meal_type: m for m in daily_meals.meals}

    for meal_type in MealType:
        asken_nutrition = asken_by_type.get(meal_type)

        if asken_nutrition is None:
            logger.debug(
                "あすけんに食事データなし（欠食）: %s %s",
                meal_type.value,
                target_date,
            )
            continue

        try:
            existing_entries = mfp_client.get_meal_entries(target_date, meal_type)

            if _is_same_nutrition(asken_nutrition, existing_entries):
                logger.info(
                    "スキップ（変更なし）: %s %s",
                    meal_type.value,
                    target_date,
                    extra={"meal_type": meal_type.value, "date": target_date.isoformat()},
                )
                result.skipped += 1
                continue

            if existing_entries:
                mfp_client.delete_meal_entries(target_date, meal_type)

            mfp_client.add_meal_entry(target_date, asken_nutrition)
            logger.info(
                "登録完了: %s %s",
                meal_type.value,
                target_date,
                extra={"meal_type": meal_type.value, "date": target_date.isoformat()},
            )
            result.registered += 1

        except MfpAuthError:
            raise
        except Exception as exc:
            msg = f"{meal_type.value} の同期に失敗しました: {exc}"
            logger.warning(msg, exc_info=True)
            result.errors.append(msg)

    return result


def run_sync(target_date: date | None = None, secret_name: str | None = None) -> dict[str, object]:
    """シークレット取得から同期実行までをまとめたエントリーポイント.

    Returns:
        同期結果を含む辞書（date, registered, skipped, errors）

    Raises:
        AskenAuthError: あすけん認証エラー
        AskenError: あすけんデータ取得・パースエラー
        MfpAuthError: MyFitnessPal 認証エラー
        ValueError: シークレット形式不正 / TARGET_DATE 形式不正
    """
    credentials = get_credentials(secret_name)
    if target_date is None:
        target_date = get_target_date()

    result = sync_meals(target_date, credentials)

    return {
        "date": target_date.isoformat(),
        "registered": result.registered,
        "skipped": result.skipped,
        "errors": result.error_count,
    }
