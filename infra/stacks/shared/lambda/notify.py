"""Email the four e-banking notifications through SendGrid.

Subscribed to the SNS topic that every CloudWatch alarm publishes to. Only these four alarm events send an email; every
other alarm message is ignored (the alarms stay visible in the CloudWatch console):

    High      Failover from Primary to Secondary   alarm <prefix>-ebanking-failover            -> ALARM
    High      Failover from Secondary to Primary   alarm <prefix>-ebanking-primary-unhealthy   -> OK
    High      Secondary is Down                    alarm <prefix>-ebanking-secondary-unhealthy -> ALARM
    Critical  E-Banking is Down                    alarm <prefix>-ebanking-both-unhealthy      -> ALARM

The email subject is exactly "<Severity> - <Title>" (for example "High - Failover from Secondary to Primary"): no prefix, no marker and no carrier names.

The SendGrid API key and the sender address are read from AWS Secrets Manager at run time (never from the environment or the
repository):

    {"SENDGRID_API_KEY": "...", "SENDGRID_FROM_EMAIL": "...", "SENDGRID_TO_EMAILS": "a@x.com,b@y.com"}   # last field optional

Recipients are the union of: the RECIPIENTS environment variable (comma separated, from Terraform), the optional SENDGRID_TO_EMAILS
field of the SendGrid secret, and the secret named by RECIPIENTS_SECRET_ARN (JSON with a SENDGRID_TO_EMAILS field, or a plain
comma separated string), so recipients can be added without a deployment. Each recipient gets their own personalization, so addresses
are not exposed to each other. A failed send raises, so SNS retries the delivery. The Amal Bank logo is embedded in the email header from, in order: the S3 object
ASSET_BUCKET/LOGO_KEY (upload or replace it any time, no deployment needed), a logo.png bundled next to this file, or a plain
text header if neither exists.

A direct invocation with {"send_test": true} sends one test email per notification through the real rendering and
SendGrid path, without touching any alarm or endpoint. The subject is the real one; a grey banner inside the body marks it as a test.

A direct invocation with {"selftest": true} checks the secret, the key and the outbound path (GET /v3/scopes) without sending
any email.
"""
import json
import os
import urllib.error
import urllib.request

import boto3

SENDGRID = "https://api.sendgrid.com/v3"
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")  # optional; bundled by build.py when present
LOGO_CID = "amalbank-logo"
NAVY = "#042c75"  # the logo's own background navy, so the logo blends into the header
SEVERITY_COLOR = {"Critical": "#b71c1c", "High": "#e65100"}
_secret_cache = None
_recipients_cache = None
_logo_cache = None


def classify(alarm_name, state):
    """-> (severity, title, explanation) for one of the three notifications, else None."""
    if alarm_name.endswith("-ebanking-failover") and state == "ALARM":
        return ("High", "Failover from Primary to Secondary",
                "The primary endpoint is failing its health check and the secondary is healthy. Route 53 is now "
                "answering with the secondary, so e-banking traffic is being served through it.")
    if alarm_name.endswith("-ebanking-primary-unhealthy") and state == "OK":
        return ("High", "Failover from Secondary to Primary",
                "The primary endpoint is passing its health check again. Route 53 answers with the primary again.")
    if alarm_name.endswith("-ebanking-secondary-unhealthy") and state == "ALARM":
        return ("High", "Secondary is Down",
                "The secondary endpoint is failing its health check, so there is currently no healthy failover target. If "
                "the primary is healthy, e-banking is not affected. If the primary also fails you will receive the Critical "
                "E-Banking is Down notification.")
    if alarm_name.endswith("-ebanking-both-unhealthy") and state == "ALARM":
        return ("Critical", "E-Banking is Down",
                "Both the primary and the secondary endpoints are failing their health checks. Route 53 keeps "
                "answering with the primary because neither is healthy, so customers may be unable to reach e-banking. "
                "Investigate now.")
    return None


def _secret():
    global _secret_cache
    if _secret_cache is None:
        arn = os.environ["SECRET_ARN"]
        client = boto3.client("secretsmanager", region_name=arn.split(":")[3])  # the secret may live in another region
        raw = json.loads(client.get_secret_value(SecretId=arn)["SecretString"])
        _secret_cache = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}  # tolerate stray spaces
    return _secret_cache


def _recipients_secret():
    """Addresses from the optional separate recipients secret (JSON {"SENDGRID_TO_EMAILS": "a,b"} or a plain string)."""
    global _recipients_cache
    arn = os.environ.get("RECIPIENTS_SECRET_ARN")
    if not arn:
        return ""
    if _recipients_cache is None:
        client = boto3.client("secretsmanager", region_name=arn.split(":")[3])
        raw = client.get_secret_value(SecretId=arn)["SecretString"]
        try:
            data = {k.strip(): v for k, v in json.loads(raw).items()}
            _recipients_cache = data.get("SENDGRID_TO_EMAILS") or ""
        except (ValueError, AttributeError):
            _recipients_cache = raw
    return _recipients_cache


def _split(text):
    return [a.strip() for a in (text or "").replace(";", ",").split(",") if a.strip()]


def _recipients(secret):
    seen, out = set(), []
    for a in _split(os.environ.get("RECIPIENTS")) + _split(secret.get("SENDGRID_TO_EMAILS")) + _split(_recipients_secret()):
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def _request(method, path, key, payload=None):
    req = urllib.request.Request(
        SENDGRID + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:  # report the status and SendGrid's error text, never the request headers
        return err.code, err.read().decode()[:500]


def _logo_bytes():
    """Logo PNG bytes from S3 (ASSET_BUCKET/LOGO_KEY), else a bundled logo.png, else None."""
    global _logo_cache
    if _logo_cache is not None:
        return _logo_cache or None
    data = b""
    bucket = os.environ.get("ASSET_BUCKET")
    if bucket:
        try:
            data = boto3.client("s3").get_object(Bucket=bucket, Key=os.environ.get("LOGO_KEY", "logo.png"))["Body"].read()
        except Exception as err:  # missing object or no access: fall back, never block a notification
            print("logo not loaded from S3: %s" % type(err).__name__)
    if not data and os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "rb") as fh:
            data = fh.read()
    _logo_cache = data
    return data or None


def _logo_attachment():
    """The bank logo as an inline (CID) attachment, or None when no logo is available."""
    data = _logo_bytes()
    if not data:
        return None
    import base64
    kind, ext = (("image/png", "png") if data[:8] == b"\x89PNG\r\n\x1a\n" else
                 ("image/jpeg", "jpg") if data[:3] == b"\xff\xd8\xff" else
                 ("image/gif", "gif") if data[:4] == b"GIF8" else (None, None))
    if kind is None:
        print("logo ignored: not a PNG, JPEG or GIF")
        return None
    return {"content": base64.b64encode(data).decode(), "type": kind, "filename": "amal-bank-logo." + ext,
            "disposition": "inline", "content_id": LOGO_CID}


def render(alarm, severity, title, explanation, has_logo=False, test=False):
    """-> (subject, text, html) for one notification. alarm is the CloudWatch alarm JSON from the SNS message."""
    name = alarm["AlarmName"]
    link = "https://console.aws.amazon.com/cloudwatch/home?region=%s#alarmsV2:alarm/%s" % (
        os.environ.get("CONSOLE_REGION", "us-east-1"), name)
    rows = [
        ("Event", "%s - %s" % (severity, title)), ("What happened", explanation),
        ("Alarm", name), ("Detail", alarm.get("AlarmDescription") or "-"), ("Reason", alarm.get("NewStateReason") or "-"),
        ("Time", alarm.get("StateChangeTime") or "-"), ("Account", alarm.get("AWSAccountId") or "-"), ("Console", link),
    ]
    if test:
        rows.insert(1, ("TEST", "This is a TEST message. No real alarm fired and nothing is wrong."))
    text = "\n".join("%s: %s" % r for r in rows)
    banner = ('<div style="background:#455a64;color:#fff;padding:8px 16px;font-size:13px;font-weight:bold">TEST MESSAGE - no real '
              'alarm fired, nothing is wrong</div>') if test else ""
    table = "<table cellpadding='5' style='font-size:14px'>" + "".join(
        "<tr><td valign='top'><b>%s</b></td><td>%s</td></tr>" % (k, v if k != "Console" else "<a href='%s'>Open in CloudWatch</a>" % v)
        for k, v in rows[1:] if k != "TEST") + "</table>"
    head = ('<img src="cid:%s" alt="Amal Bank" width="140" height="140" style="display:block;margin:0 auto">' % LOGO_CID) if has_logo else (
        '<span style="color:#fff;font-size:22px;font-weight:bold;letter-spacing:1px">Amal Bank</span>')
    color = SEVERITY_COLOR[severity]
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;border:1px solid #d9dde8">'
        '<div style="background:%s;padding:0;text-align:center">%s</div>'
        '%s<div style="background:%s;color:#fff;padding:10px 16px;font-size:16px;font-weight:bold">%s</div>'
        '<div style="padding:16px">%s</div>'
        '<div style="background:#f3f5fa;color:#667;padding:10px 16px;font-size:12px">Automated DNS monitoring notification '
        '(ebanking.amalbankso.com on AWS Route 53). Do not reply.</div></div>'
    ) % (NAVY, head, banner, color, "%s - %s" % (severity, title), table)
    return "%s - %s" % (severity, title), text, html


TEST_CASES = [  # (alarm name suffix, state, description) for the TEST emails: the same four events the real alarms produce
    ("ebanking-failover", "ALARM", "TEST: primary unhealthy and secondary healthy (simulated)"),
    ("ebanking-primary-unhealthy", "OK", "TEST: primary healthy again (simulated)"),
    ("ebanking-secondary-unhealthy", "ALARM", "TEST: secondary unhealthy (simulated)"),
    ("ebanking-both-unhealthy", "ALARM", "TEST: both endpoints unhealthy (simulated)"),
]


def _send_test(key, sender, to, logo):
    import datetime
    if not to:
        return {"sent": 0, "error": "no recipients configured"}
    prefix = os.environ.get("TEST_ALARM_PREFIX", "amal-dns")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sent = []
    for suffix, state, desc in TEST_CASES:
        alarm = {"AlarmName": "%s-%s" % (prefix, suffix), "NewStateValue": state, "AlarmDescription": desc,
                 "NewStateReason": "TEST: simulated, no real alarm", "StateChangeTime": now, "AWSAccountId": "-"}
        kind = classify(alarm["AlarmName"], state)
        subject, text, html = render(alarm, *kind, has_logo=bool(logo), test=True)
        payload = {
            "personalizations": [{"to": [{"email": a}]} for a in to], "from": {"email": sender},
            "subject": subject,
            "content": [{"type": "text/plain", "value": text}, {"type": "text/html", "value": html}],
        }
        if logo:
            payload["attachments"] = [logo]
        status, body = _request("POST", "/mail/send", key, payload)
        if status not in (200, 201, 202):
            raise RuntimeError("SendGrid rejected the test message: HTTP %s %s" % (status, body))
        sent.append({"subject": subject, "sendgrid_status": status})
    return {"sent": len(sent), "recipients": len(to), "messages": sent}


def handler(event, context):
    secret = _secret()
    key = secret["SENDGRID_API_KEY"]
    if isinstance(event, dict) and event.get("selftest"):
        status, body = _request("GET", "/scopes", key)
        scopes = json.loads(body).get("scopes", []) if status == 200 else []
        return {"sendgrid_status": status, "can_send_mail": "mail.send" in scopes,
                "from_configured": bool(secret.get("SENDGRID_FROM_EMAIL")), "recipients": len(_recipients(secret)),
                "logo_found": bool(_logo_bytes())}

    sender = secret["SENDGRID_FROM_EMAIL"]
    to = _recipients(secret)
    logo = _logo_attachment()
    if isinstance(event, dict) and event.get("send_test"):
        return _send_test(key, sender, to, logo)
    sent = ignored = 0
    for record in event.get("Records", []):
        try:
            alarm = json.loads(record["Sns"]["Message"])
            kind = classify(alarm["AlarmName"], alarm.get("NewStateValue", ""))
        except (ValueError, KeyError, TypeError):
            kind = None
        if kind is None:
            ignored += 1
            continue
        if not to:
            print("no recipients configured; %s not sent" % kind[1])  # set notification_recipients or SENDGRID_TO_EMAILS
            continue
        subject, text, html = render(alarm, *kind, has_logo=bool(logo))
        payload = {
            "personalizations": [{"to": [{"email": a}]} for a in to],
            "from": {"email": sender},
            "subject": subject,
            "content": [{"type": "text/plain", "value": text}, {"type": "text/html", "value": html}],
        }
        if logo:
            payload["attachments"] = [logo]
        status, body = _request("POST", "/mail/send", key, payload)
        if status not in (200, 201, 202):
            raise RuntimeError("SendGrid rejected the message: HTTP %s %s" % (status, body))
        sent += 1
    print("sent %d notification(s) to %d recipient(s); ignored %d other alarm message(s)" % (sent, len(to), ignored))
    return {"sent": sent, "ignored": ignored}
