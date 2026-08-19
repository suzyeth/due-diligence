#!/usr/bin/env bash
# Deploy the scheduled arm: AgentCore Runtime + a daily EventBridge schedule.
#
# This is the part that makes the product's central claim true. Everything else
# runs because a person asked; this runs on its own and mails only when what
# needs you has changed.
#
# Usage:  bash scripts/build_agentcore.sh && bash scripts/deploy_agentcore.sh you@example.com
set -euo pipefail
export MSYS_NO_PATHCONV=1   # git-bash rewrites /tmp/... and arn:... otherwise

EMAIL="${1:-}"
REGION="${REGION:-us-east-1}"
NAME="${NAME:-due_diligence}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-due-diligence-agentcore-${ACCOUNT}}"
TOPIC_NAME="due-diligence-alerts"

# ── state + code bucket ───────────────────────────────────────────────────────
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
fi
aws s3 cp build-ac/agent.zip "s3://${BUCKET}/code/agent.zip" --region "$REGION" --only-show-errors

# ── where notifications go ────────────────────────────────────────────────────
TOPIC_ARN="$(aws sns create-topic --name "$TOPIC_NAME" --region "$REGION" \
  --query TopicArn --output text)"
if [ -n "$EMAIL" ]; then
  aws sns subscribe --topic-arn "$TOPIC_ARN" --protocol email \
    --notification-endpoint "$EMAIL" --region "$REGION" >/dev/null
  echo "→ confirm the subscription in your inbox, or no mail will arrive"
fi

# ── runtime role ──────────────────────────────────────────────────────────────
# Scoped to one model, one S3 key and one topic. An agent that watches tax
# deadlines has no business holding broader credentials than that.
if ! aws iam get-role --role-name "${NAME}-agentcore" >/dev/null 2>&1; then
  aws iam create-role --role-name "${NAME}-agentcore" --assume-role-policy-document "{
    \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",
    \"Principal\":{\"Service\":\"bedrock-agentcore.amazonaws.com\"},\"Action\":\"sts:AssumeRole\",
    \"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"${ACCOUNT}\"}}}]}" >/dev/null
  aws iam put-role-policy --role-name "${NAME}-agentcore" --policy-name agent-permissions \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
      {\"Effect\":\"Allow\",\"Action\":[\"bedrock:InvokeModel\",\"bedrock:InvokeModelWithResponseStream\"],
       \"Resource\":[\"arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5*\",
                     \"arn:aws:bedrock:*:${ACCOUNT}:inference-profile/us.anthropic.claude-haiku-4-5*\"]},
      {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\"],
       \"Resource\":\"arn:aws:s3:::${BUCKET}/last-notified.json\"},
      {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\"],\"Resource\":\"arn:aws:s3:::${BUCKET}/code/*\"},
      {\"Effect\":\"Allow\",\"Action\":[\"sns:Publish\"],\"Resource\":\"${TOPIC_ARN}\"},
      {\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",
        \"logs:PutLogEvents\",\"logs:DescribeLogStreams\"],
       \"Resource\":\"arn:aws:logs:${REGION}:${ACCOUNT}:log-group:/aws/bedrock-agentcore/*\"}]}" >/dev/null
  sleep 10
fi

echo "→ creating the runtime (ARM64 code artifact from S3, no container build)"
RUNTIME_ARN="$(aws bedrock-agentcore-control create-agent-runtime --region "$REGION" \
  --agent-runtime-name "$NAME" \
  --role-arn "arn:aws:iam::${ACCOUNT}:role/${NAME}-agentcore" \
  --agent-runtime-artifact "{\"codeConfiguration\":{\"code\":{\"s3\":{\"bucket\":\"${BUCKET}\",\"prefix\":\"code/agent.zip\"}},\"runtime\":\"PYTHON_3_12\",\"entryPoint\":[\"agentcore_app.py\"]}}" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --protocol-configuration '{"serverProtocol":"HTTP"}' \
  --environment-variables "AFH_DATA_DIR=/tmp/data,AFH_STATE_BUCKET=${BUCKET},AFH_TOPIC_ARN=${TOPIC_ARN},AWS_REGION=${REGION}" \
  --query agentRuntimeArn --output text)"
RUNTIME_ID="${RUNTIME_ARN##*/}"

# Creation is asynchronous and an ARM64 mismatch only surfaces here.
until [ "$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" \
      --region "$REGION" --query status --output text)" != "CREATING" ]; do sleep 10; done
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" \
  --region "$REGION" --query '{status:status,failure:failureReason}' --output json

echo
echo "✓ runtime: $RUNTIME_ARN"
echo
echo "Next: create the schedule. Its Input carries the person being watched, so"
echo "the agent itself stays stateless — see scripts/schedule_example.json."
