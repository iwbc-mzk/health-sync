"""config モジュールのユニットテスト (moto で Secrets Manager をモック)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import asken_garmin_sync.config as config_module
from asken_garmin_sync.config import (
    Secrets,
    _validate_garmin_tokens,
    cleanup_token_dir,
    get_secrets,
    load_garmin_tokens,
    save_garmin_tokens,
)

# ─── テスト用定数 ──────────────────────────────────────────────────────────────

_SECRET_NAME = "test-asken-garmin-sync"
_REGION = "ap-northeast-1"

_BASE_SECRET: dict[str, Any] = {
    "asken_email": "asken@example.com",
    "asken_password": "asken_pw",
    "garmin_email": "garmin@example.com",
    "garmin_password": "garmin_pw",
}

_TOKEN_CONTENT: dict[str, Any] = {"access_token": "tok_abc", "expires_in": 3600}


# ─── フィクスチャ ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """moto に必要な偽 AWS 認証情報と region を設定する."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)


@pytest.fixture(autouse=True)
def reset_secrets_client():
    """_secrets_client_instance シングルトンをリセットする.

    moto のモックコンテキスト内で boto3 クライアントを新規生成させるため、
    各テスト前後でシングルトンを None にリセットする。
    """
    config_module._secrets_client_instance = None
    yield
    config_module._secrets_client_instance = None


@pytest.fixture
def sm_client():
    """moto でモックされた Secrets Manager クライアントを返すフィクスチャ.

    このフィクスチャが `with mock_aws()` コンテキストを保持する。
    テストメソッド内で追加の `with mock_aws():` を使用してはならない。
    """
    with mock_aws():
        yield boto3.client("secretsmanager", region_name=_REGION)


@pytest.fixture
def secret(sm_client):
    """基本シークレットを作成して secret_name を返す."""
    sm_client.create_secret(
        Name=_SECRET_NAME,
        SecretString=json.dumps(_BASE_SECRET),
    )
    return _SECRET_NAME


@pytest.fixture
def secret_with_tokens(sm_client):
    """garmin_tokens 付きシークレットを作成して secret_name を返す."""
    payload = {
        **_BASE_SECRET,
        "garmin_tokens": {"oauth2_token.json": _TOKEN_CONTENT},
    }
    sm_client.create_secret(
        Name=_SECRET_NAME,
        SecretString=json.dumps(payload),
    )
    return _SECRET_NAME


# ─── _validate_garmin_tokens ─────────────────────────────────────────────────


class TestValidateGarminTokens:
    def test_valid_tokens(self):
        tokens = {"oauth2_token.json": {"access_token": "tok"}}
        result = _validate_garmin_tokens(tokens)
        assert result == tokens

    def test_not_a_dict_raises(self):
        with pytest.raises(ValueError, match="JSON オブジェクト"):
            _validate_garmin_tokens(["list", "instead"])

    def test_value_not_dict_raises(self):
        with pytest.raises(ValueError, match="JSON オブジェクト"):
            _validate_garmin_tokens({"file.json": "string_not_dict"})

    def test_value_empty_dict_raises(self):
        with pytest.raises(ValueError, match="空のオブジェクト"):
            _validate_garmin_tokens({"file.json": {}})

    def test_multiple_valid_files(self):
        tokens = {
            "oauth1_token.json": {"key": "v1"},
            "oauth2_token.json": {"key": "v2"},
        }
        result = _validate_garmin_tokens(tokens)
        assert len(result) == 2

    def test_empty_dict_returns_empty(self):
        """空の外側 dict は有効 — 空 dict をそのまま返す."""
        result = _validate_garmin_tokens({})
        assert result == {}


# ─── get_secrets ─────────────────────────────────────────────────────────────


class TestGetSecrets:
    def test_returns_secrets_with_required_fields(self, sm_client, secret):
        result = get_secrets(secret)

        assert result.asken_email == "asken@example.com"
        assert result.asken_password == "asken_pw"
        assert result.garmin_email == "garmin@example.com"
        assert result.garmin_password == "garmin_pw"
        assert result.garmin_tokens is None

    def test_returns_garmin_tokens_when_present(self, sm_client, secret_with_tokens):
        result = get_secrets(secret_with_tokens)

        assert result.garmin_tokens is not None
        assert "oauth2_token.json" in result.garmin_tokens
        assert result.garmin_tokens["oauth2_token.json"] == _TOKEN_CONTENT

    def test_garmin_tokens_as_json_string_is_parsed(self, sm_client):
        """garmin_tokens が JSON 文字列として保存されている場合もパースする."""
        tokens_dict = {"oauth2_token.json": _TOKEN_CONTENT}
        payload = {
            **_BASE_SECRET,
            "garmin_tokens": json.dumps(tokens_dict),  # 二重エンコード
        }
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(payload)
        )
        result = get_secrets(_SECRET_NAME)

        assert result.garmin_tokens is not None
        assert result.garmin_tokens["oauth2_token.json"] == _TOKEN_CONTENT

    def test_missing_required_keys_raises(self, sm_client):
        sm_client.create_secret(
            Name=_SECRET_NAME,
            SecretString=json.dumps({"asken_email": "only@example.com"}),
        )
        with pytest.raises(ValueError, match="必須キー"):
            get_secrets(_SECRET_NAME)

    def test_invalid_json_raises(self, sm_client):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString="not-valid-json"
        )
        with pytest.raises(ValueError, match="JSON 解析"):
            get_secrets(_SECRET_NAME)

    def test_secret_not_dict_raises(self, sm_client):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(["list", "not", "dict"])
        )
        with pytest.raises(ValueError, match="形式が不正"):
            get_secrets(_SECRET_NAME)

    def test_garmin_tokens_invalid_structure_raises(self, sm_client):
        """garmin_tokens の値が dict でない場合は ValueError."""
        payload = {**_BASE_SECRET, "garmin_tokens": {"file.json": "not_a_dict"}}
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(payload)
        )
        with pytest.raises(ValueError):
            get_secrets(_SECRET_NAME)

    def test_garmin_tokens_invalid_json_string_raises(self, sm_client):
        """garmin_tokens が文字列だが不正 JSON の場合は ValueError."""
        payload = {**_BASE_SECRET, "garmin_tokens": "not-json"}
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(payload)
        )
        with pytest.raises(ValueError, match="garmin_tokens の JSON 解析"):
            get_secrets(_SECRET_NAME)

    def test_garmin_tokens_empty_string_treated_as_none(self, sm_client):
        """garmin_tokens が空文字列の場合は None として扱い、エラーにならない.

        Secrets Manager 初期作成時に garmin_tokens を "" で初期化するケースに対応。
        """
        payload = {**_BASE_SECRET, "garmin_tokens": ""}
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(payload)
        )
        result = get_secrets(_SECRET_NAME)

        assert result.garmin_tokens is None

    def test_uses_default_secret_name_from_env(self, sm_client, monkeypatch):
        """SECRET_NAME 環境変数のデフォルト値を使用する."""
        monkeypatch.setenv("SECRET_NAME", _SECRET_NAME)
        sm_client.create_secret(
            Name=_SECRET_NAME,
            SecretString=json.dumps(_BASE_SECRET),
        )
        result = get_secrets()  # secret_name=None → 環境変数から読み取る

        assert result.asken_email == "asken@example.com"

    def test_uses_fallback_default_secret_name(self, sm_client, monkeypatch):
        """SECRET_NAME 未設定時は 'asken-garmin-sync' をデフォルト名に使用する."""
        monkeypatch.delenv("SECRET_NAME", raising=False)
        sm_client.create_secret(
            Name="asken-garmin-sync",
            SecretString=json.dumps(_BASE_SECRET),
        )
        result = get_secrets()

        assert result.asken_email == "asken@example.com"

    def test_repr_does_not_include_password(self, sm_client, secret):
        result = get_secrets(secret)

        r = repr(result)
        assert "asken_pw" not in r
        assert "garmin_pw" not in r
        assert "asken@example.com" in r

    def test_raises_client_error_when_secret_not_found(self, sm_client):
        """存在しないシークレット名を渡すと ClientError (ResourceNotFoundException) が伝播する."""
        with pytest.raises(ClientError) as exc_info:
            get_secrets("nonexistent-secret-xyz")

        assert exc_info.value.response["Error"]["Code"] == "ResourceNotFoundException"


# ─── load_garmin_tokens ──────────────────────────────────────────────────────


class TestLoadGarminTokens:
    def _make_secrets(self, garmin_tokens=None) -> Secrets:
        return Secrets(
            asken_email="a@example.com",
            asken_password="pw",
            garmin_email="g@example.com",
            garmin_password="gw",
            garmin_tokens=garmin_tokens,
        )

    def test_writes_token_files(self, tmp_path):
        tokens = {"oauth2_token.json": _TOKEN_CONTENT}
        secrets = self._make_secrets(garmin_tokens=tokens)
        token_dir = tmp_path / ".garminconnect"

        result = load_garmin_tokens(secrets, token_dir=token_dir)

        assert result is True
        assert token_dir.exists()
        written = token_dir / "oauth2_token.json"
        assert written.exists()
        assert json.loads(written.read_text(encoding="utf-8")) == _TOKEN_CONTENT

    def test_writes_multiple_token_files(self, tmp_path):
        tokens = {
            "oauth1_token.json": {"key1": "v1"},
            "oauth2_token.json": {"key2": "v2"},
        }
        secrets = self._make_secrets(garmin_tokens=tokens)
        token_dir = tmp_path / ".garminconnect"

        result = load_garmin_tokens(secrets, token_dir=token_dir)

        assert result is True
        assert (token_dir / "oauth1_token.json").exists()
        assert (token_dir / "oauth2_token.json").exists()

    def test_returns_false_when_no_garmin_tokens(self, tmp_path):
        secrets = self._make_secrets(garmin_tokens=None)
        token_dir = tmp_path / ".garminconnect"

        result = load_garmin_tokens(secrets, token_dir=token_dir)

        assert result is False
        assert not token_dir.exists()

    def test_returns_false_when_garmin_tokens_empty(self, tmp_path):
        secrets = self._make_secrets(garmin_tokens={})
        token_dir = tmp_path / ".garminconnect"

        result = load_garmin_tokens(secrets, token_dir=token_dir)

        assert result is False

    def test_skips_invalid_filename_path_traversal(self, tmp_path):
        """パストラバーサル攻撃を含むファイル名はパストラバーサルを防いで書き込む.

        `../evil.json` → Path().name → `evil.json` → 正規表現マッチ通過
        ファイルは token_dir 内の `evil.json` に書き込まれ、親ディレクトリには影響しない。
        """
        tokens = {
            "../evil.json": {"evil": "content"},
            "oauth2_token.json": _TOKEN_CONTENT,
        }
        secrets = self._make_secrets(garmin_tokens=tokens)
        token_dir = tmp_path / ".garminconnect"

        result = load_garmin_tokens(secrets, token_dir=token_dir)

        assert result is True
        # evil.json は token_dir 内に安全に書き込まれる（親ディレクトリには影響しない）
        assert not (tmp_path / "evil.json").exists()
        assert (token_dir / "evil.json").exists()  # 変換後のファイルは token_dir 内に存在する
        assert (token_dir / "oauth2_token.json").exists()

    def test_skips_filename_with_spaces(self, tmp_path):
        """スペースを含むファイル名は _TOKEN_FILENAME_RE でスキップする."""
        tokens = {
            "bad file.json": {"bad": True},
            "oauth2_token.json": _TOKEN_CONTENT,
        }
        secrets = self._make_secrets(garmin_tokens=tokens)
        token_dir = tmp_path / ".garminconnect"

        result = load_garmin_tokens(secrets, token_dir=token_dir)

        assert result is True
        assert not (token_dir / "bad file.json").exists()
        assert (token_dir / "oauth2_token.json").exists()

    def test_returns_false_when_all_filenames_invalid(self, tmp_path):
        """全ファイル名が _TOKEN_FILENAME_RE にマッチしない場合は False を返す.

        '!' 始まりのファイル名は Path().name 変換後もマッチしない。
        """
        tokens = {"!invalid.json": {"bad": True}}
        secrets = self._make_secrets(garmin_tokens=tokens)
        token_dir = tmp_path / ".garminconnect"

        result = load_garmin_tokens(secrets, token_dir=token_dir)

        assert result is False

    def test_creates_token_dir_if_not_exists(self, tmp_path):
        tokens = {"oauth2_token.json": _TOKEN_CONTENT}
        secrets = self._make_secrets(garmin_tokens=tokens)
        token_dir = tmp_path / "deep" / ".garminconnect"

        assert not token_dir.exists()
        load_garmin_tokens(secrets, token_dir=token_dir)

        assert token_dir.exists()


# ─── save_garmin_tokens ──────────────────────────────────────────────────────


class TestSaveGarminTokens:
    def _create_token_dir(self, tmp_path: Path, files: dict[str, Any]) -> Path:
        """テスト用のトークンディレクトリを作成してファイルを書き込む."""
        token_dir = tmp_path / ".garminconnect"
        token_dir.mkdir()
        for name, content in files.items():
            (token_dir / name).write_text(json.dumps(content), encoding="utf-8")
        return token_dir

    def test_saves_tokens_to_secrets_manager(self, sm_client, tmp_path):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = self._create_token_dir(
            tmp_path, {"oauth2_token.json": _TOKEN_CONTENT}
        )

        result = save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

        assert result is True

        # Secrets Manager に保存された内容を確認（同じモックコンテキスト内）
        updated = sm_client.get_secret_value(SecretId=_SECRET_NAME)
        saved: dict[str, Any] = json.loads(updated["SecretString"])
        assert "garmin_tokens" in saved
        assert saved["garmin_tokens"]["oauth2_token.json"] == _TOKEN_CONTENT

    def test_preserves_existing_required_keys(self, sm_client, tmp_path):
        """既存の必須キーが保存後も維持される."""
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = self._create_token_dir(
            tmp_path, {"oauth2_token.json": _TOKEN_CONTENT}
        )

        save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

        updated = sm_client.get_secret_value(SecretId=_SECRET_NAME)
        saved = json.loads(updated["SecretString"])
        assert saved["asken_email"] == "asken@example.com"
        assert saved["garmin_email"] == "garmin@example.com"

    def test_saves_multiple_token_files(self, sm_client, tmp_path):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = self._create_token_dir(
            tmp_path,
            {
                "oauth1_token.json": {"key": "v1"},
                "oauth2_token.json": {"key": "v2"},
            },
        )

        save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

        updated = sm_client.get_secret_value(SecretId=_SECRET_NAME)
        saved = json.loads(updated["SecretString"])
        assert "oauth1_token.json" in saved["garmin_tokens"]
        assert "oauth2_token.json" in saved["garmin_tokens"]

    def test_returns_false_when_token_dir_missing(self, sm_client, tmp_path):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        nonexistent = tmp_path / "no_dir"

        result = save_garmin_tokens(_SECRET_NAME, token_dir=nonexistent)

        assert result is False

    def test_returns_false_when_no_json_files(self, sm_client, tmp_path):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = tmp_path / ".garminconnect"
        token_dir.mkdir()
        (token_dir / "not_json.txt").write_text("irrelevant", encoding="utf-8")

        result = save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

        assert result is False

    def test_skips_invalid_filename_in_dir(self, sm_client, tmp_path):
        """スペースを含む .json ファイルは _TOKEN_FILENAME_RE でスキップされ garmin_tokens に含まれない."""
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = tmp_path / ".garminconnect"
        token_dir.mkdir()
        # 正常ファイル
        (token_dir / "oauth2_token.json").write_text(
            json.dumps(_TOKEN_CONTENT), encoding="utf-8"
        )
        # スペースを含む不正ファイル名 — glob("*.json") には引っかかるが regex でスキップされる
        (token_dir / "bad file.json").write_text(
            json.dumps({"should": "be_skipped"}), encoding="utf-8"
        )

        save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

        updated = sm_client.get_secret_value(SecretId=_SECRET_NAME)
        saved = json.loads(updated["SecretString"])
        assert "oauth2_token.json" in saved["garmin_tokens"]
        assert "bad file.json" not in saved["garmin_tokens"]

    def test_raises_on_invalid_json_in_token_file(self, sm_client, tmp_path):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = tmp_path / ".garminconnect"
        token_dir.mkdir()
        (token_dir / "oauth2_token.json").write_text("not-json", encoding="utf-8")

        with pytest.raises(ValueError, match="JSON解析"):
            save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

    def test_raises_when_token_file_not_dict(self, sm_client, tmp_path):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = tmp_path / ".garminconnect"
        token_dir.mkdir()
        (token_dir / "oauth2_token.json").write_text(
            json.dumps(["list"]), encoding="utf-8"
        )

        with pytest.raises(ValueError, match="形式が不正"):
            save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

    def test_raises_when_token_file_empty_dict(self, sm_client, tmp_path):
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = tmp_path / ".garminconnect"
        token_dir.mkdir()
        (token_dir / "oauth2_token.json").write_text("{}", encoding="utf-8")

        with pytest.raises(ValueError, match="空のオブジェクト"):
            save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

    def test_raises_when_required_keys_missing_in_secret(self, sm_client, tmp_path):
        """Secrets Manager のシークレットに必須キーがない場合は ValueError."""
        sm_client.create_secret(
            Name=_SECRET_NAME,
            SecretString=json.dumps({"only_garmin": True}),
        )
        token_dir = self._create_token_dir(
            tmp_path, {"oauth2_token.json": _TOKEN_CONTENT}
        )

        with pytest.raises(ValueError, match="必須キー"):
            save_garmin_tokens(_SECRET_NAME, token_dir=token_dir)

    def test_uses_env_secret_name_when_none(self, sm_client, tmp_path, monkeypatch):
        """secret_name=None 時は SECRET_NAME 環境変数を使用する."""
        monkeypatch.setenv("SECRET_NAME", _SECRET_NAME)
        sm_client.create_secret(
            Name=_SECRET_NAME, SecretString=json.dumps(_BASE_SECRET)
        )
        token_dir = self._create_token_dir(
            tmp_path, {"oauth2_token.json": _TOKEN_CONTENT}
        )

        result = save_garmin_tokens(None, token_dir=token_dir)

        assert result is True

    def test_raises_client_error_when_secret_not_found(self, sm_client, tmp_path):
        """存在しないシークレット名を渡すと ClientError (ResourceNotFoundException) が伝播する."""
        token_dir = self._create_token_dir(
            tmp_path, {"oauth2_token.json": _TOKEN_CONTENT}
        )

        with pytest.raises(ClientError) as exc_info:
            save_garmin_tokens("nonexistent-secret-xyz", token_dir=token_dir)

        assert exc_info.value.response["Error"]["Code"] == "ResourceNotFoundException"


# ─── cleanup_token_dir ────────────────────────────────────────────────────────


class TestCleanupTokenDir:
    def test_deletes_existing_dir(self, tmp_path):
        token_dir = tmp_path / ".garminconnect"
        token_dir.mkdir()
        (token_dir / "oauth2_token.json").write_text("{}", encoding="utf-8")

        cleanup_token_dir(token_dir=token_dir)

        assert not token_dir.exists()

    def test_noop_when_dir_missing(self, tmp_path):
        """ディレクトリが存在しない場合は何もしない（例外なし）."""
        nonexistent = tmp_path / "no_such_dir"

        cleanup_token_dir(token_dir=nonexistent)  # 例外が発生しないことを確認

        assert not nonexistent.exists()

    def test_deletes_nested_files(self, tmp_path):
        """ディレクトリ内のすべてのファイルが削除される."""
        token_dir = tmp_path / ".garminconnect"
        token_dir.mkdir()
        (token_dir / "a.json").write_text("{}", encoding="utf-8")
        (token_dir / "b.json").write_text("{}", encoding="utf-8")

        cleanup_token_dir(token_dir=token_dir)

        assert not token_dir.exists()
