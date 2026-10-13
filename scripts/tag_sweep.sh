#!/usr/bin/env bash
# Read-only post-destroy sweep. Lists everything still tagged Project=tixlane via the Resource
# Groups Tagging API, plus IAM (global) and Lambda log groups recreated after deletion (which
# would be untagged). Writes JSON evidence and exits non-zero if anything remains.
#
#   scripts/tag_sweep.sh [--env prod] [--out docs/evidence/tag-sweep.json]
#
# --env prod   ignore resources tagged Env=bootstrap (run before the human bootstrap teardown)
# STRICT=1     also count KMS keys in PendingDeletion (they disappear after the 7-day window)
set -euo pipefail

region="${AWS_REGION:-eu-central-1}"
env_filter=""
out="docs/evidence/tag-sweep.json"
while (( $# )); do
  case "$1" in
    --env) env_filter="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done

filters=(Key=Project,Values=tixlane)
[[ -n "$env_filter" ]] && filters+=("Key=Env,Values=${env_filter}")

mkdir -p "$(dirname "$out")"
tagged="$(aws resourcegroupstaggingapi get-resources --region "$region" --tag-filters "${filters[@]}" --output json)"

pending_kms=()
remaining=()
while IFS= read -r arn; do
  [[ -z "$arn" ]] && continue
  if [[ "$arn" == arn:aws:kms:* ]]; then
    state="$(aws kms describe-key --region "$region" --key-id "$arn" --query KeyMetadata.KeyState --output text 2>/dev/null || echo Unknown)"
    if [[ "$state" == "PendingDeletion" && "${STRICT:-0}" != "1" ]]; then
      pending_kms+=("$arn")
      continue
    fi
  fi
  remaining+=("$arn")
done < <(jq -r '.ResourceTagMappingList[].ResourceARN' <<<"$tagged")

prefix="tixlane-${env_filter:-}"
iam_roles="$(aws iam list-roles --query "Roles[?starts_with(RoleName, '${prefix}')].Arn" --output json)"
log_groups="$(aws logs describe-log-groups --region "$region" --log-group-name-prefix "/aws/lambda/${prefix}" --query 'logGroups[].logGroupName' --output json)"

jq -n \
  --arg swept_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg region "$region" \
  --arg env_filter "${env_filter:-<all>}" \
  --argjson tagged "$tagged" \
  --argjson remaining "$(printf '%s\n' "${remaining[@]:-}" | jq -R . | jq -s 'map(select(length > 0))')" \
  --argjson pending_kms "$(printf '%s\n' "${pending_kms[@]:-}" | jq -R . | jq -s 'map(select(length > 0))')" \
  --argjson iam_roles "$iam_roles" \
  --argjson log_groups "$log_groups" \
  '{swept_at: $swept_at, region: $region, env_filter: $env_filter,
    remaining_tagged: $remaining, kms_pending_deletion: $pending_kms,
    iam_roles: $iam_roles, lambda_log_groups: $log_groups,
    raw_tagging_api_count: ($tagged.ResourceTagMappingList | length)}' >"$out"

total=$(( ${#remaining[@]} + $(jq length <<<"$iam_roles") + $(jq length <<<"$log_groups") ))
echo "tag sweep: ${total} remaining, ${#pending_kms[@]} KMS key(s) pending deletion -> ${out}"
(( total == 0 ))
