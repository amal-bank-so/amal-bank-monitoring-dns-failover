# Route 53 public DNS query logging -> CloudWatch Logs (us-east-1 only).
# Retention is explicit. Reading the logs is a separate, scoped permission.
# Each stack has its own resource policy scoped to its own log group, so the
# stacks stay independent (account limit: 10 policies per region).

data "aws_caller_identity" "current" {}

resource "aws_cloudwatch_log_group" "query" {
  name              = "/aws/route53/${local.ebanking_zone}"
  retention_in_days = var.query_log_retention_days
}

data "aws_iam_policy_document" "route53_query_logs" {
  statement {
    sid       = "Route53QueryLogging"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.query.arn}:*"]

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

resource "aws_cloudwatch_log_resource_policy" "route53_query_logs" {
  policy_name     = "${var.name_prefix}-route53-query-logging-ebanking"
  policy_document = data.aws_iam_policy_document.route53_query_logs.json
}

resource "aws_route53_query_log" "this" {
  zone_id                  = aws_route53_zone.ebanking.zone_id
  cloudwatch_log_group_arn = aws_cloudwatch_log_group.query.arn

  depends_on = [aws_cloudwatch_log_resource_policy.route53_query_logs]
}
