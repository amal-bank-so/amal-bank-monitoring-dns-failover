# Production settings for the shared stack (no secrets).

# Alarm notifications go out by email through SendGrid. The API key and the sender address are read at run time from this
# AWS Secrets Manager secret (eu-west-1) and never appear in the repository, the environment or the Terraform state.
sendgrid_secret_arn = "arn:aws:secretsmanager:eu-west-1:029288159395:secret:SendGrid_API-8txWzZ"

# Recipients live in a second secret (JSON field SENDGRID_TO_EMAILS, comma separated, eu-west-1), so they can be changed without
# a deployment. Addresses can also be listed here.
recipients_secret_arn   = "arn:aws:secretsmanager:eu-west-1:029288159395:secret:SENDGRID_TO_EMAILS-xCE0uY"
notification_recipients = []
