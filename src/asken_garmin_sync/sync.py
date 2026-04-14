"""同期オーケストレーション - あすけん ↔ Garmin Connect 双方向同期."""
from __future__ import annotations

import logging
from datetime import date
from typing import TypedDict

from .asken_client import AskenClient, AskenAuthError, AskenError
from .config import cleanup_token_dir, get_secrets, load_garmin_tokens, save_garmin_tokens
from .garmin_client import GarminAuthError, GarminClient, GarminError

logger = logging.getLogger(__name__)


def sync_body_composition_to_garmin(
    asken_client: AskenClient,
    garmin_client: GarminClient,
    target_date: date,
) -> bool:
    """あすけんから体重・体脂肪率を取得して Garmin Connect に登録する.

    Args:
        asken_client: 認証済みあすけんクライアント
        garmin_client: 認証済み Garmin Connect クライアント
        target_date: 同期対象日

    Returns:
        True: 同期成功
        False: あすけんにデータが存在しない（体重未記録）

    Raises:
        AskenAuthError: あすけん認証エラー
        AskenError: あすけん操作エラー
        GarminAuthError: Garmin 認証エラー
        GarminError: Garmin 操作エラー
    """
    body = asken_client.get_body_composition(target_date)
    if body is None:
        logger.info("あすけんに体重データが存在しないためスキップ: %s", target_date)
        return False

    garmin_client.add_body_composition(body)
    logger.info(
        "体重・体脂肪率を同期しました: %s weight=%.1fkg fat=%s",
        target_date,
        body.weight_kg,
        f"{body.body_fat_percent:.1f}%" if body.body_fat_percent is not None else "未記録",
    )
    return True


def sync_calories_to_asken(
    garmin_client: GarminClient,
    asken_client: AskenClient,
    target_date: date,
) -> bool:
    """Garmin Connect からアクティビティカロリーを取得してあすけんに登録する.

    Args:
        garmin_client: 認証済み Garmin Connect クライアント
        asken_client: 認証済みあすけんクライアント
        target_date: 同期対象日

    Returns:
        True: 同期成功
        False: Garmin にカロリーデータが存在しない（0 kcal）

    Raises:
        GarminAuthError: Garmin 認証エラー
        GarminError: Garmin 操作エラー
        AskenAuthError: あすけん認証エラー
        AskenError: あすけん操作エラー
    """
    activity = garmin_client.get_activity_calories(target_date)
    if activity.calories_burned <= 0:
        logger.info(
            "Garmin にアクティビティカロリーが存在しないためスキップ: %s", target_date
        )
        return False

    asken_client.register_activity_calories(target_date, activity.calories_burned)
    logger.info(
        "アクティビティカロリーを同期しました: %s %dkcal",
        target_date,
        activity.calories_burned,
    )
    return True


class _SyncResult(TypedDict):
    synced: bool
    error: str | None


class SyncResult(TypedDict):
    body_composition: _SyncResult
    calories: _SyncResult


def run_sync(target_date: date, secret_name: str | None = None) -> SyncResult:
    """あすけん ↔ Garmin Connect の双方向同期を実行する.

    トークンライフサイクル全体（取得 → 使用 → 保存 → クリーンアップ）を管理する。
    操作エラー（AskenError / GarminError）が発生しても、もう片方の同期は続行する。
    認証エラー（AskenAuthError / GarminAuthError）は即座に伝播する（リトライ不可）。

    Args:
        target_date: 同期対象日（JST 基準）
        secret_name: Secrets Manager のシークレット名（省略時は環境変数 SECRET_NAME）

    Returns:
        各同期方向の結果（synced: 同期成功フラグ, error: エラーメッセージ or None）

    Raises:
        AskenAuthError: あすけん認証エラー（即座に伝播）
        GarminAuthError: Garmin 認証エラー（即座に伝播）
    """
    result: SyncResult = {
        "body_composition": {"synced": False, "error": None},
        "calories": {"synced": False, "error": None},
    }

    garmin_initialized = False
    try:
        secrets = get_secrets(secret_name)
        load_garmin_tokens(secrets)

        # 認証エラーは即座に伝播（両クライアントとも）
        asken = AskenClient(secrets.asken_email, secrets.asken_password)
        garmin = GarminClient(secrets.garmin_email, secrets.garmin_password)
        # Garmin クライアントが初期化成功した段階でトークン保存フラグを立てる。
        # login() でトークンが更新された可能性があるため、auth error が後続で発生しても
        # finally でトークンを保存する。
        garmin_initialized = True

        # 6.1: あすけん → Garmin（体重・体脂肪率）
        # 認証エラーは捕捉せず伝播、操作エラーのみ捕捉して続行
        try:
            synced = sync_body_composition_to_garmin(asken, garmin, target_date)
            result["body_composition"]["synced"] = synced
        except (AskenError, GarminError) as exc:
            logger.error("体重・体脂肪率の同期に失敗しました: %s", exc)
            result["body_composition"]["error"] = str(exc)

        # 6.2: Garmin → あすけん（アクティビティカロリー）
        # 認証エラーは捕捉せず伝播、操作エラーのみ捕捉して続行
        try:
            synced = sync_calories_to_asken(garmin, asken, target_date)
            result["calories"]["synced"] = synced
        except (GarminError, AskenError) as exc:
            logger.error("アクティビティカロリーの同期に失敗しました: %s", exc)
            result["calories"]["error"] = str(exc)

    finally:
        # Garmin クライアントの初期化が成功した場合はトークンを保存する。
        # auth error が sync 中に発生した場合も、login() でトークンが更新されている
        # 可能性があるため、cleanup_token_dir() より前に必ず保存を試みる。
        # save_garmin_tokens() が例外を投げても cleanup_token_dir() は必ず実行する。
        if garmin_initialized:
            try:
                save_garmin_tokens(secret_name)
            except Exception:
                logger.error("Garmin トークンの保存に失敗しました", exc_info=True)
        cleanup_token_dir()

    return result
