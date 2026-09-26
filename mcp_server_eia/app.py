"""EIA Crude Oil MCP Server — wraps the U.S. EIA API for petroleum spot prices."""

import os
import urllib.request
import urllib.parse
import json

from fastmcp import FastMCP

mcp = FastMCP(
    "EIA Crude Oil Prices",
    instructions=(
        "Provides crude oil spot prices from the U.S. Energy Information Administration (EIA). "
        "Use these tools to get current and historical WTI and Brent crude oil prices."
    ),
)

EIA_API_BASE = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
EIA_API_KEY = os.environ.get("EIA_API_KEY", "")

PRODUCTS = {
    "WTI": "EPCWTI",
    "Brent": "EPCBRENT",
}


def _eia_query(product_code: str, length: int = 5, start: str = None, end: str = None) -> list:
    """Call EIA API and return data rows."""
    params = {
        "api_key": EIA_API_KEY,
        "frequency": "daily",
        "data[0]": "value",
        "facets[product][]": product_code,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": str(length),
    }
    if start:
        params["start"] = start
    if end:
        params["end"] = end

    url = f"{EIA_API_BASE}?{urllib.parse.urlencode(params)}"
    resp = urllib.request.urlopen(url, timeout=30)
    data = json.loads(resp.read())
    return data.get("response", {}).get("data", [])


def _format_row(row: dict) -> dict:
    return {
        "date": row["period"],
        "product": row["product-name"],
        "price_usd_per_barrel": float(row["value"]),
        "description": row["series-description"],
    }


@mcp.tool
def get_current_oil_price(product: str = "WTI") -> dict:
    """Get the most recent crude oil spot price.

    Args:
        product: "WTI" (West Texas Intermediate) or "Brent" (UK Brent). Defaults to WTI.

    Returns:
        Latest available spot price with date, product name, and price in USD/barrel.
    """
    product = product.upper()
    if product not in PRODUCTS:
        return {"error": f"Unknown product '{product}'. Use 'WTI' or 'Brent'."}

    rows = _eia_query(PRODUCTS[product], length=1)
    if not rows:
        return {"error": "No data returned from EIA API"}
    return _format_row(rows[0])


@mcp.tool
def get_oil_price_history(
    product: str = "WTI",
    days: int = 30,
    start_date: str = None,
    end_date: str = None,
) -> dict:
    """Get historical crude oil spot prices.

    Args:
        product: "WTI" or "Brent". Defaults to WTI.
        days: Number of most recent trading days to return (default 30, max 365). Ignored if start_date is provided.
        start_date: Start date in YYYY-MM-DD format (optional).
        end_date: End date in YYYY-MM-DD format (optional, defaults to today).

    Returns:
        List of daily prices sorted newest first, with summary statistics.
    """
    product = product.upper()
    if product not in PRODUCTS:
        return {"error": f"Unknown product '{product}'. Use 'WTI' or 'Brent'."}

    days = min(days, 365)

    if start_date:
        rows = _eia_query(PRODUCTS[product], length=days, start=start_date, end=end_date)
    else:
        rows = _eia_query(PRODUCTS[product], length=days)

    if not rows:
        return {"error": "No data returned from EIA API"}

    prices = [float(r["value"]) for r in rows]
    formatted = [_format_row(r) for r in rows]

    return {
        "product": product,
        "count": len(formatted),
        "date_range": {"from": formatted[-1]["date"], "to": formatted[0]["date"]},
        "summary": {
            "latest": prices[0],
            "min": min(prices),
            "max": max(prices),
            "avg": round(sum(prices) / len(prices), 2),
        },
        "prices": formatted,
    }


@mcp.tool
def get_oil_price_trend(product: str = "WTI", days: int = 30) -> dict:
    """Analyze the crude oil price trend over a recent period.

    Args:
        product: "WTI" or "Brent". Defaults to WTI.
        days: Number of trading days to analyze (default 30, max 90).

    Returns:
        Trend analysis including direction, price change, percent change, and volatility.
    """
    product = product.upper()
    if product not in PRODUCTS:
        return {"error": f"Unknown product '{product}'. Use 'WTI' or 'Brent'."}

    days = min(days, 90)
    rows = _eia_query(PRODUCTS[product], length=days)
    if len(rows) < 2:
        return {"error": "Not enough data for trend analysis"}

    prices = [float(r["value"]) for r in rows]
    latest = prices[0]
    oldest = prices[-1]
    change = round(latest - oldest, 2)
    pct_change = round((change / oldest) * 100, 2)

    # Simple volatility: std dev of daily returns
    daily_returns = [(prices[i] - prices[i + 1]) / prices[i + 1] for i in range(len(prices) - 1)]
    avg_return = sum(daily_returns) / len(daily_returns)
    variance = sum((r - avg_return) ** 2 for r in daily_returns) / len(daily_returns)
    volatility = round(variance ** 0.5 * 100, 2)

    if pct_change > 3:
        direction = "rising"
    elif pct_change < -3:
        direction = "falling"
    else:
        direction = "stable"

    return {
        "product": product,
        "period_days": len(rows),
        "date_range": {"from": rows[-1]["period"], "to": rows[0]["period"]},
        "latest_price": latest,
        "oldest_price": oldest,
        "price_change": change,
        "pct_change": pct_change,
        "direction": direction,
        "daily_volatility_pct": volatility,
        "high": max(prices),
        "low": min(prices),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
