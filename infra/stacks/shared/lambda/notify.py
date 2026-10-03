"""Email the three e-banking notifications through SendGrid.

Subscribed to the SNS topic that every CloudWatch alarm publishes to. Only these three alarm events send an email; every
other alarm message is ignored (the alarms stay visible in the CloudWatch console):

    HIGH      Failover from Primary to Secondary   alarm <prefix>-ebanking-failover          -> ALARM
    HIGH      Primary is Back                      alarm <prefix>-ebanking-primary-unhealthy -> OK
    CRITICAL  E-Banking is Down                    alarm <prefix>-ebanking-both-unhealthy    -> ALARM

The SendGrid API key and the sender address are read from AWS Secrets Manager at run time (never from the environment or the
repository):

    {"SENDGRID_API_KEY": "...", "SENDGRID_FROM_EMAIL": "...", "SENDGRID_TO_EMAILS": "a@x.com,b@y.com"}   # last field optional

Recipients are the RECIPIENTS environment variable (comma separated, from Terraform) plus the optional SENDGRID_TO_EMAILS
secret field, so recipients can be added without a deployment. Each recipient gets their own personalization, so addresses
are not exposed to each other. A failed send raises, so SNS retries the delivery. If a logo.png is bundled next to this file it
is embedded in the email header.

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
NAVY = "#0a2260"
SEVERITY_COLOR = {"CRITICAL": "#b71c1c", "HIGH": "#e65100"}
_secret_cache = None


def classify(alarm_name, state):
    """-> (severity, title, explanation) for one of the three notifications, else None."""
    primary = os.environ.get("PRIMARY_LABEL", "Primary")
    secondary = os.environ.get("SECONDARY_LABEL", "Secondary")
    if alarm_name.endswith("-ebanking-failover") and state == "ALARM":
        return ("HIGH", "Failover from %s to %s" % (primary, secondary),
                "The primary endpoint is failing its health check and the secondary is healthy. Route 53 is now answering "
                "with the secondary, so e-banking traffic is being served through it.")
    if alarm_name.endswith("-ebanking-primary-unhealthy") and state == "OK":
        return ("HIGH", "%s is Back" % primary,
                "The primary endpoint is passing its health check again. Route 53 answers with the primary again.")
    if alarm_name.endswith("-ebanking-both-unhealthy") and state == "ALARM":
        return ("CRITICAL", "E-Banking is Down",
                "Both the primary and the secondary endpoints are failing their health checks. Route 53 keeps answering with "
                "the primary because neither is healthy, so customers may be unable to reach e-banking. Investigate now.")
    return None


def _secret():
    global _secret_cache
    if _secret_cache is None:
        arn = os.environ["SECRET_ARN"]
        client = boto3.client("secretsmanager", region_name=arn.split(":")[3])  # the secret may live in another region
        raw = json.loads(client.get_secret_value(SecretId=arn)["SecretString"])
        _secret_cache = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}  # tolerate stray spaces
    return _secret_cache


def _split(text):
    return [a.strip() for a in (text or "").replace(";", ",").split(",") if a.strip()]


def _recipients(secret):
    seen, out = set(), []
    for a in _split(os.environ.get("RECIPIENTS")) + _split(secret.get("SENDGRID_TO_EMAILS")):
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


def _logo_attachment():
    """The bank logo as an inline (CID) attachment, or None when no logo.png is bundled."""
    if not os.path.exists(LOGO_PATH):
        return None
    import base64
    with open(LOGO_PATH, "rb") as fh:
        return {"content": base64.b64encode(fh.read()).decode(), "type": "image/png", "filename": "amal-bank-logo.png",
                "disposition": "inline", "content_id": LOGO_CID}


def render(alarm, severity, title, explanation, has_logo=False):
    """-> (subject, text, html) for one notification. alarm is the CloudWatch alarm JSON from the SNS message."""
    name = alarm["AlarmName"]
    link = "https://console.aws.amazon.com/cloudwatch/home?region=%s#alarmsV2:alarm/%s" % (
        os.environ.get("CONSOLE_REGION", "us-east-1"), name)
    rows = [
        ("Event", "%s - %s" % (severity, title)), ("What happened", explanation),
        ("Alarm", name), ("Detail", alarm.get("AlarmDescription") or "-"), ("Reason", alarm.get("NewStateReason") or "-"),
        ("Time", alarm.get("StateChangeTime") or "-"), ("Account", alarm.get("AWSAccountId") or "-"), ("Console", link),
    ]
    text = "\n".join("%s: %s" % r for r in rows)
    table = "<table cellpadding='5' style='font-size:14px'>" + "".join(
        "<tr><td valign='top'><b>%s</b></td><td>%s</td></tr>" % (k, v if k != "Console" else "<a href='%s'>Open in CloudWatch</a>" % v)
        for k, v in rows[1:]) + "</table>"
    head = ('<img src="cid:%s" alt="Amal Bank" width="110" style="display:block;margin:0 auto">' % LOGO_CID) if has_logo else (
        '<span style="color:#fff;font-size:22px;font-weight:bold;letter-spacing:1px">Amal Bank</span>')
    color = SEVERITY_COLOR[severity]
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;border:1px solid #d9dde8">'
        '<div style="background:%s;padding:16px;text-align:center">%s</div>'
        '<div style="background:%s;color:#fff;padding:10px 16px;font-size:16px;font-weight:bold">%s</div>'
        '<div style="padding:16px">%s</div>'
        '<div style="background:#f3f5fa;color:#667;padding:10px 16px;font-size:12px">Automated DNS monitoring notification '
        '(ebanking.amalbankso.com on AWS Route 53). Do not reply.</div></div>'
    ) % (NAVY, head, color, "%s - %s" % (severity, title), table)
    return "%s - %s" % (severity, title), text, html


def handler(event, context):
    secret = _secret()
    key = secret["SENDGRID_API_KEY"]
    if isinstance(event, dict) and event.get("selftest"):
        status, body = _request("GET", "/scopes", key)
        scopes = json.loads(body).get("scopes", []) if status == 200 else []
        return {"sendgrid_status": status, "can_send_mail": "mail.send" in scopes,
                "from_configured": bool(secret.get("SENDGRID_FROM_EMAIL")), "recipients": len(_recipients(secret))}

    sender = secret["SENDGRID_FROM_EMAIL"]
    to = _recipients(secret)
    logo = _logo_attachment()
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
            "subject": os.environ.get("SUBJECT_PREFIX", "") + subject,
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
