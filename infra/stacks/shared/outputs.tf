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
