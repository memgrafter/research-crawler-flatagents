

Docs:
https://openrouter.ai/docs/api/reference/limits


Working example:
```
curl  -H "authorization: Bearer $OPENROUTER_API_KEY" https://openrouter.ai/api/v1/key | jq .
```


## Output

I have no idea how to read this.

{
  "data": {
    "label": "sk-or-v1-726...f5c",
    "is_management_key": false,
    "is_provisioning_key": false,
    "limit": 1,
    "limit_reset": null,
    "limit_remaining": 0.8246643901,
    "include_byok_in_limit": true,
    "usage": 0.1753356099,
    "usage_daily": 0,
    "usage_weekly": 0,
    "usage_monthly": 0,
    "byok_usage": 0,
    "byok_usage_daily": 0,
    "byok_usage_weekly": 0,
    "byok_usage_monthly": 0,
    "is_free_tier": false,
    "expires_at": null,
    "rate_limit": {
      "requests": -1,
      "interval": "10s",
      "note": "This field is deprecated and safe to ignore."
    }
  }
}
