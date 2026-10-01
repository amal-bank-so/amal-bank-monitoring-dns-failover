#!/usr/bin/env bash
# Website checks for amalbank.so, run from a network that can reach the domain.
#
#   check_web.sh                      what is served now (legacy redirect service today)
#   check_web.sh --pin <ip>           send every request to <ip> but with the real Host name,
#                                     e.g. a CloudFront edge IP of the new distribution BEFORE
#                                     the apex/www records are switched
#   check_web.sh --cloudfront d1.cloudfront.net   same, resolving the edge IP for you
#
# Prints, per URL: the status line, Location, Cache-Control and Server headers (redirects are
# NOT followed), then the TLS certificate details. Save the output as evidence, and use the
# first run (before delegation) to record the legacy redirect behaviour that the stack's
# redirect_status_code / redirect_preserve_path / redirect_preserve_query must reproduce.
set -u
PIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pin) PIN="$2"; shift 2 ;;
    --cloudfront) PIN="$(getent ahostsv4 "$2" | awk 'NR==1{print $1}')"; [ -n "$PIN" ] || { echo "cannot resolve $2" >&2; exit 2; }; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done

DOMAIN="amalbank.so"
curl_pin() { # host -> curl args that pin the host to $PIN on both ports
  [ -n "$PIN" ] && printf -- '--resolve %s:80:%s --resolve %s:443:%s' "$1" "$PIN" "$1" "$PIN"
}
probe() { # label method url
  local label="$1" method="$2" url="$3" host
  host="$(printf '%s' "$url" | sed -E 's#^[a-z]+://([^/:?]+).*#\1#')"
  echo "== $label: $method $url"
  # shellcheck disable=SC2046
  curl -sS -m 20 -o /dev/null -X "$method" --max-redirs 0 $(curl_pin "$host") \
       -D - -w 'curl_exit=%{exitcode} http_code=%{http_code} remote_ip=%{remote_ip}\n' "$url" 2>&1 \
    | tr -d '\r' | grep -i -E '^(HTTP/|location:|cache-control:|server:|via:|x-cache:|curl_exit|curl:)' | sed 's/^/   /'
}
tls() { # host
  local host="$1" target="${PIN:-$1}"
  echo "== TLS $host (SNI $host, connecting to $target:443)"
  echo | openssl s_client -connect "$target:443" -servername "$host" -verify_hostname "$host" 2>/dev/null \
    | grep -E '^(subject=|issuer=|Verify return code|Verification)' | sed 's/^/   /'
  echo | openssl s_client -connect "$target:443" -servername "$host" 2>/dev/null \
    | openssl x509 -noout -dates -ext subjectAltName 2>/dev/null | sed 's/^/   /'
}

echo "# amalbank.so web check $(date -u +%FT%TZ)${PIN:+ (pinned to $PIN)}"
probe "apex http"            GET  "http://$DOMAIN/"
probe "apex https"           GET  "https://$DOMAIN/"
probe "www http"             GET  "http://www.$DOMAIN/"
probe "www https"            GET  "https://www.$DOMAIN/"
probe "path+query http"      GET  "http://$DOMAIN/some/path?x=1&y=a%20b&z"
probe "path+query https"     GET  "https://www.$DOMAIN/some/path?x=1&y=a%20b&z"
probe "HEAD https"           HEAD "https://$DOMAIN/"
probe "POST http"            POST "http://$DOMAIN/form"
if [ -z "$PIN" ]; then
  echo "== names that must NOT exist today (expect resolution failure)"
  for h in "zz-nonexistent-91827.$DOMAIN" "a.b.$DOMAIN"; do
    printf '   %s -> ' "$h"; getent hosts "$h" >/dev/null 2>&1 && echo "RESOLVES (unexpected)" || echo "no such name (expected)"
  done
fi
tls "$DOMAIN"
tls "www.$DOMAIN"
