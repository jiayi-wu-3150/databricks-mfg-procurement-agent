---
name: oil-market-analyst
description: Analyze crude oil prices and market trends to inform PE resin procurement timing. Use when the user asks about current oil prices (WTI/Brent), price trends, volatility, or how oil markets affect material costs. Triggers include "what's the oil price", "is oil rising or falling", "oil price trend", "market conditions for buying resin".
---

# Oil Market Analyst Skill

Analyze crude oil prices and market trends to inform procurement timing decisions for PE resin (HDPE, LDPE, PP) purchasing.

## Workflow Overview

1. **Identify what the user needs** → Current price, historical data, or trend analysis
2. **Call EIA MCP tool** → Get oil price data
3. **Interpret for procurement** → Translate oil signals into buy/wait guidance

## Step 1: Select the Right Tool

| User Intent | Tool | Key Arguments |
|-------------|------|---------------|
| "What's the oil price?" | `get_current_oil_price` | `product`: "WTI" (default) or "Brent" |
| "Show me oil prices for last month" | `get_oil_price_history` | `product`, `days` (1–365), or `start_date`/`end_date` (YYYY-MM-DD) |
| "Is oil going up or down?" | `get_oil_price_trend` | `product`, `days` (1–90) |

Default to **WTI** unless the user specifies Brent.

## Step 2: Call the EIA oil tools

**Source:** UC MCP Service `jywu.jywu_mfg_agent.eia_oil` (backed by the `mcp-jywu-eia-oil`
FastMCP app; consumed over the internal `/ai-gateway/mcp-services/…` path). Tools:
`get_current_oil_price`, `get_oil_price_history`, `get_oil_price_trend`.

### get_current_oil_price

```
get_current_oil_price(product="WTI")
```

Returns: `date`, `product`, `price_usd_per_barrel`, `description`

### get_oil_price_history

```
get_oil_price_history(product="WTI", days=30)
get_oil_price_history(product="Brent", start_date="2026-03-01", end_date="2026-03-31")
```

Returns: `prices` (list), `summary` (latest, min, max, avg), `date_range`, `count`

### get_oil_price_trend

```
get_oil_price_trend(product="WTI", days=30)
```

Returns: `direction` (rising/falling/stable), `pct_change`, `price_change`, `daily_volatility_pct`, `high`, `low`, `latest_price`, `oldest_price`

## Step 3: Interpret Results for Procurement

### Trend Direction

| Direction | Meaning | Procurement Guidance |
|-----------|---------|---------------------|
| `rising` (>3% increase) | Oil costs going up | Resin prices will follow in 2–4 weeks. **Favor buying now.** |
| `falling` (>3% decrease) | Oil costs dropping | Resin prices may soften. **Waiting could save money.** |
| `stable` (within ±3%) | No strong signal | **Decide based on inventory needs and supplier quotes.** |

### Volatility

| `daily_volatility_pct` | Level | Implication |
|------------------------|-------|-------------|
| <1% | Low | Prices predictable, less urgency |
| 1–2% | Normal | Standard market conditions |
| >2% | High | Prices uncertain — consider locking in prices sooner |

### Oil-to-Resin Relationship

- PE resins (HDPE, LDPE, PP) are petroleum derivatives
- Crude oil is a **leading indicator** — resin prices typically lag oil by 2–4 weeks
- A **$10/barrel move in WTI ≈ $50–80/ton change** in PE resin prices

## Output Format

Present results conversationally, always including:
1. **Current price** with date
2. **Trend** direction and magnitude
3. **Procurement implication** — what this means for buying resin

Example:
> WTI crude is at **$89.33/barrel** (Mar 23). Over the past 30 days, oil has been **rising (+4.2%)** with moderate volatility (1.3%). Since PE resin prices typically follow oil with a 2–4 week lag, **resin prices are likely heading up**. If you need to buy, locking in current supplier quotes would be prudent.
