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

echo "✓ deployed. To expose it publicly (this creates an unauthenticated URL that"
echo "  spends money on every request — read web/app.py's rate limiter first):"
echo
echo "  aws lambda create-function-url-config --function-name $FUNCTION \\"
echo "    --auth-type NONE --region $REGION --query FunctionUrl --output text"
echo "  aws lambda add-permission --function-name $FUNCTION --region $REGION \\"
echo "    --statement-id public --action lambda:InvokeFunctionUrl \\"
echo "    --principal '*' --function-url-auth-type NONE"
