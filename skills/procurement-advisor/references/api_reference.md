# API Reference — Procurement Data Sources

## 1. Internal data — `get_material_status` (UC function via UC Functions MCP)

The UC function `jywu.jywu_mfg_agent.get_material_status(material)` summarizes the four
procurement tables below (returns total on-hand inventory, cheapest recent quote, next-month
demand). Company policy comes from the **AI Search Playbook** over `procurement_doc_chunks_index`.

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
| snapshot_date | date | Snapshot date of the reading |
| material | string | HDPE, LDPE, or PP |
| warehouse | string | Houston or Chicago |
| quantity_tons | int | Current stock on hand |
| safety_stock_tons | int | Minimum acceptable stock level |
| days_of_supply | int | Days until stockout at current consumption |
| reorder_point_tons | int | On-hand level that triggers a replenishment PO |

#### `production_demand`
Upcoming material requirements by month.

| Column | Type | Description |
|--------|------|-------------|
| production_month | string | Production month (YYYY-MM) |
| material | string | HDPE, LDPE, or PP |
| required_tons | int | Required quantity |
| product_line | string | Packaging Film, Shrink Wrap, or Containers |
| priority | string | Production priority level |
| confidence_pct | int | Demand-forecast confidence |

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

**App:** `mcp-jywu-pricing` (FastMCP → Model Serving endpoint `jywu-pricing-model`)
**Governed as:** UC MCP Service `jywu.jywu_mfg_agent.pricing` (reached over `…/ai-gateway/mcp-services/…`)

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
