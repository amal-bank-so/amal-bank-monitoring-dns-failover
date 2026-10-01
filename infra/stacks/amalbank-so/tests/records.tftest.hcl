# Offline tests (mock provider, no AWS access): run with `terraform test`.
# Guards the conversion of inventory values into what the AWS provider expects. A plain
# plan against real AWS found that quoted TXT values were double-quoted and rejected.

mock_provider "aws" {}

variables {
  enable_alarms  = false
  legacy_web_ips = ["192.0.2.1"]
}

run "txt_values_are_unquoted_for_the_provider" {
  command = plan

  variables {
    inventory_file = "tests/fixtures/txt.json"
  }

  assert {
    condition     = aws_route53_record.inventory["@|TXT"].records == toset(["v=spf1 include:spf.protection.outlook.com -all", "MS=ms12345678"])
    error_message = "apex TXT values must be passed to the provider without their outer quotes"
  }

  assert {
    condition     = aws_route53_record.inventory["long|TXT"].records == toset(["first-string\"\"second-string"])
    error_message = "a multi-string TXT value must use \"\" between strings"
  }

  assert {
    condition     = aws_route53_record.inventory["autodiscover|TXT"].records == toset(["autodiscover.outlook.com"])
    error_message = "single TXT value must lose its outer quotes"
  }

  assert {
    condition     = aws_route53_record.inventory["_dmarc|TXT"].records == toset(["v=DMARC1; p=none; rua=mailto:dmarc@example.com"])
    error_message = "semicolons and other characters inside a TXT value must be preserved"
  }

  assert {
    condition     = aws_route53_record.inventory["@|MX"].records == toset(["5 amalbank-so.mail.protection.outlook.com."]) && aws_route53_record.inventory["sel|CNAME"].records == toset(["sel.example.com."])
    error_message = "non-TXT types must pass through unchanged"
  }
}

run "unquoted_txt_in_the_inventory_is_refused" {
  command = plan

  variables {
    inventory_file = "tests/fixtures/txt_unquoted.json"
  }

  expect_failures = [terraform_data.inventory_guard]
}

run "web_records_default_to_the_legacy_ip_and_ttl" {
  command = plan

  variables {
    inventory_file = "tests/fixtures/txt.json"
  }

  assert {
    condition     = aws_route53_record.web["www-A"].records == toset(["192.0.2.1"]) && aws_route53_record.web["www-A"].ttl == 60
    error_message = "www must serve the legacy IP with TTL 60 until web_use_cloudfront"
  }

  assert {
    condition     = length(aws_route53_record.web) == 2
    error_message = "before the switch only the apex and www A records exist"
  }
}

run "switch_replaces_legacy_records_with_cloudfront_aliases" {
  command = plan

  variables {
    inventory_file               = "tests/fixtures/txt.json"
    enable_certificate           = true
    enable_redirect              = true
    enable_redirect_distribution = true
    web_use_cloudfront           = true
    redirect_status_code         = 301
    redirect_preserve_path       = true
    redirect_preserve_query      = true
  }

  assert {
    condition     = length(aws_route53_record.web) == 4
    error_message = "after the switch the apex and www each have an A and AAAA alias"
  }
}
