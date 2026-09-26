"""Pricing MCP Server — calls Model Serving endpoint for deal quality predictions."""

import os
import json
import urllib.request

from fastmcp import FastMCP

mcp = FastMCP(
    "ML Pricing Predictions",
    instructions=(
        "Predicts whether a proposed PE resin purchase (HDPE, LDPE, PP) is a good deal "
        "based on price, oil price, quantity, and market conditions. "
        "Uses a trained ML model deployed on Databricks Model Serving."
    ),
)

WORKSPACE_URL = os.environ.get("WORKSPACE_URL", "").rstrip("/")
SERVING_ENDPOINT = os.environ.get("SERVING_ENDPOINT", "jywu-pricing-model")

MATERIAL_MAP = {"HDPE": 0, "LDPE": 1, "PP": 2}


def _get_token() -> str:
    """Get auth token from Databricks SDK (works in Apps and locally)."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    auth = w.config.authenticate()
    if isinstance(auth, dict):
        return auth.get("Authorization", "").replace("Bearer ", "")
    return str(auth)


def _call_serving(records: list) -> list:
    token = _get_token()
    url = f"{WORKSPACE_URL}/serving-endpoints/{SERVING_ENDPOINT}/invocations"
    payload = json.dumps({"dataframe_records": records}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read()).get("predictions", [])


@mcp.tool
def predict_deal_quality(
    material: str,
    price_usd_ton: float,
    oil_price: float,
    quantity_tons: int = 100,
    price_vs_30d_avg_pct: float = 0.0,
) -> dict:
    """Predict whether a proposed purchase is a good deal using the ML pricing model.

    Args:
        material: Material type — "HDPE", "LDPE", or "PP".
        price_usd_ton: Proposed price in USD per ton.
        oil_price: Current crude oil price in USD per barrel.
        quantity_tons: Purchase quantity in tons (default 100).
        price_vs_30d_avg_pct: Price compared to 30-day average as percentage (e.g., -3.5 means 3.5% below average).

    Returns:
        Prediction with buy/wait recommendation.
    """
    material = material.upper()
    if material not in MATERIAL_MAP:
        return {"error": f"Unknown material '{material}'. Use 'HDPE', 'LDPE', or 'PP'."}

    record = {
        "material_code": MATERIAL_MAP[material],
        "price_usd_ton": price_usd_ton,
        "oil_price_at_purchase": oil_price,
        "quantity_tons": quantity_tons,
        "price_vs_30d_avg_pct": price_vs_30d_avg_pct,
    }

    predictions = _call_serving([record])
    if not predictions:
        return {"error": "No prediction returned from model serving endpoint"}

    pred = predictions[0]
    is_good = bool(pred == 1)

    return {
        "material": material,
        "price_usd_ton": price_usd_ton,
        "oil_price": oil_price,
        "quantity_tons": quantity_tons,
        "price_vs_30d_avg_pct": price_vs_30d_avg_pct,
        "prediction": "Good Deal" if is_good else "Bad Deal",
        "recommendation": "BUY" if is_good else "WAIT",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
