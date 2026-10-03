# Alarm notifications by email through SendGrid: exactly four e-banking notifications (the Lambda ignores every other
# alarm message; those alarms remain visible in the CloudWatch console):
#   High      Failover from Primary to Secondary   (ebanking stack alarm *-ebanking-failover -> ALARM)
#   High      Failover from Secondary to Primary   (*-ebanking-primary-unhealthy -> OK)
#   High      Secondary is Down                    (*-ebanking-secondary-unhealthy -> ALARM)
#   Critical  E-Banking is Down                    (*-ebanking-both-unhealthy -> ALARM)
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
    sid       = "ReadOnlyTheNotificationSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = compact([var.sendgrid_secret_arn, var.recipients_secret_arn])
  }
  statement {
    sid       = "ReadTheLogo"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.assets.arn}/${var.logo_key}"]
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
      SECRET_ARN            = var.sendgrid_secret_arn
      RECIPIENTS_SECRET_ARN = var.recipients_secret_arn
      RECIPIENTS            = join(",", var.notification_recipients)
      ASSET_BUCKET          = aws_s3_bucket.assets.bucket
      LOGO_KEY              = var.logo_key
      CONSOLE_REGION        = var.aws_region
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

# Private bucket for the email logo: upload (or replace) the object `logo_key` and the next notification uses it, no deployment
# needed. The notify Lambda can read exactly that one object; nothing else is stored here.
resource "aws_s3_bucket" "assets" {
  bucket = "${var.name_prefix}-notify-assets-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "assets_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.assets.arn, "${aws_s3_bucket.assets.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "assets" {
  bucket = aws_s3_bucket.assets.id
  policy = data.aws_iam_policy_document.assets_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.assets]
}

