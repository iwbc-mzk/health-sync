"""AWS Secrets Manager からの認証情報取得とGarminトークン永続化."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_secretsmanager import SecretsManagerClient

logger = logging.getLogger(__name__)

GARMIN_TOKEN_DIR: Path = Path("/tmp/.garminconnect")

# IMPORTANT: Lambda の同時実行数は必ず 1 に設定すること。
# save_garmin_tokens() は read-modify-write であり、AWS Secrets Manager には
# Compare-And-Swap API が存在しないため、並行実行によるトークン上書き破壊を
# API レベルでは防げない。同時実行数=1 が唯一の保証手段である。

# トークンファイル名として許容するパターン（パストラバーサル防止）
# ASCII 英数字・アンダースコア・ハイフンのみ許容（Unicode 英数字を意図的に除外）
_TOKEN_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*\.json$")

# boto3 クライアントの遅延初期化（モジュールレベル変数、ウォームスタート最適化）
_secrets_client_instance: SecretsManagerClient | None = None


def _secrets_client() -> SecretsManagerClient:
    """boto3 Secrets Manager クライアントを遅延初期化して返す.

    モジュール import 時の NoRegionError を防ぐため、初回呼び出し時に初期化する。
    Lambda のウォームスタートではキャッシュ済みのインスタンスを再利用する。
    """
    global _secrets_client_instance
    if _secrets_client_instance is None:
        _secrets_client_instance = boto3.client("secretsmanager")  # type: ignore[assignment]
    return _secrets_client_instance


class Secrets:
    """Secrets Manager から取得した認証情報."""

    def __init__(
        self,
        asken_email: str,
        asken_password: str,
        garmin_email: str,
        garmin_password: str,
        garmin_tokens: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.asken_email = asken_email
        self.asken_password = asken_password
        self.garmin_email = garmin_email
        self.garmin_password = garmin_password
        # garmin_tokens は {ファイル名: パース済みJSONオブジェクト} の辞書。
        # garminconnect ライブラリが使うトークンファイル群をすべて保持する。
        self.garmin_tokens = garmin_tokens

    def __repr__(self) -> str:
        # パスワード・トークンはログに出さない
        return f"Secrets(asken_email={self.asken_email!r}, garmin_email={self.garmin_email!r})"


def _validate_garmin_tokens(raw: Any) -> dict[str, dict[str, Any]]:
    """garmin_tokens 値を検証して型付き辞書として返す.

    各トークンファイルの値が非空の dict であることを確認する。

    Raises:
        ValueError: 構造が不正な場合
    """
    if not isinstance(raw, dict):
        raise ValueError("garmin_tokens は JSON オブジェクトである必要があります")
    validated: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"garmin_tokens のキーは文字列である必要があります: {key!r}")
        if not isinstance(value, dict):
            raise ValueError(f"garmin_tokens[{key!r}] は JSON オブジェクトである必要があります")
        if not value:
            raise ValueError(f"garmin_tokens[{key!r}] が空のオブジェクトです")
        validated[key] = value
    return validated


def get_secrets(secret_name: str | None = None) -> Secrets:
    """Secrets Manager からシークレットを取得して返す.

    Raises:
        ValueError: 必須キーが欠けているか形式が不正な場合（機密情報をメッセージに含まない）
    """
    name = secret_name or os.environ.get("SECRET_NAME", "asken-garmin-sync")
    response = _secrets_client().get_secret_value(SecretId=name)

    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str) or not secret_string:
        raise ValueError("SecretString が取得できません（バイナリシークレットは未サポート）")

    try:
        raw: Any = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise ValueError("シークレットの JSON 解析に失敗しました") from exc

    if not isinstance(raw, dict):
        raise ValueError("シークレットの形式が不正です（JSON オブジェクトを期待）")

    required_keys = ("asken_email", "asken_password", "garmin_email", "garmin_password")
    missing = [k for k in required_keys if k not in raw]
    if missing:
        raise ValueError(f"シークレットに必須キーが存在しません: {missing}")

    garmin_tokens: dict[str, dict[str, Any]] | None = None
    garmin_tokens_raw = raw.get("garmin_tokens")
    # None と空文字列 ("") は「トークン未設定」として扱い、スキップする。
    # create_secret.sh は null で初期化するが、Secrets Manager を手動編集して
    # "" を設定した場合にも安全に動作するよう明示的に除外する。
    if garmin_tokens_raw is not None and garmin_tokens_raw != "":
        if isinstance(garmin_tokens_raw, str):
            try:
                garmin_tokens_raw = json.loads(garmin_tokens_raw)
            except json.JSONDecodeError as exc:
                raise ValueError("garmin_tokens の JSON 解析に失敗しました") from exc
        garmin_tokens = _validate_garmin_tokens(garmin_tokens_raw)

    return Secrets(
        asken_email=str(raw["asken_email"]),
        asken_password=str(raw["asken_password"]),
        garmin_email=str(raw["garmin_email"]),
        garmin_password=str(raw["garmin_password"]),
        garmin_tokens=garmin_tokens,
    )


def load_garmin_tokens(secrets: Secrets, token_dir: Path = GARMIN_TOKEN_DIR) -> bool:
    """Secrets Manager のトークンを token_dir 配下のファイルに書き出す.

    garmin_tokens は {ファイル名: JSONオブジェクト} の辞書。
    garminconnect が使うファイル（oauth1_token.json / oauth2_token.json 等）を
    すべて復元する。

    Returns:
        True: トークンファイルを書き出した
        False: トークンが存在しないためスキップ
    """
    if not secrets.garmin_tokens:
        logger.debug("garmin_tokens が未設定のためスキップ")
        return False

    token_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for filename, content in secrets.garmin_tokens.items():
        # パストラバーサル防止: ファイル名を正規表現で検証（ASCII 限定）
        safe_name = Path(filename).name
        if not _TOKEN_FILENAME_RE.match(safe_name):
            logger.warning("不正なトークンファイル名をスキップします: %r", filename)
            continue
        token_file = token_dir / safe_name
        try:
            token_file.write_text(json.dumps(content), encoding="utf-8")
        except OSError:
            logger.error("トークンファイルの書き込みに失敗しました: %s", safe_name, exc_info=True)
            raise
        logger.debug("Garmin トークンファイルを書き出しました: %s", safe_name)
        written += 1

    if written == 0:
        logger.warning("有効なトークンファイルが1件も書き出されませんでした")
        return False

    return True


def save_garmin_tokens(secret_name: str | None = None, token_dir: Path = GARMIN_TOKEN_DIR) -> bool:
    """トークンディレクトリ内の全 JSON ファイルを読み取り Secrets Manager に保存する.

    トークンは {ファイル名: パース済みJSONオブジェクト} の辞書として保存する。
    ClientRequestToken にランダム UUID を渡してリトライ時の冪等性を確保する。

    NOTE: 並行実行保護は Lambda の同時実行数=1 による。
          AWS Secrets Manager には CAS API が存在しないため、
          同時実行数=1 の設定が唯一の並行書き込み防止手段である。
    NOTE: cleanup_token_dir() より前に呼び出すこと。

    Returns:
        True: 保存成功
        False: トークンファイルが存在しないためスキップ

    Raises:
        ValueError: トークンファイルが不正な形式の場合
    """
    if not token_dir.exists():
        logger.debug("トークンディレクトリが存在しないためスキップ: %s", token_dir)
        return False

    token_files = sorted(token_dir.glob("*.json"))
    if not token_files:
        logger.debug("トークンファイルが存在しないためスキップ")
        return False

    tokens: dict[str, dict[str, Any]] = {}
    for token_file in token_files:
        # パストラバーサル防止・不正ファイル混入防止: ファイル名を正規表現で検証（ASCII 限定）
        if not _TOKEN_FILENAME_RE.match(token_file.name):
            logger.warning("不正なトークンファイル名をスキップします: %r", token_file.name)
            continue

        raw_text = token_file.read_text(encoding="utf-8")
        try:
            parsed: Any = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"トークンファイルのJSON解析に失敗しました: {token_file.name}") from exc

        if not isinstance(parsed, dict):
            raise ValueError(f"トークンファイルの形式が不正です: {token_file.name}")
        if not parsed:
            raise ValueError(f"トークンファイルが空のオブジェクトです: {token_file.name}")

        tokens[token_file.name] = parsed

    if not tokens:
        logger.warning("有効なトークンファイルが存在しないためスキップします（全ファイルが検証で除外されました）")
        return False

    name = secret_name or os.environ.get("SECRET_NAME", "asken-garmin-sync")
    response = _secrets_client().get_secret_value(SecretId=name)

    # VersionId をログに記録することで並行書き込みの検出を補助する
    # （AWS Secrets Manager には CAS API がないため、同時実行数=1 が唯一の保護手段）
    version_id = response.get("VersionId", "unknown")
    logger.debug("シークレット取得: VersionId=%s", version_id)

    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str) or not secret_string:
        raise ValueError("SecretString が取得できません")

    try:
        current: Any = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise ValueError("シークレットの JSON 解析に失敗しました") from exc

    if not isinstance(current, dict):
        raise ValueError("シークレットの形式が不正です")

    current["garmin_tokens"] = tokens

    # 書き込み前に必須キーが残っていることを確認する（不意のキー消失を防ぐ）
    required_keys = ("asken_email", "asken_password", "garmin_email", "garmin_password")
    missing = [k for k in required_keys if k not in current]
    if missing:
        raise ValueError(f"書き込み前チェック: シークレットに必須キーが存在しません: {missing}")

    # ClientRequestToken は意図的に省略する。
    # ランダム UUID を渡すと毎回異なるバージョンが生成されて冪等性が機能せず、
    # バージョン上限（100）に早期到達するリスクがある。
    # 並行書き込み保護は Lambda の同時実行数=1 設定に委ねる。
    _secrets_client().put_secret_value(
        SecretId=name,
        SecretString=json.dumps(current),
    )
    logger.debug("Garmin トークンを Secrets Manager に保存しました（%d ファイル）", len(tokens))
    return True


def cleanup_token_dir(token_dir: Path = GARMIN_TOKEN_DIR) -> None:
    """トークンディレクトリを削除する.

    NOTE: save_garmin_tokens() を呼び出した後に実行すること。
    Lambda のウォームスタートで古いトークンが残留するのを防ぐ。
    handler の finally ブロックで呼び出すことを推奨。
    """
    if token_dir.exists():
        try:
            shutil.rmtree(token_dir)
            logger.debug("トークンディレクトリを削除しました: %s", token_dir)
        except OSError:
            logger.warning("トークンディレクトリの削除に失敗しました: %s", token_dir, exc_info=True)
