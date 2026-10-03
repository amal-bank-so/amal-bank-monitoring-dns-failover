# Alarm notifications by email through SendGrid: exactly three e-banking notifications (the Lambda ignores every other
# alarm message; those alarms remain visible in the CloudWatch console):
#   HIGH      Failover from Primary to Secondary   (ebanking stack alarm *-ebanking-failover -> ALARM)
#   HIGH      Primary is Back                      (*-ebanking-primary-unhealthy -> OK)
#   CRITICAL  E-Banking is Down                    (*-ebanking-both-unhealthy -> ALARM)
#
# Every alarm in every stack publishes to the shared SNS topic. A Lambda subscribed to that topic reads the SendGrid API key
# and sender from AWS Secrets Manager at run time (nothing secret is in this repository, the environment or the state) and
# sends one email per alarm state change to the recipients. Its role can read exactly one secret and write its own logs.
#
# lambda/notify.zip is built from lambda/notify.py (+ optional lambda/logo.png) by `python lambda/build.py`; rebuild and
# commit it after changing either file. Direct invocation with {"selftest": true} validates the secret, the key and the
# outbound path without sending any email.

data "aws_iam_policy_document" "notify_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "notify" {
  name               = "${var.name_prefix}-notify"
  assume_role_policy = data.aws_iam_policy_document.notify_assume.json
}

data "aws_iam_policy_document" "notify" {
  statement {
    sid       = "ReadOnlyTheSendGridSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.sendgrid_secret_arn]
  }
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.notify.arn}:*"]
  }
}

resource "aws_iam_role_policy" "notify" {
  name   = "notify"
  role   = aws_iam_role.notify.id
  policy = data.aws_iam_policy_document.notify.json
}

resource "aws_cloudwatch_log_group" "notify" {
  name              = "/aws/lambda/${var.name_prefix}-notify"
  retention_in_days = var.notification_log_retention_days
}

resource "aws_lambda_function" "notify" {
  function_name    = "${var.name_prefix}-notify"
  description      = "Emails CloudWatch alarm notifications from the shared SNS topic through SendGrid"
  role             = aws_iam_role.notify.arn
  runtime          = "python3.12"
  handler          = "notify.handler"
  filename         = "${path.module}/lambda/notify.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda/notify.zip")
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      SECRET_ARN      = var.sendgrid_secret_arn
      RECIPIENTS      = join(",", var.notification_recipients)
      SUBJECT_PREFIX  = var.notification_subject_prefix
      PRIMARY_LABEL   = var.ebanking_primary_label
      SECONDARY_LABEL = var.ebanking_secondary_label
      CONSOLE_REGION  = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.notify, aws_iam_role_policy.notify]
}

resource "aws_lambda_permission" "from_sns" {
  statement_id  = "AllowAlertTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.notify.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic_subscription" "notify" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.notify.arn

  depends_on = [aws_lambda_permission.from_sns]
}
