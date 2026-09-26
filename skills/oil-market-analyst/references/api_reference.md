# API Reference — EIA Crude Oil MCP Server

**App:** `mcp-jywu-eia-oil`
**Endpoint:** `(set after deployment — see app.yaml env vars)`

## Tools

### `get_current_oil_price`

Get the most recent crude oil spot price.

**Arguments:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `product` | string | "WTI" | "WTI" or "Brent" |

**Response:**
```json
{
  "date": "2026-03-23",
  "product": "WTI Crude Oil",
  "price_usd_per_barrel": 89.33,
  "description": "Cushing, OK WTI Spot Price FOB (Dollars per Barrel)"
}
```

### `get_oil_price_history`

Get historical crude oil spot prices.

**Arguments:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `product` | string | "WTI" | "WTI" or "Brent" |
| `days` | int | 30 | Number of trading days (1–365). Ignored if start_date set. |
| `start_date` | string | null | Start date YYYY-MM-DD |
| `end_date` | string | null | End date YYYY-MM-DD |

**Response:**
```json
{
  "product": "WTI",
  "count": 30,
  "date_range": {"from": "2026-02-21", "to": "2026-03-23"},
  "summary": {"latest": 89.33, "min": 82.10, "max": 91.50, "avg": 86.72},
  "prices": [
    {"date": "2026-03-23", "product": "WTI Crude Oil", "price_usd_per_barrel": 89.33, "description": "..."},
    ...
  ]
}
```

### `get_oil_price_trend`

Analyze crude oil price trend over a recent period.

**Arguments:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `product` | string | "WTI" | "WTI" or "Brent" |
| `days` | int | 30 | Trading days to analyze (1–90) |

**Response:**
```json
{
  "product": "WTI",
  "period_days": 30,
  "date_range": {"from": "2026-02-21", "to": "2026-03-23"},
  "latest_price": 89.33,
  "oldest_price": 85.70,
  "price_change": 3.63,
  "pct_change": 4.24,
  "direction": "rising",
  "daily_volatility_pct": 1.32,
  "high": 91.50,
  "low": 82.10
}
```

**Direction logic:**
- `pct_change > 3` → "rising"
- `pct_change < -3` → "falling"
- otherwise → "stable"

## Data Source

U.S. Energy Information Administration (EIA) API v2
- Endpoint: `https://api.eia.gov/v2/petroleum/pri/spt/data/`
- Frequency: Daily
- Products: WTI (EPCWTI), Brent (EPCBRENT)
