# Shared alert topic for every stack's CloudWatch alarms -> owner-approved recipients.
# Email subscriptions stay "PendingConfirmation" until each recipient clicks the
# confirmation link; delivery must be verified before cutover (plan task 2.3).
# The topic is not KMS-encrypted: CloudWatch alarms cannot publish to topics
# encrypted with the AWS-managed SNS key. Use a customer-managed key policy if
# the bank requires encryption at rest.
#
# Other stacks find this topic by name (${var.name_prefix}-alerts), so apply this
# stack first.

data "aws_caller_identity" "current" {}

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-alerts"
}

data "aws_iam_policy_document" "alerts" {
  statement {
    sid       = "AllowCloudWatchAlarms"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}
