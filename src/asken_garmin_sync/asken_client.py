"""あすけんクライアント - 体重取得・運動カロリー登録."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from utils.asken_base_client import (
    _BASE_URL,
    _HEADERS,
    AskenAuthError,
    AskenBaseClient,
    AskenError,
    request_with_retry,
)

from .models import BodyComposition

logger = logging.getLogger(__name__)

_COMMENT_URL = f"{_BASE_URL}/wsp/comment/{{date}}"
_EXERCISE_URL = f"{_BASE_URL}/wsp/exercise/{{date}}"
_EXERCISE_ADD_URL = f"{_BASE_URL}/exercise/add/{{exercise_id}}"
_EXERCISE_DELETE_URL = f"{_BASE_URL}/exercise/delete_v2/{{item_type}}/{{authcode}}"

# 運動登録のデフォルト設定
DEFAULT_EXERCISE_ID: int = 1061  # ジム・フィットネスクラブでの運動
DEFAULT_CAL_PER_MIN: float = 5.8  # kcal/分

# re-export for backward compatibility with existing tests / callers
__all__ = ["AskenAuthError", "AskenError", "AskenClient"]


class AskenClient(AskenBaseClient):
    """あすけんスクレイピングクライアント（体重取得・運動カロリー登録）."""

    # ─── 体重・体脂肪率取得 ──────────────────────────────────────────────────

    def get_body_composition(self, target_date: date) -> BodyComposition | None:
        """コメントページから体重・体脂肪率を取得する.

        Returns:
            BodyComposition: 体重が記録されている場合
            None: 体重が未記録の場合
        Raises:
            AskenError: ページ取得またはパース失敗
        """
        url = _COMMENT_URL.format(date=target_date.isoformat())
        resp = request_with_retry(
            self._session.get, url, headers=_HEADERS, timeout=30
        )

        soup = BeautifulSoup(resp.text, "lxml")

        weight_input = soup.find("input", {"name": "data[Body][weight]"})
        fat_input = soup.find("input", {"name": "data[Body][body_fat]"})

        if weight_input is None:
            logger.warning("体重入力フィールドが見つかりません: %s", target_date)
            return None

        weight_raw = weight_input.get("value")
        weight_str = str(weight_raw).strip() if weight_raw is not None else ""

        if not weight_str:
            logger.debug("体重未記録: %s", target_date)
            return None

        try:
            weight_kg = float(weight_str)
        except ValueError as exc:
            raise AskenError(f"体重の解析に失敗しました: {weight_str!r}") from exc

        body_fat: float | None = None
        if fat_input is not None:
            fat_raw = fat_input.get("value")
            fat_str = str(fat_raw).strip() if fat_raw is not None else ""
            if fat_str:
                try:
                    body_fat = float(fat_str)
                except ValueError:
                    logger.warning("体脂肪率の解析に失敗しました: %r（スキップ）", fat_str)

        logger.debug(
            "体重・体脂肪率取得: %s weight=%.1f fat=%s",
            target_date,
            weight_kg,
            f"{body_fat:.1f}%" if body_fat is not None else "未記録",
        )
        return BodyComposition(
            date=target_date,
            weight_kg=weight_kg,
            body_fat_percent=body_fat,
        )

    # ─── 運動カロリー登録 ────────────────────────────────────────────────────

    _EXE_DATAS_RE = re.compile(
        r"WspExerciseV2\.exeDatas\s*=\s*(\{.*?\});",
        re.DOTALL,
    )

    def _get_exercise_entries(self, target_date: date) -> list[tuple[str, str, str]]:
        """運動ページから既存エントリの (item_type, authcode, code) リストを取得する.

        運動リストは JavaScript の WspExerciseV2.exeDatas に JSON として埋め込まれており、
        view_list() で動的レンダリングされる。BeautifulSoup で script タグを探し、
        JSON を抽出して menus 配列の item_type / authcode / code を返す。
        code は運動カタログ ID で、スクリプト登録エントリと手動登録エントリの判別に使用する。
        """
        url = _EXERCISE_URL.format(date=target_date.isoformat())
        resp = request_with_retry(
            self._session.get, url, headers=_HEADERS, timeout=30
        )
        soup = BeautifulSoup(resp.text, "lxml")

        entries: list[tuple[str, str, str]] = []
        for script in soup.find_all("script"):
            script_text = script.string or ""
            match = self._EXE_DATAS_RE.search(script_text)
            if not match:
                continue
            try:
                data: dict[str, Any] = json.loads(match.group(1))
            except json.JSONDecodeError:
                logger.warning("WspExerciseV2.exeDatas の JSON パースに失敗しました")
                break
            for menu in data.get("menus", []):
                item_type = str(menu.get("item_type", ""))
                authcode = str(menu.get("authcode", ""))
                code = str(menu.get("code", ""))
                if item_type and authcode:
                    entries.append((item_type, authcode, code))
            break

        logger.debug("既存の運動エントリ %d 件を検出: %s", len(entries), target_date)
        return entries

    def _delete_exercise_entry(
        self, target_date: date, item_type: str, authcode: str
    ) -> None:
        """運動エントリを削除する."""
        url = _EXERCISE_DELETE_URL.format(item_type=item_type, authcode=authcode)
        # 削除 API は JSON レスポンスの契約がない（空ボディや HTML を返すことがある）。
        # request_with_retry 内の raise_for_status() で HTTP エラーは検出済みのため、
        # HTTP 200 を成功とみなす。
        request_with_retry(
            self._session.get,
            url,
            params={"record_date": target_date.isoformat()},
            headers=_HEADERS,
            timeout=30,
        )
        logger.debug("運動エントリを削除しました: %s/%s", item_type, authcode)

    def _add_exercise_entry(
        self, target_date: date, exercise_id: int, amount: int
    ) -> None:
        """運動エントリを追加する.

        Args:
            exercise_id: あすけん運動カタログ ID
            amount: 運動時間（分）
        """
        url = _EXERCISE_ADD_URL.format(exercise_id=exercise_id)
        resp = request_with_retry(
            self._session.post,
            url,
            params={"record_date": target_date.isoformat()},
            data={"amount": amount},
            headers=_HEADERS,
            timeout=30,
        )
        try:
            data: dict[str, Any] = resp.json()
        except json.JSONDecodeError as exc:
            raise AskenError("運動登録 API のレスポンスが JSON ではありません") from exc

        if data.get("result") != "OK":
            raise AskenError(f"運動登録に失敗しました: {data}")

        logger.debug(
            "運動エントリを追加しました: exercise_id=%d amount=%d分", exercise_id, amount
        )

    def register_activity_calories(
        self,
        target_date: date,
        calories: int,
        exercise_id: int = DEFAULT_EXERCISE_ID,
        cal_per_min: float = DEFAULT_CAL_PER_MIN,
    ) -> None:
        """Garmin のアクティビティカロリーをあすけん運動ページに登録する（上書き対応）.

        exercise_id が一致するスクリプト登録エントリのみ削除してから新しいエントリを追加する。
        手動で追加された運動エントリ（異なる exercise_id を持つもの）は保持する。

        Args:
            target_date: 対象日
            calories: 登録するカロリー（kcal）
            exercise_id: あすけん運動カタログ ID
            cal_per_min: 選択した運動の消費カロリー/分

        Raises:
            AskenError: 登録失敗
        """
        if calories <= 0:
            logger.info("カロリーが 0 以下のため運動登録をスキップ: %s", target_date)
            return

        entries = self._get_exercise_entries(target_date)
        for it, ac, code in entries:
            if not code:
                logger.warning(
                    "code フィールドが空の運動エントリをスキップしました（手動エントリとして保持）: "
                    "item_type=%s authcode=%s date=%s",
                    it,
                    ac,
                    target_date,
                )
        script_entries = [
            (it, ac) for it, ac, code in entries if code == str(exercise_id)
        ]
        manual_count = len(entries) - len(script_entries)
        if manual_count > 0:
            logger.info(
                "手動追加の運動エントリ %d 件をスキップしました（保持）: %s",
                manual_count,
                target_date,
            )
        for item_type, authcode in script_entries:
            self._delete_exercise_entry(target_date, item_type, authcode)
            time.sleep(0.3)  # 連続削除のレート制限対策

        if script_entries:
            logger.debug("%d 件のスクリプト登録運動エントリを削除しました", len(script_entries))

        # カロリーから運動時間を算出（5分単位、四捨五入、最小5分）
        # Python の round() は銀行家の丸めを使うため int(x + 0.5) で明示的に四捨五入する
        raw_minutes = calories / cal_per_min
        amount = max(5, int(raw_minutes / 5 + 0.5) * 5)
        logger.debug(
            "運動時間算出: %dkcal ÷ %.1fkcal/分 → %d分（5分単位）",
            calories,
            cal_per_min,
            amount,
        )

        self._add_exercise_entry(target_date, exercise_id, amount)
        logger.info(
            "あすけんに運動を登録しました: %s %dkcal → exercise_id=%d %d分",
            target_date,
            calories,
            exercise_id,
            amount,
        )
