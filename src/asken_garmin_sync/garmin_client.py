"""Garmin Connect クライアント - 認証・体重登録・アクティビティカロリー取得.

NOTE: MFA（多要素認証）が無効なアカウントが必要。
      MFA が有効な場合、フルログインは対話的な確認コード入力を要求するため、
      Lambda 等の非対話環境では動作しない。
      MFA を無効にする方法: Garmin Connect アカウント設定 → セキュリティ →
      2 段階認証を無効化する。
      本実装では prompt_mfa=None を明示的に設定しており、MFA が要求された場合は
      GarminConnectAuthenticationError として即座に失敗する。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .config import GARMIN_TOKEN_DIR
from .models import ActivityCalories, BodyComposition

logger = logging.getLogger(__name__)

# タイムゾーン定数
_JST = ZoneInfo("Asia/Tokyo")

# リトライ設定（認証エラーはリトライしない）
# max_retries=3 は「初回1回 + 最大3回リトライ = 合計最大4回試行」を意味する
_MAX_RETRIES: int = 3
_RETRY_BASE_DELAY: float = 1.0  # 指数バックオフの基底（秒）


class GarminAuthError(Exception):
    """Garmin Connect 認証エラー（リトライ不可）."""


class GarminError(Exception):
    """Garmin Connect 操作エラー."""


def _call_with_retry(
    fn: Any,
    *args: Any,
    max_retries: int = _MAX_RETRIES,
    **kwargs: Any,
) -> Any:
    """Garmin API 呼び出しを 429 / 接続エラー時に指数バックオフでリトライする.

    - 認証エラー (GarminConnectAuthenticationError) は即座に GarminAuthError を送出する
    - 429 レート制限 / 接続エラーは最大 max_retries 回リトライする
      （max_retries=3 の場合: 初回1回 + 最大3回リトライ = 合計最大4回試行）
    - max_retries=0 は「1回だけ試行してリトライなし」として動作する
    """
    if max_retries < 0:
        raise ValueError(f"max_retries は 0 以上である必要があります: {max_retries}")

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except GarminConnectAuthenticationError as exc:
            raise GarminAuthError(
                f"Garmin Connect 認証エラー: {exc}"
            ) from exc
        except (GarminConnectTooManyRequestsError, GarminConnectConnectionError) as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Garmin API 失敗 (attempt %d/%d): %s — %.1f秒後にリトライ",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
            else:
                raise GarminError(
                    f"Garmin API が {max_retries + 1} 回失敗しました"
                ) from last_exc
    raise GarminError("リトライ上限に達しました")  # pragma: no cover


class GarminClient:
    """Garmin Connect クライアント.

    使用方法::

        secrets = get_secrets()
        load_garmin_tokens(secrets)          # トークンを /tmp に展開
        client = GarminClient(
            secrets.garmin_email,
            secrets.garmin_password,
        )
        # ... 各種操作 ...
        save_garmin_tokens()                 # トークンを Secrets Manager に保存
        cleanup_token_dir()                  # /tmp クリーンアップ
    """

    def __init__(
        self,
        email: str,
        password: str,
        token_dir: Path = GARMIN_TOKEN_DIR,
    ) -> None:
        self._client = self._init_client(email, password, token_dir)

    # ─── 認証 ────────────────────────────────────────────────────────────────

    def _init_client(
        self,
        email: str,
        password: str,
        token_dir: Path,
    ) -> Garmin:
        """Garmin Connect クライアントを初期化する.

        トークンファイルが存在する場合はトークン復元を試みる。
        garminconnect の login() はトークン読み込み失敗時に自動的に
        クレデンシャルログインへフォールバックする。

        ログイン処理は接続エラー / 429 時に _call_with_retry() でリトライする。
        認証エラーはリトライしない。

        prompt_mfa=None を明示的に指定することで、Lambda 環境で MFA プロンプトが
        発生した場合に対話的入力待ちでハングすることを防ぐ。

        Raises:
            GarminAuthError: 認証失敗（メールアドレス / パスワード誤り・MFA 要求等）
            GarminError: ログイン時のネットワーク障害等
        """
        # prompt_mfa=None を明示: Lambda 等の非対話環境で MFA 要求時にハングしない
        garmin = Garmin(email=email, password=password, prompt_mfa=None)

        # トークンディレクトリが存在する場合はトークン復元を試みる
        # any() はジェネレータを短絡評価するため大量ファイルがある場合も効率的
        use_tokenstore = token_dir.exists() and any(token_dir.glob("*.json"))

        def _do_login() -> None:
            garmin.login(tokenstore=str(token_dir) if use_tokenstore else None)

        try:
            _call_with_retry(_do_login)
        except GarminAuthError as exc:
            raise GarminAuthError(
                "Garmin Connect のログインに失敗しました"
                "（メールアドレスまたはパスワードを確認してください）"
            ) from exc
        except GarminError as exc:
            raise GarminError(
                f"Garmin Connect のログイン中にネットワークエラーが発生しました: {exc}"
            ) from exc

        if use_tokenstore:
            logger.info("Garmin Connect: トークンから認証しました")
        else:
            logger.info("Garmin Connect: パスワードでログインしました")

        return garmin

    # ─── 体重・体脂肪率登録 ─────────────────────────────────────────────────

    def add_body_composition(self, body: BodyComposition) -> None:
        """体重・体脂肪率を Garmin Connect に登録する.

        タイムスタンプは JST（Asia/Tokyo）基準の ISO 8601 形式（+09:00 オフセット付き）
        で送信する。naive datetime だと UTC として解釈され日付が前日にずれる可能性がある。

        Args:
            body: 登録する体組成データ

        Raises:
            GarminAuthError: 認証エラー
            GarminError: 登録失敗（リトライ上限超過）
        """
        # JST オフセット付き ISO 8601: "2026-04-13T00:00:00+09:00"
        timestamp = datetime(
            body.date.year, body.date.month, body.date.day, 0, 0, 0, tzinfo=_JST
        ).isoformat()

        _call_with_retry(
            self._client.add_body_composition,
            timestamp,
            body.weight_kg,
            body.body_fat_percent,
        )
        logger.info(
            "Garmin Connect に体重・体脂肪率を登録しました: %s weight=%.1fkg fat=%s",
            body.date,
            body.weight_kg,
            f"{body.body_fat_percent:.1f}%"
            if body.body_fat_percent is not None
            else "未記録",
        )

    # ─── アクティビティカロリー取得 ─────────────────────────────────────────

    def get_activity_calories(self, target_date: date) -> ActivityCalories:
        """指定日のアクティビティ消費カロリーを取得する.

        get_stats() の activeKilocalories を使用する。
        - activeKilocalories: 純粋な活動・運動による消費カロリー
        - totalKilocalories: 安静時代謝 + 活動カロリーの合計（あすけんには不適）

        activeKilocalories が存在しない / None / 負値の場合は 0 を返す。
        （センサー未装着日・Garmin が稀に返す負値への対策）

        Args:
            target_date: 取得対象日

        Returns:
            ActivityCalories: 対象日のアクティビティ消費カロリー（0以上）

        Raises:
            GarminAuthError: 認証エラー
            GarminError: 取得失敗（リトライ上限超過）
        """
        cdate = target_date.isoformat()
        stats: dict[str, Any] = _call_with_retry(
            self._client.get_stats,
            cdate,
        )

        active_kcal = stats.get("activeKilocalories")
        if active_kcal is None:
            logger.warning(
                "Garmin Stats に activeKilocalories が存在しません: %s (keys=%s)",
                target_date,
                list(stats.keys()),
            )
            calories = 0
        else:
            # 負値は 0 に丸める（Garmin が稀に負値を返すことがある）
            calories = max(0, int(active_kcal))

        logger.debug(
            "Garmin アクティビティカロリー取得: %s %dkcal", target_date, calories
        )
        return ActivityCalories(date=target_date, calories_burned=calories)
