#!/usr/bin/env bash
# deploy_mfp.sh — asken-myfitnesspal-sync SAM デプロイスクリプト
#
# 使用方法:
#   ./scripts/deploy_mfp.sh [オプション]
#
# オプション:
#   -r, --region    AWSリージョン (デフォルト: ap-northeast-1)
#   -p, --profile   AWS CLIプロファイル (デフォルト: default)
#   -s, --secret    Secrets Managerのシークレット名 (デフォルト: asken-myfitnesspal-sync)
#   -g, --guided    sam deploy --guided で対話形式デプロイ
#   -h, --help      ヘルプを表示

set -euo pipefail

# ─── スクリプトのディレクトリを基準に絶対パスを解決する ──────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ─── デフォルト値 ─────────────────────────────────────────────────────────────
REGION="ap-northeast-1"
PROFILE="default"
SECRET_NAME="asken-myfitnesspal-sync"
GUIDED=false
STACK_NAME="asken-myfitnesspal-sync"
TEMPLATE_FILE="${REPO_ROOT}/template-mfp.yaml"
CONFIG_ENV="mfp"

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
    -g|--guided)  GUIDED=true;      shift ;;
    -h|--help)    usage ;;
    *) error "不明なオプション: $1"; exit 1 ;;
  esac
done

# ─── 前提条件チェック ─────────────────────────────────────────────────────────
check_prerequisites() {
  info "前提条件を確認しています..."

  if ! command -v python3 &>/dev/null; then
    error "python3 が見つかりません。インストールしてください:"
    error "  https://www.python.org/downloads/"
    exit 1
  fi
  success "python3: $(python3 --version)"

  if ! command -v sam &>/dev/null; then
    error "SAM CLI が見つかりません。以下でインストールしてください:"
    error "  https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
    exit 1
  fi
  local sam_version
  sam_version=$(sam --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
  local required_version="1.111.0"
  if [[ -z "$sam_version" ]]; then
    error "SAM CLI のバージョンを取得できませんでした。"
    error "  sam --version を手動で確認してください。"
    exit 1
  fi
  if ! python3 -c "
v = tuple(int(x) for x in '${sam_version}'.split('.'))
r = tuple(int(x) for x in '${required_version}'.split('.'))
exit(0 if v >= r else 1)
"; then
    error "SAM CLI ${sam_version} は要件未満です。${required_version} 以上が必要です (makefile ビルドサポート)。"
    error "  pip install --upgrade aws-sam-cli"
    exit 1
  fi
  success "SAM CLI: ${sam_version}"

  if ! command -v aws &>/dev/null; then
    error "AWS CLI が見つかりません。以下でインストールしてください:"
    error "  https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
  fi
  success "AWS CLI: $(aws --version)"

  if ! command -v docker &>/dev/null; then
    error "Docker が見つかりません。SAM ビルドに Docker が必要です。"
    error "  https://docs.docker.com/engine/install/"
    exit 1
  fi
  if ! docker info &>/dev/null; then
    error "Docker デーモンが起動していません。Docker を起動してください。"
    exit 1
  fi
  success "Docker: 起動確認済み"

  if [[ ! -f "$TEMPLATE_FILE" ]]; then
    error "SAM テンプレートが見つかりません: ${TEMPLATE_FILE}"
    exit 1
  fi
  success "SAM テンプレート: ${TEMPLATE_FILE}"

  # AWS認証情報確認
  if ! aws sts get-caller-identity --profile "$PROFILE" --region "$REGION" &>/dev/null; then
    error "AWS 認証情報が有効ではありません。"
    error "  aws configure --profile $PROFILE  で設定してください。"
    exit 1
  fi
  local account_id
  account_id=$(aws sts get-caller-identity --profile "$PROFILE" --region "$REGION" --query Account --output text)
  success "AWS アカウント: ${account_id} (リージョン: ${REGION}, プロファイル: ${PROFILE})"
}

# ─── Secrets Manager シークレット存在確認 ────────────────────────────────────
check_secret() {
  info "Secrets Manager シークレットを確認しています: ${SECRET_NAME}"

  if ! aws secretsmanager describe-secret \
      --secret-id "$SECRET_NAME" \
      --profile "$PROFILE" \
      --region "$REGION" &>/dev/null; then
    error "シークレット '${SECRET_NAME}' が存在しません。"
    error "先に以下を実行してシークレットを作成してください:"
    error "  ./scripts/create_secret_mfp.sh --region ${REGION} --profile ${PROFILE}"
    exit 1
  fi

  # 必須キーの存在確認
  local secret_value
  secret_value=$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_NAME" \
    --profile "$PROFILE" \
    --region "$REGION" \
    --query SecretString \
    --output text)

  for key in asken_email asken_password myfitnesspal_session_cookie; do
    if ! S="$secret_value" K="$key" python3 -c \
        "import sys,json,os; d=json.loads(os.environ['S']); sys.exit(0 if os.environ['K'] in d else 1)" \
        2>/dev/null; then
      error "シークレットに必須キー '${key}' がありません。"
      error "  ./scripts/create_secret_mfp.sh で再作成するか、手動で追加してください。"
      exit 1
    fi
  done
  success "シークレット確認済み (必須キーすべて存在)"
}

# ─── utils コピー / クリーンアップ ────────────────────────────────────────────
# Docker コンテナは CodeUri ディレクトリのみマウントされるため、
# ビルド前に src/utils/ を CodeUri 内に一時コピーして Makefile から参照できるようにする。
_UTILS_DST="${REPO_ROOT}/src/asken_myfitnesspal_sync/utils"

cleanup_utils() {
  if [[ -d "$_UTILS_DST" ]]; then
    rm -rf "$_UTILS_DST"
  fi
}

copy_utils() {
  cleanup_utils
  cp -r "${REPO_ROOT}/src/utils" "$_UTILS_DST"
  info "utils をコピーしました: ${_UTILS_DST}"
}

# ─── SAM ビルド ───────────────────────────────────────────────────────────────
sam_build() {
  cd "$REPO_ROOT"
  info "SAM ビルドを開始します (use_container=true, config-env=${CONFIG_ENV})..."
  sam build \
    --use-container \
    --cached \
    --parallel \
    --config-env "$CONFIG_ENV" \
    --profile "$PROFILE" \
    --region "$REGION"
  success "SAM ビルド完了"
}

# ─── SAM デプロイ ─────────────────────────────────────────────────────────────
sam_deploy() {
  if [[ "$GUIDED" == true ]]; then
    info "SAM 対話形式デプロイを開始します..."
    sam deploy \
      --guided \
      --config-env "$CONFIG_ENV" \
      --profile "$PROFILE" \
      --region "$REGION" \
      --parameter-overrides "SecretName=${SECRET_NAME}"
  else
    info "SAM デプロイを開始します..."
    sam deploy \
      --no-confirm-changeset \
      --config-env "$CONFIG_ENV" \
      --profile "$PROFILE" \
      --region "$REGION" \
      --parameter-overrides "SecretName=${SECRET_NAME}"
  fi
  success "SAM デプロイ完了"
}

# ─── デプロイ後確認 ───────────────────────────────────────────────────────────
post_deploy_check() {
  info "デプロイ後の確認を行います..."

  # Lambda関数名取得
  local function_name
  function_name=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --profile "$PROFILE" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='SyncFunctionName'].OutputValue" \
    --output text 2>/dev/null || true)

  if [[ -z "$function_name" ]]; then
    warn "Lambda 関数名を取得できませんでした。CloudFormation コンソールで確認してください。"
    return
  fi
  success "Lambda 関数名: ${function_name}"
  echo ""
  warn "次のステップ:"
  echo "  1. Lambda を手動実行して動作確認:"
  echo "     aws lambda invoke --function-name ${function_name} \\"
  echo "       --profile ${PROFILE} --region ${REGION} /tmp/response.json"
  echo "     cat /tmp/response.json"
  echo ""
  echo "  2. CloudWatch ログで実行結果を確認:"
  echo "     aws logs tail /aws/lambda/${function_name} \\"
  echo "       --follow --profile ${PROFILE} --region ${REGION}"
  echo ""
  echo "  3. MyFitnessPal でデータ連携を確認:"
  echo "     - 当日の食事データ (朝食/昼食/夕食/間食) が登録されているか"
  echo ""
  echo "  4. EventBridge スケジュールの確認 (毎時・毎日 23:59 JST で自動実行):"
  echo "     aws scheduler get-schedule --name asken-myfitnesspal-sync-hourly \\"
  echo "       --group-name default \\"
  echo "       --profile ${PROFILE} --region ${REGION}"
  echo "     aws scheduler get-schedule --name asken-myfitnesspal-sync-daily \\"
  echo "       --group-name default \\"
  echo "       --profile ${PROFILE} --region ${REGION}"
}

# ─── メイン ───────────────────────────────────────────────────────────────────
main() {
  echo ""
  echo "=== asken-myfitnesspal-sync デプロイ ==="
  echo "  リージョン : ${REGION}"
  echo "  プロファイル: ${PROFILE}"
  echo "  シークレット: ${SECRET_NAME}"
  echo "  スタック    : ${STACK_NAME}"
  echo "  テンプレート : ${TEMPLATE_FILE}"
  echo "  config-env  : ${CONFIG_ENV}"
  echo ""

  trap cleanup_utils EXIT

  check_prerequisites
  check_secret
  copy_utils
  sam_build
  sam_deploy
  post_deploy_check

  echo ""
  success "デプロイ完了！"
}

main "$@"
