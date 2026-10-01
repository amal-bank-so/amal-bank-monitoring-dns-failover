# amalbank.so zone verification (pre, 20261001T192035Z)

Zone `Z02483903EQGQQFHLQUL3` in account `029288159395`. Results: PASS 15, FAIL 0, PENDING 1, INFO 3

| Area | Check | Result | Detail |
|---|---|---|---|
| zone | hosted zone | PASS | Z02483903EQGQQFHLQUL3, 4 name servers: ns-1337.awsdns-39.org, ns-1683.awsdns-18.co.uk, ns-377.awsdns-47.com, ns-524.awsdns-01.net |
| records | amalbank.so. A | PASS | ttl 60 values ['34.198.182.201'] |
| records | amalbank.so. MX | PASS | ttl 60 values ['5 amalbank-so.mail.protection.outlook.com.'] |
| records | amalbank.so. TXT | PASS | ttl 300 values ['"v=spf1 include:spf.protection.outlook.com -all"'] |
| records | autodiscover.amalbank.so. TXT | PASS | ttl 300 values ['"autodiscover.outlook.com"'] |
| records | www.amalbank.so. A | PASS | ttl 60 values ['34.198.182.201'] |
| apex | NS values | PASS | 4 name servers: ns-1337.awsdns-39.org., ns-1683.awsdns-18.co.uk., ns-377.awsdns-47.com., ns-524.awsdns-01.net. |
| apex | NS TTL | PASS | 900 (expected <= 900, so a rollback takes effect quickly) |
| apex | SOA present | PASS |  |
| records | _984c17a10f6adbb533a09ad195037191.amalbank.so. CNAME | INFO | ACM certificate validation record |
| records | _be4e608b1f25121f39d4ba7feddcf586.www.amalbank.so. CNAME | INFO | ACM certificate validation record |
| authoritative | TestDNSAnswer amalbank.so. A | PASS | ['34.198.182.201'] |
| authoritative | TestDNSAnswer amalbank.so. MX | PASS | ['5 amalbank-so.mail.protection.outlook.com.'] |
| authoritative | TestDNSAnswer amalbank.so. TXT | PASS | ['"v=spf1 include:spf.protection.outlook.com -all"'] |
| authoritative | TestDNSAnswer autodiscover.amalbank.so. TXT | PASS | ['"autodiscover.outlook.com"'] |
| authoritative | TestDNSAnswer www.amalbank.so. A | PASS | ['34.198.182.201'] |
| authoritative | unknown name behaviour | PASS | zz-430e6143a6.amalbank.so A -> NXDOMAIN (expected NXDOMAIN, matching the live zone which has no wildcard) |
| certificate | amalbank.so (amalbank.so, www.amalbank.so) | PENDING | PENDING_VALIDATION |
| cloudfront | redirect distribution | INFO | not created yet |
