---
name: inventory-monitor
description: Monitor PE resin inventory levels, detect stockout risks, and recommend reorder actions. Use when the user asks about current stock, safety stock alerts, days of supply, warehouse levels, or which materials need reordering. Triggers include "inventory status", "what's our stock", "are we running low", "reorder alert", "days of supply".
---

# Inventory Monitor Skill

Monitor inventory levels, detect stockout risks, and recommend reorder actions for PE resin materials (HDPE, LDPE, PP).

## Workflow Overview

1. **Query inventory levels** → Current stock, safety stock, days of supply
2. **Check production demand** → What's needed and when
3. **Assess risk** → Apply alert thresholds
4. **Recommend reorder** → Quantity, supplier, urgency

## Step 1: Query Inventory Data (Genie Space)

Ask the Genie Space:

| Question | What You Learn |
|----------|---------------|
| "Show all inventory levels with safety stock" | Full picture across all materials and warehouses |
| "What materials are below safety stock?" | CRITICAL items needing immediate action |
| "What is the days of supply for [MATERIAL]?" | How long until stockout at current consumption |
| "Show inventory for [WAREHOUSE]" | Site-specific stock levels |

### Key Columns in `inventory_levels`

| Column | Description |
|--------|-------------|
| `material` | HDPE, LDPE, or PP |
| `warehouse` | Houston or Chicago |
| `quantity_tons` | Current stock on hand |
| `safety_stock_tons` | Minimum acceptable stock level |
| `days_of_supply` | Days until stockout at current consumption rate |

## Step 2: Check Production Demand

Ask the Genie Space:
- "What is the production demand for next month?"
- "What materials do we need for [MONTH] production?"

### Key Columns in `production_demand`

| Column | Description |
|--------|-------------|
| `material` | HDPE, LDPE, or PP |
| `month` | Production month (YYYY-MM) |
| `quantity_tons_needed` | How much is required |
| `product_line` | Packaging Film, Shrink Wrap, or Containers |
| `priority` | Production priority level |

## Step 3: Apply Alert Thresholds

| Level | Condition | Action |
|-------|-----------|--------|
| **CRITICAL** | `quantity_tons < safety_stock_tons` | Order immediately — production at risk |
| **WARNING** | `days_of_supply < 14` | Start sourcing — place order within days |
| **WATCH** | `days_of_supply < 30` | Monitor — plan upcoming purchase |
| **OK** | `days_of_supply >= 30` | No action needed |

## Step 4: Recommend Reorder

When a material is CRITICAL or WARNING:

1. **Calculate quantity needed**:
   ```
   reorder_qty = (safety_stock_tons × 1.5) - quantity_tons + next_month_demand
   ```

2. **Check supplier options** — Ask Genie: "What are the active quotes for [MATERIAL] sorted by price?"

3. **Check lead time feasibility** — If `days_of_supply < lead_time_days` for the cheapest supplier, flag as URGENT and recommend a faster (possibly more expensive) supplier.

4. **Round up to minimum order** — Ensure quantity meets supplier's `min_order_tons`.

## Output Format

### Inventory Overview

Present as a status table:

```
Material | Warehouse | Stock   | Safety Stock | Days of Supply | Status
---------|-----------|---------|-------------|----------------|----------
HDPE     | Houston   | 120 ton | 100 ton      | 18             | ⚠ WARNING
HDPE     | Chicago   | 85 ton  | 100 ton      | 12             | 🔴 CRITICAL
LDPE     | Houston   | 200 ton | 150 ton      | 35             | ✅ OK
LDPE     | Chicago   | 160 ton | 150 ton      | 28             | 👀 WATCH
PP       | Houston   | 90 ton  | 80 ton       | 22             | 👀 WATCH
PP       | Chicago   | 95 ton  | 80 ton       | 32             | ✅ OK
```

### Reorder Recommendations

For any CRITICAL or WARNING items, provide:
1. **Material and warehouse** — what needs restocking
2. **Quantity** — how much to order
3. **Recommended supplier** — best price with acceptable lead time
4. **Urgency** — when the order must be placed to avoid stockout
5. **Estimated cost** — quantity × quoted price

Example:
> **🔴 CRITICAL: HDPE at Chicago — 85 tons (below 100 ton safety stock)**
> - Need: 115 tons (restore to 1.5× safety stock + April demand of 50 tons)
> - Best price: AsiaResin at $1,228/ton — but 30-day lead time exceeds 12 days of supply
> - **Recommended: PolySource at $1,305/ton, 10-day lead time** — order by end of week
> - Estimated cost: 125 tons × $1,305 = $163,125 (rounded to 125 ton minimum)
