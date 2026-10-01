#!/usr/bin/env bash
# Source this file:  . tools/aws-env.sh
# Maps the mixed-case credential variables used in this environment onto the names
# the AWS SDK/Terraform read. Prints no secret values.
if [ -n "${AWS_Access_key:-}" ] && [ -n "${AWS_Secret_Access_key:-}" ]; then
  export AWS_ACCESS_KEY_ID="$AWS_Access_key"
  export AWS_SECRET_ACCESS_KEY="$AWS_Secret_Access_key"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  echo "AWS credentials mapped (key id ${AWS_ACCESS_KEY_ID:0:4}...)"
else
  echo "AWS_Access_key / AWS_Secret_Access_key not set; using standard AWS_* variables as-is"
fi
