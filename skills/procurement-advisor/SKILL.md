---
name: procurement-advisor
description: Comprehensive purchase decision framework combining supplier quotes, inventory, oil market data, and ML pricing predictions. Use when the user asks whether to buy a material, evaluates a deal or supplier quote, compares suppliers, or needs a procurement recommendation. Triggers include "should I buy", "evaluate this quote", "compare suppliers for HDPE", "is this a good deal", "purchase recommendation".
---

# Procurement Advisor Skill

Decision framework for PE resin purchasing that combines all 3 MCP data sources into a buy/wait recommendation.

## Workflow Overview

1. **Check internal data** → on-hand inventory, cheapest recent quote, next-month demand (`get_material_status` UC function)
2. **Check oil market** → current price and trend (EIA oil tools)
3. **Evaluate the deal** → ML prediction on deal quality (`predict_deal_quality`)
4. **Check policy** → approval thresholds, preferred suppliers, price-lock/MOQ (AI Search playbook)
5. **Synthesize recommendation** → combine all signals

## Step 1: Check Internal Data (`get_material_status`)

Call the UC function tool (governed, exposed via the UC Functions MCP `/api/2.0/mcp/functions/jywu/jywu_mfg_agent`):

```
get_material_status(material="HDPE")
```

Returns a concise summary: **total on-hand inventory (tons)**, **cheapest recent supplier quote** (supplier + $/ton), and **next-month demand (tons)**.

For company policy — approval thresholds, preferred suppliers, price-lock/MOQ clauses, safety-stock rules — retrieve from the **AI Search "Procurement Playbook"** (managed MCP over `procurement_doc_chunks_index`).

### Underlying tables (for reference)

`get_material_status` reads these UC tables in `jywu.jywu_mfg_agent`:

| Table | Key Columns |
|-------|-------------|
| `supplier_quotes` | quote_date, supplier, material, price_usd_ton, lead_time_days, min_order_tons, valid_until |
| `inventory_levels` | snapshot_date, material, warehouse, quantity_tons, safety_stock_tons, days_of_supply, reorder_point_tons |
| `production_demand` | production_month, material, required_tons, product_line, priority, confidence_pct |
| `purchase_history` | purchase_date, material, supplier, quantity_tons, price_usd_ton, oil_price_at_purchase, price_vs_30d_avg_pct, outcome (Great Buy / Good Buy / Neutral / Overpaid) |

### Historical Price Context

`purchase_history.price_vs_30d_avg_pct` is the price vs. the trailing 30-day average. For a **new** proposed purchase, compute it the same way:
```
price_vs_30d_avg_pct = ((proposed_price - avg_price) / avg_price) * 100
```
This is a required input for the ML model in Step 3.

## Step 2: Check Oil Market (EIA oil tools)

**Source:** UC MCP Service `jywu.jywu_mfg_agent.eia_oil`

```
get_current_oil_price(product="WTI")
get_oil_price_trend(product="WTI", days=30)
```

Extract:
- `latest_price` — current WTI price (needed for ML model)
- `direction` — rising / falling / stable
- `pct_change` — magnitude of trend

## Step 3: Evaluate the Deal (`predict_deal_quality`)

**Source:** UC MCP Service `jywu.jywu_mfg_agent.pricing` (backed by the `mcp-jywu-pricing` app → Model Serving endpoint `jywu-pricing-model`)

```
predict_deal_quality(
    material="HDPE",
    price_usd_ton=1300.0,
    oil_price=89.33,
    quantity_tons=250,
    price_vs_30d_avg_pct=-2.5
)
```

| Argument | Source |
|----------|--------|
| `material` | User's request ("HDPE", "LDPE", or "PP") |
| `price_usd_ton` | From supplier quote or user input |
| `oil_price` | From Step 2 (`get_current_oil_price`) |
| `quantity_tons` | From production demand or user input |
| `price_vs_30d_avg_pct` | Calculated from purchase history in Step 1 |

Returns: `prediction` ("Good Deal" or "Bad Deal") and `recommendation` ("BUY" or "WAIT")

## Step 4: Synthesize Recommendation

Combine all signals using this decision matrix:

| Inventory Status | Oil Trend | ML Prediction | Recommendation |
|-----------------|-----------|---------------|----------------|
| Below safety stock | Any | Any | **BUY NOW** — can't risk stockout |
| <10 days supply | Rising | Good Deal | **BUY NOW** — prices going up, deal is good |
| <10 days supply | Falling | Bad Deal | **BUY MINIMUM** — cover near-term need, wait for better price |
| >20 days supply | Rising | Good Deal | **BUY** — lock in before prices rise |
| >20 days supply | Falling | Bad Deal | **WAIT** — prices dropping, no urgency |
| >20 days supply | Stable | Good Deal | **BUY** — fair price, adequate timing |
| >20 days supply | Stable | Bad Deal | **WAIT** — price is above market |

## Output Format

Always include in the recommendation:

1. **Action**: BUY NOW / BUY / BUY MINIMUM / WAIT
2. **Supplier**: Best option (lowest price within acceptable lead time)
3. **Quantity**: Based on production demand + safety stock replenishment
4. **Urgency**: Based on days of supply vs supplier lead time
5. **Price context**: How quote compares to historical purchases and ML assessment
6. **Market outlook**: Oil trend and what it means for near-term resin prices

Example:
> **Recommendation: BUY — 200 tons of HDPE from AsiaResin at $1,228/ton**
>
> - **Inventory**: Houston has 12 days of supply (WARNING) — need to reorder
> - **Oil market**: WTI at $89.33, trending up +4.2% over 30 days — resin prices likely to follow
> - **ML assessment**: Good Deal — price is 2.5% below 30-day average
> - **Supplier**: AsiaResin offers lowest price ($1,228/ton) but 30-day lead time. If urgent, PolySource at $1,305/ton delivers in 10 days.
> - **Action**: Place order this week. Oil trend suggests prices will rise further.
