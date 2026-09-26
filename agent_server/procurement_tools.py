"""Internal-data tool for the procurement agent.

`get_material_status(material)` is the single `@function_tool` internal-data
source (Genie was dropped in the migration). It runs a few small parameterized
queries against the UC procurement tables via the SQL warehouse and returns a
concise, LLM-friendly summary: on-hand inventory, the cheapest current supplier
quote, and near-term production demand.

Catalog/schema/warehouse come from env (set in the app config), with sane
defaults for local runs.
"""

import os

from databricks.sdk import WorkspaceClient

CATALOG = os.environ.get("MFG_CATALOG", "serverless_stable_r4umw1_catalog")
SCHEMA = os.environ.get("MFG_SCHEMA", "jywu_mfg_agent")
WAREHOUSE_ID = os.environ.get("MFG_WAREHOUSE_ID", "b04eb16e0536bd88")

_VALID_MATERIALS = {"HDPE", "LDPE", "PP"}


def _rows(w: WorkspaceClient, sql: str):
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=sql, wait_timeout="30s"
    )
    return r.result.data_array or []


def get_material_status_data(material: str) -> str:
    """Core logic (no agents-SDK dependency) so it can be unit-tested directly."""
    material = (material or "").strip().upper()
    if material not in _VALID_MATERIALS:
        return f"Unknown material '{material}'. Valid materials: {', '.join(sorted(_VALID_MATERIALS))}."

    w = WorkspaceClient()
    base = f"{CATALOG}.{SCHEMA}"
    m = material.replace("'", "''")

    inv = _rows(w, f"""SELECT warehouse, quantity_tons, safety_stock_tons, days_of_supply, reorder_point_tons
        FROM {base}.inventory_levels WHERE material='{m}' ORDER BY warehouse""")
    quotes = _rows(w, f"""SELECT supplier, price_usd_ton, lead_time_days, min_order_tons, quote_date
        FROM {base}.supplier_quotes
        WHERE material='{m}' AND quote_date=(SELECT MAX(quote_date) FROM {base}.supplier_quotes WHERE material='{m}')
        ORDER BY price_usd_ton ASC""")
    demand = _rows(w, f"""SELECT production_month, required_tons, priority, confidence_pct
        FROM {base}.production_demand WHERE material='{m}' ORDER BY production_month LIMIT 3""")

    lines = [f"Material status for {material}:"]

    if inv:
        total = sum(int(r[1]) for r in inv)
        lines.append(f"- Inventory (total {total} tons on hand):")
        for wh, qty, ss, dos, rp in inv:
            flag = " ⚠ at/below reorder point" if int(qty) <= int(rp) else ""
            lines.append(f"    • {wh}: {qty}t (safety stock {ss}, {dos} days of supply, reorder at {rp}){flag}")
    else:
        lines.append("- Inventory: no records.")

    if quotes:
        s, price, lt, moq, qd = quotes[0]
        lines.append(f"- Cheapest current quote: {s} at ${float(price):,.2f}/ton "
                     f"(lead {lt} days, MOQ {moq}t; quoted {qd}).")
        if len(quotes) > 1:
            others = "; ".join(f"{r[0]} ${float(r[1]):,.0f}" for r in quotes[1:])
            lines.append(f"    Other quotes: {others}.")
    else:
        lines.append("- Supplier quotes: none found.")

    if demand:
        d = "; ".join(f"{mo} {tons}t ({pri}, {conf}% conf)" for mo, tons, pri, conf in demand)
        lines.append(f"- Near-term production demand: {d}.")
    else:
        lines.append("- Production demand: none scheduled.")

    return "\n".join(lines)
