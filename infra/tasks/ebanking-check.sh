#!/usr/bin/env bash
# Read-only check of both e-banking endpoints: the records served by Route 53, the health check of each endpoint
# (checker observations and the CloudWatch status), the four alarms and their recent state changes.
# Runs with the read-only role amal-bank-dns-failover-readonly; it changes nothing.
set -euo pipefail

ZONE_ID="${EBANKING_ZONE_ID:-Z01112481OCSOFIT54YT1}"
DAYS="${1:-14}"
export AWS_DEFAULT_REGION=us-east-1

echo "== Records in zone $ZONE_ID (A records)"
aws route53 list-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --query "ResourceRecordSets[?Type=='A'].{Name:Name,Failover:Failover,Id:SetIdentifier,TTL:TTL,IP:ResourceRecords[0].Value,HealthCheck:HealthCheckId}" \
  --output table

echo
echo "== Health checks behind those records"
for hc in $(aws route53 list-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --query "ResourceRecordSets[?HealthCheckId!=null].HealthCheckId" --output text); do
  aws route53 get-health-check --health-check-id "$hc" \
    --query "HealthCheck.{Id:Id,IP:HealthCheckConfig.IPAddress,Port:HealthCheckConfig.Port,Type:HealthCheckConfig.Type,Interval:HealthCheckConfig.RequestInterval,Threshold:HealthCheckConfig.FailureThreshold}" \
    --output table
  echo "-- checker observations for $hc"
  aws route53 get-health-check-status --health-check-id "$hc" \
    --query "HealthCheckObservations[].{Region:Region,IP:IPAddress,Status:StatusReport.Status}" --output text \
    | awk '{n++; if ($0 ~ /Success/) ok++; else bad[$0]=1} END {printf "%d of %d checker locations report Success\n", ok, n; for (b in bad) print "  not success: " b}'
  echo "-- CloudWatch HealthCheckStatus, last 15 minutes (1 = healthy)"
  aws cloudwatch get-metric-statistics --namespace AWS/Route53 --metric-name HealthCheckStatus \
    --dimensions Name=HealthCheckId,Value="$hc" \
    --start-time "$(date -u -d '15 minutes ago' +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
    --period 60 --statistics Minimum \
    --query "sort_by(Datapoints,&Timestamp)[].Minimum" --output text
  echo
done

echo "== Alarms"
aws cloudwatch describe-alarms --alarm-name-prefix amal-dns-ebanking --alarm-types CompositeAlarm MetricAlarm \
  --query "[CompositeAlarms[].{Name:AlarmName,State:StateValue,Since:StateUpdatedTimestamp,Reason:StateReason}, MetricAlarms[].{Name:AlarmName,State:StateValue,Since:StateUpdatedTimestamp,Reason:StateReason}][]" \
  --output table

echo
echo "== State changes in the last $DAYS days (any line here means an alarm changed state)"
for a in $(aws cloudwatch describe-alarms --alarm-name-prefix amal-dns-ebanking --alarm-types CompositeAlarm MetricAlarm \
  --query "[CompositeAlarms[].AlarmName, MetricAlarms[].AlarmName][]" --output text); do
  aws cloudwatch describe-alarm-history --alarm-name "$a" --history-item-type StateUpdate \
    --start-date "$(date -u -d "$DAYS days ago" +%FT%TZ)" --end-date "$(date -u +%FT%TZ)" \
    --query "AlarmHistoryItems[].[Timestamp,AlarmName,HistorySummary]" --output text
done
echo "(end of state changes)"
