#!/usr/bin/env bash
# Deploy the live demo to AWS Lambda behind a Function URL.
#
# Prerequisites: AWS credentials with permission to create IAM roles and Lambda
# functions, and Bedrock access to the model in src/agents.py.
#
# Usage:  bash scripts/build_lambda.sh && bash scripts/deploy_lambda.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1   # git-bash rewrites /tmp/... into a Windows path otherwise

FUNCTION="${FUNCTION:-due-diligence}"
REGION="${REGION:-us-east-1}"
ROLE="${ROLE:-due-diligence-lambda}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "→ creating execution role $ROLE"
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  # Scoped to the one model this app calls, rather than bedrock:*.
  aws iam put-role-policy --role-name "$ROLE" --policy-name bedrock-invoke-haiku \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\"],\"Resource\":[\"arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5*\",\"arn:aws:bedrock:*:${ACCOUNT}:inference-profile/us.anthropic.claude-haiku-4-5*\"]}]}"
  sleep 10   # role propagation
fi

if aws lambda get-function --function-name "$FUNCTION" --region "$REGION" >/dev/null 2>&1; then
  echo "→ updating code"
  aws lambda update-function-code --function-name "$FUNCTION" --region "$REGION" \
    --zip-file fileb://build/function.zip --no-cli-pager >/dev/null
else
  echo "→ creating function"
  aws lambda create-function --function-name "$FUNCTION" --region "$REGION" \
    --runtime python3.12 --role "arn:aws:iam::${ACCOUNT}:role/${ROLE}" \
    --handler lambda_handler.handler --zip-file fileb://build/function.zip \
    --timeout 60 --memory-size 1024 \
    --environment 'Variables={AFH_DATA_DIR=/tmp/data}' --no-cli-pager >/dev/null
fi

aws lambda wait function-updated-v2 --function-name "$FUNCTION" --region "$REGION"

# /tmp is the only writable path on Lambda, and it survives between warm
# invocations — which is exactly the lifetime the extraction cache wants.
aws lambda update-function-configuration --function-name "$FUNCTION" --region "$REGION" \
  --environment 'Variables={AFH_DATA_DIR=/tmp/data}' --no-cli-pager >/dev/null
aws lambda wait function-updated-v2 --function-name "$FUNCTION" --region "$REGION"

# ── public endpoint ───────────────────────────────────────────────────────────
# An HTTP API rather than a Lambda Function URL. The Function URL path is the
# obvious one and it is what the docs reach for first, but on this account every
# request to it returned 403 AccessDeniedException even with AuthType NONE and
# the documented wildcard resource policy in place — an account-level block on
# public Function URLs, not a misconfiguration. API Gateway is unaffected. If
# you are reproducing this on your own account, try the Function URL first; it
# is one fewer moving part.
API_ID="$(aws apigatewayv2 get-apis --region "$REGION"   --query "Items[?Name=='${FUNCTION}-demo'].ApiId | [0]" --output text)"

if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  echo "→ creating HTTP API"
  API_ID="$(aws apigatewayv2 create-api --name "${FUNCTION}-demo"     --protocol-type HTTP --target "arn:aws:lambda:${REGION}:${ACCOUNT}:function:${FUNCTION}"     --region "$REGION" --query ApiId --output text)"
  aws lambda add-permission --function-name "$FUNCTION" --region "$REGION"     --statement-id apigw-invoke --action lambda:InvokeFunction     --principal apigateway.amazonaws.com     --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT}:${API_ID}/*/*" >/dev/null
fi

echo "✓ live at https://${API_ID}.execute-api.${REGION}.amazonaws.com"
echo
echo "  This endpoint is unauthenticated and spends money per request."
echo "  Guards: the per-container rate limiter in web/app.py, the account"
echo "  concurrency ceiling, and a monthly AWS Budgets alarm. Set one up if"
echo "  you have not:  aws budgets create-budget --account-id $ACCOUNT ..."
