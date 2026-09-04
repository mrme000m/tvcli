# Cloudflare API — curl recipes (same calls `scripts/cf.py` makes)

All calls use `Authorization: Bearer <token>` (read-all for GET, write-all
for mutating) against `https://api.cloudflare.com/client/v4`.
Account ID = `account-id` field of vault item `cloudflare-tunnels`
(folder `cloudflare`; see `browser-debug/secrets/VAULT_CONVENTIONS.md`).

```bash
# redacted credential check (cf.py resolves env first, vault fallback)
./bin/cf.sh auth-status

ACCT="$CF_ACCOUNT_ID"   # env-first; vault fallback is inside cf.py (never echoed)
R="$read_all_token"; W="$write_all_token"   # from vault fields, never commit

# zones — find the indevs domain
curl -s -H "Authorization: Bearer $R" \
  "$API/zones?per_page=50" | jq -r '.result[] | "\(.id) \(.name) \(.status)"'

# tunnels
curl -s -H "Authorization: Bearer $R" \
  "$API/accounts/$ACCT/cfd_tunnel?per_page=50" | jq '.result[] | {id,name,status}'

# connectors ride on the tunnel object (no separate endpoint)
curl -s -H "Authorization: Bearer $R" \
  "$API/accounts/$ACCT/cfd_tunnel?per_page=50" | jq '.result[] | {name, status, connections}'

# create tunnel (remotely-managed)
curl -s -X POST -H "Authorization: Bearer $W" -H 'Content-Type: application/json' \
  "$API/accounts/$ACCT/cfd_tunnel" -d '{"name":"codespace-web"}' | jq .

# set ingress (hostname on the indevs zone → local service)
curl -s -X PUT -H "Authorization: Bearer $W" -H 'Content-Type: application/json' \
  "$API/accounts/$ACCT/cfd_tunnel/$TUNNEL_ID/configurations" \
  -d '{"config":{"ingress":[{"hostname":"app.00m.indevs.in","service":"http://localhost:3081"},{"service":"http_status:404"}]}}' | jq .

# DNS: CNAME hostname → <tunnel-id>.cfargotunnel.com (proxied)
curl -s -X POST -H "Authorization: Bearer $W" -H 'Content-Type: application/json' \
  "$API/zones/$ZONE_ID/dns_records" \
  -d '{"type":"CNAME","name":"app.00m.indevs.in","content":"'$TUNNEL_ID'.cfargotunnel.com","proxied":true,"ttl":1}' | jq .

# delete (cascade drops connections/config)
curl -s -X DELETE -H "Authorization: Bearer $W" \
  "$API/accounts/$ACCT/cfd_tunnel/$TUNNEL_ID?cascade=true" | jq .
```
