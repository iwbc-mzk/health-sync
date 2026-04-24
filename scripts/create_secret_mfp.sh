#!/usr/bin/env bash
# create_secret_mfp.sh — asken-myfitnesspal-sync Secrets Manager シークレット作成スクリプト
#
# 使用方法:
#   ./scripts/create_secret_mfp.sh [オプション]
#
# オプション:
#   -r, --region    AWSリージョン (デフォルト: ap-northeast-1)
#   -p, --profile   AWS CLIプロファイル (デフォルト: default)
#   -s, --secret    シークレット名 (デフォルト: asken-myfitnesspal-sync)
#   -h, --help      ヘルプを表示
#
# 説明:
#   このスクリプトは Secrets Manager にシークレットを作成します。
#   各認証情報はプロンプトで対話的に入力します（履歴に残りません）。
#   既存シークレットがある場合は値を更新します。

set -euo pipefail

# ─── デフォルト値 ─────────────────────────────────────────────────────────────
REGION="ap-northeast-1"
PROFILE="default"
SECRET_NAME="asken-myfitnesspal-sync"

# ─── カラー出力 ───────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR]${NC}  $*" >&2; }

# ─── 引数パース ───────────────────────────────────────────────────────────────
usage() {
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r|--region)  REGION="$2";      shift 2 ;;
    -p|--profile) PROFILE="$2";     shift 2 ;;
    -s|--secret)  SECRET_NAME="$2"; shift 2 ;;
    -h|--help)    usage ;;
    *) error "不明なオプション: $1"; exit 1 ;;
  esac
done

# ─── 前提条件チェック ─────────────────────────────────────────────────────────
check_prerequisites() {
  if ! command -v aws &>/dev/null; then
    error "AWS CLI が見つかりません。"
    exit 1
  fi
  if ! command -v python3 &>/dev/null; then
    error "Python3 が見つかりません。"
    exit 1
  fi
  if ! aws sts get-caller-identity --profile "$PROFILE" --region "$REGION" &>/dev/null; then
    error "AWS 認証情報が有効ではありません。aws configure --profile $PROFILE で設定してください。"
    exit 1
  fi
}

# ─── 認証情報の対話入力 ───────────────────────────────────────────────────────
read_credentials() {
  echo ""
  warn "認証情報を入力してください（入力内容はシェル履歴に残りません）"
  echo ""

  read -r -p "あすけん メールアドレス: " ASKEN_EMAIL
  read -r -s -p "あすけん パスワード: " ASKEN_PASSWORD
  echo ""
  read -r -p "MyFitnessPal メールアドレス: " MFP_EMAIL
  read -r -s -p "MyFitnessPal パスワード: " MFP_PASSWORD
  echo ""

  if [[ -z "$ASKEN_EMAIL" || -z "$ASKEN_PASSWORD" || -z "$MFP_EMAIL" || -z "$MFP_PASSWORD" ]]; then
    error "すべての認証情報を入力してください。"
    exit 1
  fi
}

# ─── シークレット作成/更新 ────────────────────────────────────────────────────
create_or_update_secret() {
  info "シークレット内容を構築しています..."

  local secret_json
  secret_json=$(ASKEN_EMAIL="$ASKEN_EMAIL" \
    ASKEN_PASSWORD="$ASKEN_PASSWORD" \
    MFP_EMAIL="$MFP_EMAIL" \
    MFP_PASSWORD="$MFP_PASSWORD" \
    python3 -c '
import json, os
print(json.dumps({
    "asken_email":           os.environ["ASKEN_EMAIL"],
    "asken_password":        os.environ["ASKEN_PASSWORD"],
    "myfitnesspal_email":    os.environ["MFP_EMAIL"],
    "myfitnesspal_password": os.environ["MFP_PASSWORD"],
}))
')

  # 既存シークレット確認
  local secret_exists=false
  local deletion_date="None"
  if aws secretsmanager describe-secret \
      --secret-id "$SECRET_NAME" \
      --profile "$PROFILE" \
      --region "$REGION" &>/dev/null; then
    secret_exists=true
    if ! deletion_date=$(aws secretsmanager describe-secret \
        --secret-id "$SECRET_NAME" \
        --profile "$PROFILE" \
        --region "$REGION" \
        --query DeletedDate \
        --output text); then
      error "シークレットの削除状態の確認中にエラーが発生しました。"
      exit 1
    fi
  fi

  if [[ "$secret_exists" == true ]]; then
    # 削除予定状態の場合は復元してから更新する
    if [[ -n "$deletion_date" && "$deletion_date" != "None" ]]; then
      warn "シークレット '${SECRET_NAME}' は削除予定状態です。復元します。"
      aws secretsmanager restore-secret \
        --secret-id "$SECRET_NAME" \
        --profile "$PROFILE" \
        --region "$REGION" >/dev/null
      success "シークレットを復元しました。"
    fi

    warn "既存のシークレット '${SECRET_NAME}' が見つかりました。値を更新します。"
    aws secretsmanager put-secret-value \
      --secret-id "$SECRET_NAME" \
      --secret-string "$secret_json" \
      --profile "$PROFILE" \
      --region "$REGION" \
      --output text >/dev/null
    success "シークレット '${SECRET_NAME}' を更新しました。"
  else
    info "新規シークレット '${SECRET_NAME}' を作成します..."
    aws secretsmanager create-secret \
      --name "$SECRET_NAME" \
      --description "asken-myfitnesspal-sync 認証情報" \
      --secret-string "$secret_json" \
      --profile "$PROFILE" \
      --region "$REGION" \
      --output text >/dev/null
    success "シークレット '${SECRET_NAME}' を作成しました。"
  fi

  # ARN 表示
  local secret_arn
  secret_arn=$(aws secretsmanager describe-secret \
    --secret-id "$SECRET_NAME" \
    --profile "$PROFILE" \
    --region "$REGION" \
    --query ARN \
    --output text)
  info "シークレット ARN: ${secret_arn}"
}

# ─── メイン ───────────────────────────────────────────────────────────────────
main() {
  echo ""
  echo "=== Secrets Manager シークレット作成 (asken-myfitnesspal-sync) ==="
  echo "  リージョン : ${REGION}"
  echo "  プロファイル: ${PROFILE}"
  echo "  シークレット: ${SECRET_NAME}"
  echo ""
  echo "  シークレット構造:"
  echo "    asken_email           : あすけんログイン用メールアドレス"
  echo "    asken_password        : あすけんログイン用パスワード"
  echo "    myfitnesspal_email    : MyFitnessPal ログイン用メールアドレス"
  echo "    myfitnesspal_password : MyFitnessPal ログイン用パスワード"
  echo ""

  check_prerequisites
  read_credentials
  create_or_update_secret

  echo ""
  success "完了！次のステップ: ./scripts/deploy_mfp.sh でデプロイしてください。"
}

main "$@"
