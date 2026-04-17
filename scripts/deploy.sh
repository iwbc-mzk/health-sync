#!/usr/bin/env bash
# deploy.sh — asken-garmin-sync SAM デプロイスクリプト
#
# 使用方法:
#   ./scripts/deploy.sh [オプション]
#
# オプション:
#   -r, --region    AWSリージョン (デフォルト: ap-northeast-1)
#   -p, --profile   AWS CLIプロファイル (デフォルト: default)
#   -s, --secret    Secrets Managerのシークレット名 (デフォルト: asken-garmin-sync)
#   -g, --guided    sam deploy --guided で対話形式デプロイ
#   -h, --help      ヘルプを表示

set -euo pipefail

# ─── デフォルト値 ─────────────────────────────────────────────────────────────
REGION="ap-northeast-1"
PROFILE="default"
SECRET_NAME="asken-garmin-sync"
GUIDED=false

# ─── カラー出力 ───────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

  if ! command -v sam &>/dev/null; then
    error "SAM CLI が見つかりません。以下でインストールしてください:"
    error "  https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
    exit 1
  fi
  success "SAM CLI: $(sam --version)"

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
    error "  ./scripts/create_secret.sh --region ${REGION} --profile ${PROFILE}"
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

  for key in asken_email asken_password garmin_email garmin_password; do
    if ! S="$secret_value" K="$key" python -c \
        "import sys,json,os; d=json.loads(os.environ['S']); sys.exit(0 if os.environ['K'] in d else 1)" \
        2>/dev/null; then
      error "シークレットに必須キー '${key}' がありません。"
      error "  ./scripts/create_secret.sh で再作成するか、手動で追加してください。"
      exit 1
    fi
  done
  success "シークレット確認済み (必須キーすべて存在)"
}

# ─── SAM ビルド ───────────────────────────────────────────────────────────────
sam_build() {
  info "SAM ビルドを開始します (use_container=true)..."
  sam build \
    --use-container \
    --cached \
    --parallel \
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
      --profile "$PROFILE" \
      --region "$REGION" \
      --parameter-overrides "SecretName=${SECRET_NAME}"
  else
    info "SAM デプロイを開始します (チェンジセットプレビュー中)..."
    # --no-confirm-changeset でプレビューのみ表示し、スクリプト側で実行可否を確認する。
    # samconfig.toml の confirm_changeset = true はここでは使用しない。
    sam deploy \
      --no-confirm-changeset \
      --profile "$PROFILE" \
      --region "$REGION" \
      --parameter-overrides "SecretName=${SECRET_NAME}"
  fi
  success "SAM デプロイ完了"
}

# ─── デプロイ後確認 ───────────────────────────────────────────────────────────
post_deploy_check() {
  info "デプロイ後の確認を行います..."

  local stack_name="asken-garmin-sync"

  # Lambda関数名取得
  local function_name
  function_name=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --profile "$PROFILE" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='SyncFunctionName'].OutputValue" \
    --output text)

  success "Lambda 関数名: ${function_name}"
  echo ""
  warn "次のステップ:"
  echo "  1. Lambda を手動実行してGarminトークンを確立:"
  echo "     aws lambda invoke --function-name ${function_name} \\"
  echo "       --profile ${PROFILE} --region ${REGION} /tmp/response.json"
  echo "     cat /tmp/response.json"
  echo ""
  echo "  2. CloudWatch ログで実行結果を確認:"
  echo "     aws logs tail /aws/lambda/${function_name} \\"
  echo "       --follow --profile ${PROFILE} --region ${REGION}"
  echo ""
  echo "  3. 両サービスで以下のデータを確認:"
  echo "     - あすけん: 当日の消費カロリーが更新されているか"
  echo "     - Garmin Connect: 当日の体重・体脂肪率が登録されているか"
  echo ""
  echo "  4. EventBridge スケジュールの確認 (30分間隔で自動実行):"
  echo "     aws scheduler get-schedule --name asken-garmin-sync-schedule \\"
  echo "       --profile ${PROFILE} --region ${REGION}"
}

# ─── メイン ───────────────────────────────────────────────────────────────────
main() {
  echo ""
  echo "=== asken-garmin-sync デプロイ ==="
  echo "  リージョン : ${REGION}"
  echo "  プロファイル: ${PROFILE}"
  echo "  シークレット: ${SECRET_NAME}"
  echo ""

  check_prerequisites
  check_secret
  sam_build
  sam_deploy
  post_deploy_check

  echo ""
  success "デプロイ完了！"
}

main "$@"
