# API Reference — Inventory Data

## Genie Space (Managed MCP)

Natural language SQL over inventory and demand tables in Unity Catalog (`jywu.agent_mcp`).

### `inventory_levels`

Current warehouse stock levels for PE resin materials.

| Column | Type | Description |
|--------|------|-------------|
| material | string | HDPE, LDPE, or PP |
| warehouse | string | Houston or Chicago |
| quantity_tons | double | Current stock on hand |
| safety_stock_tons | double | Minimum acceptable stock level |
| days_of_supply | int | Days until stockout at current consumption rate |
| last_updated | string | Last update timestamp |

**Materials:** HDPE (Packaging Film), LDPE (Shrink Wrap), PP (Containers)
**Warehouses:** Houston, Chicago

### `production_demand`

Upcoming material requirements by production month.

| Column | Type | Description |
|--------|------|-------------|
| material | string | HDPE, LDPE, or PP |
| month | string | Production month (YYYY-MM) |
| quantity_tons_needed | double | Required quantity for production |
| product_line | string | Packaging Film, Shrink Wrap, or Containers |
| priority | string | Production priority level |

### `supplier_quotes`

Used for reorder recommendations — finding suppliers with stock and lead times.

| Column | Type | Description |
|--------|------|-------------|
| supplier | string | ChemCorp, PolySource, AsiaResin, EuroChem |
| material | string | HDPE, LDPE, or PP |
| price_usd_ton | double | Quoted price |
| lead_time_days | int | Delivery lead time in days |
| min_order_tons | int | Minimum order quantity |
| valid_until | string | Quote expiration date |

### Useful Genie Queries

```
"Show all inventory levels with safety stock status"
"What materials are below safety stock?"
"What is the days of supply for HDPE across all warehouses?"
"What is the production demand for April?"
"What are the cheapest quotes for HDPE with delivery under 2 weeks?"
```
