# API Reference — Inventory Data

## Internal data — `get_material_status` (UC function via UC Functions MCP)

`jywu.jywu_mfg_agent.get_material_status(material)` summarizes on-hand inventory, the cheapest
recent quote, and next-month demand from the tables below (returns the **aggregate** on-hand
figure). Safety-stock/reorder policy comes from the **AI Search Playbook** over
`procurement_doc_chunks_index`. Per-warehouse `days_of_supply` lives in `inventory_levels`.

### `inventory_levels`

Current warehouse stock levels for PE resin materials.

| Column | Type | Description |
|--------|------|-------------|
| snapshot_date | date | Snapshot date of the reading |
| material | string | HDPE, LDPE, or PP |
| warehouse | string | Houston or Chicago |
| quantity_tons | int | Current stock on hand |
| safety_stock_tons | int | Minimum acceptable stock level |
| days_of_supply | int | Days until stockout at current consumption rate |
| reorder_point_tons | int | On-hand level that triggers a replenishment PO |

**Materials:** HDPE (Packaging Film), LDPE (Shrink Wrap), PP (Containers)
**Warehouses:** Houston, Chicago

### `production_demand`

Upcoming material requirements by production month.

| Column | Type | Description |
|--------|------|-------------|
| production_month | string | Production month (YYYY-MM) |
| material | string | HDPE, LDPE, or PP |
| required_tons | int | Required quantity for production |
| product_line | string | Packaging Film, Shrink Wrap, or Containers |
| priority | string | Production priority level |
| confidence_pct | int | Demand-forecast confidence |

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

### Illustrative questions (answered from the tables above)

```
"Show all inventory levels with safety stock status"
"What materials are below safety stock?"
"What is the days of supply for HDPE across all warehouses?"
"What is the production demand for April?"
"What are the cheapest quotes for HDPE with delivery under 2 weeks?"
```
(These map to the `inventory_levels` / `production_demand` / `supplier_quotes` columns; the agent's
`get_material_status` tool returns the summary form. Direct table Q&A would need a Genie/table tool.)
