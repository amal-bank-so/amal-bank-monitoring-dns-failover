output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "alerts_topic_name" {
  value = aws_sns_topic.alerts.name
}

output "email_subscriptions" {
  description = "Recipients to confirm. Subscriptions are PendingConfirmation until the link in each email is clicked."
  value       = sort(var.alert_emails)
}

output "notify_lambda" {
  value = aws_lambda_function.notify.function_name
}

output "notification_recipients" {
  description = "Recipients configured in Terraform (the secret may add more through SENDGRID_TO_EMAILS)."
  value       = sort(var.notification_recipients)
}
