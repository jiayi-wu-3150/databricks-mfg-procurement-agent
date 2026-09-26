# API Reference — Procurement Data Sources

## 1. Genie Space (Managed MCP)

Natural language SQL over 4 procurement tables in Unity Catalog (`jywu.agent_mcp`).

### Tables

#### `supplier_quotes`
Active vendor pricing for PE resin materials.

| Column | Type | Description |
|--------|------|-------------|
| quote_date | string | Date the quote was issued (YYYY-MM-DD) |
| supplier | string | Vendor name (ChemCorp, PolySource, AsiaResin, EuroChem) |
| material | string | HDPE, LDPE, or PP |
| price_usd_ton | double | Quoted price in USD per ton |
| lead_time_days | int | Delivery lead time in days |
| min_order_tons | int | Minimum order quantity |
| valid_until | string | Quote expiration date (YYYY-MM-DD) |

#### `inventory_levels`
Current warehouse stock levels.

| Column | Type | Description |
|--------|------|-------------|
| material | string | HDPE, LDPE, or PP |
| warehouse | string | Houston or Chicago |
| quantity_tons | double | Current stock on hand |
| safety_stock_tons | double | Minimum acceptable stock level |
| days_of_supply | int | Days until stockout at current consumption |
| last_updated | string | Last update timestamp |

#### `production_demand`
Upcoming material requirements by month.

| Column | Type | Description |
|--------|------|-------------|
| material | string | HDPE, LDPE, or PP |
| month | string | Production month (YYYY-MM) |
| quantity_tons_needed | double | Required quantity |
| product_line | string | Packaging Film, Shrink Wrap, or Containers |
| priority | string | Production priority level |

#### `purchase_history`
Past procurement decisions with outcome labels.

| Column | Type | Description |
|--------|------|-------------|
| purchase_date | string | Date of purchase (YYYY-MM-DD) |
| supplier | string | Vendor name |
| material | string | HDPE, LDPE, or PP |
| price_usd_ton | double | Price paid |
| quantity_tons | int | Quantity purchased |
| oil_price_at_purchase | double | WTI crude price at time of purchase |
| price_vs_30d_avg_pct | double | Price vs 30-day average (%) |
| outcome | string | Great Buy, Good Buy, Neutral, or Overpaid |

### Suppliers

| Supplier | Lead Time | Min Order | Price Tendency |
|----------|-----------|-----------|----------------|
| ChemCorp | 12–16 days | 50 tons | Market price |
| PolySource | 8–12 days | 25 tons | Slight premium (+2%) |
| AsiaResin | 25–35 days | 100 tons | Discount (-4%) |
| EuroChem | 18–25 days | 75 tons | Near market (-1%) |

## 2. EIA MCP Server

See `oil-market-analyst/references/api_reference.md` for full tool documentation.

Key tools used:
- `get_current_oil_price(product="WTI")` → latest price
- `get_oil_price_trend(product="WTI", days=30)` → direction + volatility

## 3. Pricing MCP Server

**App:** `mcp-jywu-pricing`
**Endpoint:** `(set after deployment — see app.yaml env vars)`

### `predict_deal_quality`

Predict whether a proposed purchase is a good deal using the ML pricing model.

**Arguments:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `material` | string | required | "HDPE", "LDPE", or "PP" |
| `price_usd_ton` | float | required | Proposed price in USD per ton |
| `oil_price` | float | required | Current crude oil price (USD/barrel) |
| `quantity_tons` | int | 100 | Purchase quantity in tons |
| `price_vs_30d_avg_pct` | float | 0.0 | Price vs 30-day average (e.g., -3.5 = 3.5% below avg) |

**Response:**
```json
{
  "material": "HDPE",
  "price_usd_ton": 1300.0,
  "oil_price": 89.33,
  "quantity_tons": 250,
  "price_vs_30d_avg_pct": -2.5,
  "prediction": "Good Deal",
  "recommendation": "BUY"
}
```

**Model:** GradientBoostingClassifier trained on purchase history. Features: material_code, price_usd_ton, oil_price_at_purchase, quantity_tons, price_vs_30d_avg_pct.
