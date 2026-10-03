# Production settings for the shared stack (no secrets).

# Alarm notifications go out by email through SendGrid. The API key and the sender address are read at run time from this
# AWS Secrets Manager secret (eu-west-1) and never appear in the repository, the environment or the Terraform state.
sendgrid_secret_arn = "arn:aws:secretsmanager:eu-west-1:029288159395:secret:SendGrid_API-8txWzZ"

# Recipients. Add addresses here, or add a comma-separated SENDGRID_TO_EMAILS field to the secret (no deployment needed).
notification_recipients = []
