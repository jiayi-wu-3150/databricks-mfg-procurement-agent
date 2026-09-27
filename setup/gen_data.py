"""Generate synthetic procurement data.

Self-contained: creates the schema, the 4 core UC tables, and the
`procurement_docs` table (AI Search "Procurement Playbook" source) via the
SQL warehouse using the Databricks SQL Statement Execution API.

Tiny, deterministic dataset (seed 42) — no Spark/cluster needed.

Run:  DATABRICKS_CONFIG_PROFILE=azure-demo uv run python -m setup.gen_data
"""

import random
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from setup.config import (
    CATALOG,
    SCHEMA,
    WAREHOUSE_ID,
    TABLE_SUPPLIER_QUOTES,
    TABLE_INVENTORY_LEVELS,
    TABLE_PRODUCTION_DEMAND,
    TABLE_PURCHASE_HISTORY,
    TABLE_PROCUREMENT_DOCS,
)

w = WorkspaceClient()


def run(sql: str, label: str = "") -> None:
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=sql, wait_timeout="50s"
    )
    state = resp.status.state
    # Poll if still running
    while state in (StatementState.PENDING, StatementState.RUNNING):
        resp = w.statement_execution.get_statement(resp.statement_id)
        state = resp.status.state
    if state != StatementState.SUCCEEDED:
        msg = resp.status.error.message if resp.status.error else "unknown error"
        print(f"  ✗ {label or sql[:60]}: {state} — {msg}")
        sys.exit(1)
    print(f"  ✓ {label or sql[:60]}")


def q(val) -> str:
    """SQL literal: escape single quotes for strings; pass through numbers."""
    if isinstance(val, str):
        return "'" + val.replace("'", "''") + "'"
    return str(val)


# --- Schema ------------------------------------------------------------------
print(f"Creating schema {CATALOG}.{SCHEMA} ...")
run(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}", f"schema {SCHEMA}")

# --- Constants (mirror the original notebook) --------------------------------
MATERIALS = ["HDPE", "LDPE", "PP"]
SUPPLIERS = {
    "ChemCorp": {"lead_time_range": (12, 16), "min_order": 50, "price_premium": 1.0},
    "PolySource": {"lead_time_range": (8, 12), "min_order": 25, "price_premium": 1.02},
    "AsiaResin": {"lead_time_range": (25, 35), "min_order": 100, "price_premium": 0.96},
    "EuroChem": {"lead_time_range": (18, 25), "min_order": 75, "price_premium": 0.99},
}
BASE_PRICES = {"HDPE": 1280, "LDPE": 1400, "PP": 970}
random.seed(42)

from datetime import datetime, timedelta

# --- 1. supplier_quotes ------------------------------------------------------
print(f"\n{TABLE_SUPPLIER_QUOTES}")
run(f"DROP TABLE IF EXISTS {TABLE_SUPPLIER_QUOTES}", "drop")
run(
    f"""CREATE TABLE {TABLE_SUPPLIER_QUOTES} (
        quote_date DATE, supplier STRING, material STRING,
        price_usd_ton DOUBLE, lead_time_days INT, min_order_tons INT, valid_until DATE)""",
    "create",
)
rows = []
base_date = datetime(2026, 3, 18)
for day_offset in range(5):
    quote_date = base_date + timedelta(days=day_offset)
    for material in MATERIALS:
        for supplier in random.sample(list(SUPPLIERS.keys()), k=random.randint(2, 3)):
            info = SUPPLIERS[supplier]
            price = round(BASE_PRICES[material] * info["price_premium"] * random.uniform(0.97, 1.03), 2)
            lead_time = random.randint(*info["lead_time_range"])
            valid_days = random.choice([5, 7, 10])
            rows.append(
                f"(DATE'{quote_date:%Y-%m-%d}',{q(supplier)},{q(material)},{price},"
                f"{lead_time},{info['min_order']},DATE'{(quote_date + timedelta(days=valid_days)):%Y-%m-%d}')"
            )
run(f"INSERT INTO {TABLE_SUPPLIER_QUOTES} VALUES\n" + ",\n".join(rows), f"insert {len(rows)} rows")

# --- 2. inventory_levels -----------------------------------------------------
print(f"\n{TABLE_INVENTORY_LEVELS}")
run(f"DROP TABLE IF EXISTS {TABLE_INVENTORY_LEVELS}", "drop")
run(
    f"""CREATE TABLE {TABLE_INVENTORY_LEVELS} (
        snapshot_date DATE, material STRING, warehouse STRING, quantity_tons INT,
        safety_stock_tons INT, days_of_supply INT, reorder_point_tons INT)""",
    "create",
)
inventory = [
    ("HDPE", "Houston", 320, 150, 18, 200), ("HDPE", "Chicago", 210, 100, 22, 130),
    ("LDPE", "Houston", 85, 100, 6, 120), ("LDPE", "Chicago", 140, 100, 12, 120),
    ("PP", "Houston", 175, 80, 25, 100), ("PP", "Chicago", 95, 60, 20, 75),
]
rows = [f"(DATE'2026-03-20',{q(m)},{q(wh)},{qty},{ss},{dos},{rp})" for m, wh, qty, ss, dos, rp in inventory]
run(f"INSERT INTO {TABLE_INVENTORY_LEVELS} VALUES\n" + ",\n".join(rows), f"insert {len(rows)} rows")

# --- 3. production_demand ----------------------------------------------------
print(f"\n{TABLE_PRODUCTION_DEMAND}")
run(f"DROP TABLE IF EXISTS {TABLE_PRODUCTION_DEMAND}", "drop")
run(
    f"""CREATE TABLE {TABLE_PRODUCTION_DEMAND} (
        production_month STRING, material STRING, required_tons INT,
        product_line STRING, priority STRING, confidence_pct INT)""",
    "create",
)
product_lines = {"HDPE": "Packaging Film", "LDPE": "Shrink Wrap", "PP": "Containers"}
demand_data = [
    ("2026-04", "HDPE", 400, "High", 95), ("2026-04", "LDPE", 250, "High", 90), ("2026-04", "PP", 150, "Medium", 85),
    ("2026-05", "HDPE", 450, "High", 80), ("2026-05", "LDPE", 200, "Medium", 70), ("2026-05", "PP", 180, "Medium", 65),
    ("2026-06", "HDPE", 380, "Medium", 60), ("2026-06", "LDPE", 220, "Medium", 55), ("2026-06", "PP", 160, "Low", 50),
]
rows = [f"({q(mo)},{q(mat)},{tons},{q(product_lines[mat])},{q(pri)},{conf})" for mo, mat, tons, pri, conf in demand_data]
run(f"INSERT INTO {TABLE_PRODUCTION_DEMAND} VALUES\n" + ",\n".join(rows), f"insert {len(rows)} rows")

# --- 4. purchase_history -----------------------------------------------------
print(f"\n{TABLE_PURCHASE_HISTORY}")
run(f"DROP TABLE IF EXISTS {TABLE_PURCHASE_HISTORY}", "drop")
run(
    f"""CREATE TABLE {TABLE_PURCHASE_HISTORY} (
        purchase_date DATE, material STRING, supplier STRING, quantity_tons INT,
        price_usd_ton DOUBLE, oil_price_at_purchase DOUBLE, price_vs_30d_avg_pct DOUBLE, outcome STRING)""",
    "create",
)
purchase_scenarios = [
    ("2025-04-10", "HDPE", "ChemCorp", 300, 1320, 76.50, 1.5, "Neutral"),
    ("2025-04-22", "LDPE", "PolySource", 150, 1410, 77.20, 0.8, "Neutral"),
    ("2025-05-08", "HDPE", "AsiaResin", 500, 1240, 72.10, -3.5, "Great Buy"),
    ("2025-05-15", "PP", "ChemCorp", 200, 960, 71.80, -2.1, "Good Buy"),
    ("2025-06-03", "HDPE", "PolySource", 250, 1350, 79.30, 3.2, "Overpaid"),
    ("2025-06-18", "LDPE", "EuroChem", 175, 1390, 78.50, 1.0, "Neutral"),
    ("2025-07-10", "HDPE", "ChemCorp", 350, 1310, 74.20, -1.8, "Good Buy"),
    ("2025-07-25", "PP", "AsiaResin", 300, 940, 73.00, -2.5, "Great Buy"),
    ("2025-08-05", "LDPE", "PolySource", 200, 1430, 80.10, 2.8, "Overpaid"),
    ("2025-08-20", "HDPE", "EuroChem", 275, 1290, 75.60, -0.5, "Good Buy"),
    ("2025-09-12", "HDPE", "AsiaResin", 500, 1220, 68.30, -6.2, "Great Buy"),
    ("2025-09-28", "LDPE", "ChemCorp", 200, 1380, 70.50, -3.1, "Good Buy"),
    ("2025-10-08", "PP", "PolySource", 150, 990, 77.40, 2.0, "Overpaid"),
    ("2025-10-22", "HDPE", "ChemCorp", 300, 1340, 78.90, 3.5, "Overpaid"),
    ("2025-11-04", "HDPE", "PolySource", 250, 1260, 70.50, -5.1, "Great Buy"),
    ("2025-11-18", "LDPE", "AsiaResin", 400, 1350, 69.80, -4.2, "Great Buy"),
    ("2025-12-03", "PP", "EuroChem", 175, 955, 72.30, -1.0, "Good Buy"),
    ("2025-12-15", "HDPE", "ChemCorp", 300, 1370, 80.20, 4.1, "Overpaid"),
    ("2026-01-10", "HDPE", "ChemCorp", 300, 1350, 78.20, 2.1, "Overpaid"),
    ("2026-01-25", "LDPE", "PolySource", 200, 1380, 75.40, -1.5, "Good Buy"),
    ("2026-02-08", "HDPE", "AsiaResin", 500, 1260, 71.00, -4.2, "Great Buy"),
    ("2026-02-20", "PP", "ChemCorp", 150, 980, 73.50, 0.8, "Neutral"),
    ("2026-03-05", "HDPE", "PolySource", 250, 1300, 76.10, 1.3, "Overpaid"),
    ("2026-03-12", "LDPE", "EuroChem", 175, 1395, 74.80, -0.3, "Neutral"),
]
rows = [
    f"(DATE'{d}',{q(mat)},{q(sup)},{qty},{float(price)},{oil},{avg_pct},{q(outcome)})"
    for d, mat, sup, qty, price, oil, avg_pct, outcome in purchase_scenarios
]
run(f"INSERT INTO {TABLE_PURCHASE_HISTORY} VALUES\n" + ",\n".join(rows), f"insert {len(rows)} rows")

# --- 5. procurement_docs (AI Search "Procurement Playbook") ------------------
print(f"\n{TABLE_PROCUREMENT_DOCS}")
run(f"DROP TABLE IF EXISTS {TABLE_PROCUREMENT_DOCS}", "drop")
run(
    f"""CREATE TABLE {TABLE_PROCUREMENT_DOCS} (
        id BIGINT, title STRING, category STRING, content STRING)
        TBLPROPERTIES (delta.enableChangeDataFeed = true)""",
    "create (Change Data Feed on for Delta Sync index)",
)
docs = [
    (1, "Spend approval thresholds", "Approval",
     "Purchase orders up to $250,000 may be approved by the procurement manager. Spot buys above "
     "$250,000 require VP of Supply Chain sign-off. Any single commitment above $1,000,000 requires "
     "CFO approval. Splitting an order to stay under a threshold is prohibited."),
    (2, "Preferred suppliers by material", "Sourcing",
     "ChemCorp is the preferred primary supplier for HDPE and PP due to contract reliability and "
     "Houston proximity. PolySource is the preferred secondary for fast lead times. AsiaResin is "
     "approved for large-volume HDPE spot buys only when total landed cost beats ChemCorp by >3%. "
     "EuroChem is approved for LDPE."),
    (3, "Price-lock and MOQ clauses", "Contracts",
     "Preferred-supplier contracts lock quoted prices for 30 days from the quote date. Minimum order "
     "quantities: ChemCorp 50 tons, PolySource 25 tons, AsiaResin 100 tons, EuroChem 75 tons. Orders "
     "below MOQ incur a 5% small-lot surcharge and are discouraged."),
    (4, "Safety stock policy", "Inventory",
     "Each material/warehouse must hold safety stock at or above the documented level. If on-hand "
     "quantity is at or below the reorder point, a replenishment PO should be raised the same week. "
     "Houston is the primary buffer site; Chicago targets 12+ days of supply."),
    (5, "Buy-timing guidance vs. oil price", "Strategy",
     "Resin prices track crude oil with a lag. When crude is trending down or a quote is more than 3% "
     "below the trailing 30-day average, favor buying to lock the price. When crude is spiking, buy "
     "only to cover near-term production demand and defer discretionary volume."),
    (6, "Supplier diversification", "Risk",
     "No single supplier should exceed 60% of annual spend for any one material. Maintain at least two "
     "qualified suppliers per material. AsiaResin's long lead times (25-35 days) require an added "
     "in-transit buffer and are not suitable for urgent replenishment."),
    (7, "Payment terms", "Contracts",
     "Standard terms are Net 45. A 2% early-payment discount applies for payment within 10 days on "
     "ChemCorp and PolySource contracts. Prepayment is not permitted for AsiaResin."),
    (8, "Sustainability and recycled content", "Policy",
     "Where price and quality are comparable (within 2%), prefer suppliers offering certified "
     "recycled-content resin. Document the sustainability rationale on any PO where a higher-cost "
     "recycled option is chosen over the lowest quote."),
]
rows = [f"({i},{q(t)},{q(c)},{q(body)})" for i, t, c, body in docs]
run(f"INSERT INTO {TABLE_PROCUREMENT_DOCS} VALUES\n" + ",\n".join(rows), f"insert {len(rows)} rows")

# --- Verify ------------------------------------------------------------------
print("\n=== Row counts ===")
for tbl in [
    TABLE_SUPPLIER_QUOTES, TABLE_INVENTORY_LEVELS, TABLE_PRODUCTION_DEMAND,
    TABLE_PURCHASE_HISTORY, TABLE_PROCUREMENT_DOCS,
]:
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=f"SELECT COUNT(*) FROM {tbl}", wait_timeout="30s"
    )
    print(f"  {tbl}: {r.result.data_array[0][0]} rows")
print("\nPhase 2 data generation complete.")
