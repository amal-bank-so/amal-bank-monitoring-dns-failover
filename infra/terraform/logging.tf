# Route 53 public DNS query logging -> CloudWatch Logs (us-east-1 only).
# Retention is explicit. Reading the logs is a separate, scoped permission
# (see tools/deployer-policy.json for deploy-time access only).

locals {
  query_log_zones = {
    amalbank_so = aws_route53_zone.amalbank_so
    ebanking    = aws_route53_zone.ebanking
  }
}

resource "aws_cloudwatch_log_group" "query" {
  for_each = local.query_log_zones

  name              = "/aws/route53/${each.value.name}"
  retention_in_days = var.query_log_retention_days
}

data "aws_iam_policy_document" "route53_query_logs" {
  statement {
    sid       = "Route53QueryLogging"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for g in aws_cloudwatch_log_group.query : "${g.arn}:*"]

    principals {
      type        = "Service"
      identifiers = ["route53.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# Resource policies are account/region-wide (max 10); one policy for both groups.
resource "aws_cloudwatch_log_resource_policy" "route53_query_logs" {
  policy_name     = "${var.name_prefix}-route53-query-logging"
  policy_document = data.aws_iam_policy_document.route53_query_logs.json
}

resource "aws_route53_query_log" "this" {
  for_each = local.query_log_zones

  zone_id                  = each.value.zone_id
  cloudwatch_log_group_arn = aws_cloudwatch_log_group.query[each.key].arn

  depends_on = [aws_cloudwatch_log_resource_policy.route53_query_logs]
}
