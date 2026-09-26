# Databricks notebook source
# MAGIC %md
# MAGIC # Step 1: Generate Synthetic Manufacturing Procurement Data
# MAGIC
# MAGIC Creates 4 tables in Unity Catalog:
# MAGIC - `supplier_quotes` — vendor pricing, lead times, minimums
# MAGIC - `inventory_levels` — warehouse stock, safety stock, days of supply
# MAGIC - `production_demand` — upcoming material requirements by month
# MAGIC - `purchase_history` — past procurement decisions with outcome labels

# COMMAND ----------

# MAGIC %run ./config

# COMMAND ----------

import random
from datetime import datetime, timedelta
from pyspark.sql.types import *

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Constants

# COMMAND ----------

MATERIALS = ["HDPE", "LDPE", "PP"]
SUPPLIERS = {
    "ChemCorp": {"lead_time_range": (12, 16), "min_order": 50, "price_premium": 1.0},
    "PolySource": {"lead_time_range": (8, 12), "min_order": 25, "price_premium": 1.02},
    "AsiaResin": {"lead_time_range": (25, 35), "min_order": 100, "price_premium": 0.96},
    "EuroChem": {"lead_time_range": (18, 25), "min_order": 75, "price_premium": 0.99},
}
WAREHOUSES = ["Houston", "Chicago"]
PRODUCT_LINES = {"HDPE": "Packaging Film", "LDPE": "Shrink Wrap", "PP": "Containers"}
BASE_PRICES = {"HDPE": 1280, "LDPE": 1400, "PP": 970}

random.seed(42)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Supplier Quotes

# COMMAND ----------

quotes = []
base_date = datetime(2026, 3, 18)

for day_offset in range(5):
    quote_date = base_date + timedelta(days=day_offset)
    for material in MATERIALS:
        quoting_suppliers = random.sample(list(SUPPLIERS.keys()), k=random.randint(2, 3))
        for supplier in quoting_suppliers:
            info = SUPPLIERS[supplier]
            base = BASE_PRICES[material]
            price = round(base * info["price_premium"] * random.uniform(0.97, 1.03), 2)
            lead_time = random.randint(*info["lead_time_range"])
            valid_days = random.choice([5, 7, 10])
            quotes.append({
                "quote_date": quote_date.strftime("%Y-%m-%d"),
                "supplier": supplier,
                "material": material,
                "price_usd_ton": price,
                "lead_time_days": lead_time,
                "min_order_tons": info["min_order"],
                "valid_until": (quote_date + timedelta(days=valid_days)).strftime("%Y-%m-%d"),
            })

quotes_schema = StructType([
    StructField("quote_date", StringType()),
    StructField("supplier", StringType()),
    StructField("material", StringType()),
    StructField("price_usd_ton", DoubleType()),
    StructField("lead_time_days", IntegerType()),
    StructField("min_order_tons", IntegerType()),
    StructField("valid_until", StringType()),
])

df_quotes = spark.createDataFrame(quotes, schema=quotes_schema)
df_quotes = df_quotes.withColumn("quote_date", df_quotes.quote_date.cast("date"))
df_quotes = df_quotes.withColumn("valid_until", df_quotes.valid_until.cast("date"))

df_quotes.write.mode("overwrite").saveAsTable(TABLE_SUPPLIER_QUOTES)
print(f"Created {TABLE_SUPPLIER_QUOTES}: {df_quotes.count()} rows")
df_quotes.show(5, truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Inventory Levels

# COMMAND ----------

inventory_data = {
    ("HDPE", "Houston"): {"quantity": 320, "safety_stock": 150, "days_supply": 18, "reorder_point": 200},
    ("HDPE", "Chicago"): {"quantity": 210, "safety_stock": 100, "days_supply": 22, "reorder_point": 130},
    ("LDPE", "Houston"): {"quantity": 85, "safety_stock": 100, "days_supply": 6, "reorder_point": 120},
    ("LDPE", "Chicago"): {"quantity": 140, "safety_stock": 100, "days_supply": 12, "reorder_point": 120},
    ("PP", "Houston"): {"quantity": 175, "safety_stock": 80, "days_supply": 25, "reorder_point": 100},
    ("PP", "Chicago"): {"quantity": 95, "safety_stock": 60, "days_supply": 20, "reorder_point": 75},
}

inventory = []
for (material, warehouse), data in inventory_data.items():
    inventory.append({
        "snapshot_date": "2026-03-20",
        "material": material,
        "warehouse": warehouse,
        "quantity_tons": data["quantity"],
        "safety_stock_tons": data["safety_stock"],
        "days_of_supply": data["days_supply"],
        "reorder_point_tons": data["reorder_point"],
    })

inventory_schema = StructType([
    StructField("snapshot_date", StringType()),
    StructField("material", StringType()),
    StructField("warehouse", StringType()),
    StructField("quantity_tons", IntegerType()),
    StructField("safety_stock_tons", IntegerType()),
    StructField("days_of_supply", IntegerType()),
    StructField("reorder_point_tons", IntegerType()),
])

df_inventory = spark.createDataFrame(inventory, schema=inventory_schema)
df_inventory = df_inventory.withColumn("snapshot_date", df_inventory.snapshot_date.cast("date"))

df_inventory.write.mode("overwrite").saveAsTable(TABLE_INVENTORY_LEVELS)
print(f"Created {TABLE_INVENTORY_LEVELS}: {df_inventory.count()} rows")
df_inventory.show(truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Production Demand

# COMMAND ----------

demand_data = [
    ("2026-04", "HDPE", 400, "High", 95),
    ("2026-04", "LDPE", 250, "High", 90),
    ("2026-04", "PP", 150, "Medium", 85),
    ("2026-05", "HDPE", 450, "High", 80),
    ("2026-05", "LDPE", 200, "Medium", 70),
    ("2026-05", "PP", 180, "Medium", 65),
    ("2026-06", "HDPE", 380, "Medium", 60),
    ("2026-06", "LDPE", 220, "Medium", 55),
    ("2026-06", "PP", 160, "Low", 50),
]

demand = []
product_lines = {"HDPE": "Packaging Film", "LDPE": "Shrink Wrap", "PP": "Containers"}
for month, material, tons, priority, confidence in demand_data:
    demand.append({
        "production_month": month,
        "material": material,
        "required_tons": tons,
        "product_line": product_lines[material],
        "priority": priority,
        "confidence_pct": confidence,
    })

demand_schema = StructType([
    StructField("production_month", StringType()),
    StructField("material", StringType()),
    StructField("required_tons", IntegerType()),
    StructField("product_line", StringType()),
    StructField("priority", StringType()),
    StructField("confidence_pct", IntegerType()),
])

df_demand = spark.createDataFrame(demand, schema=demand_schema)

df_demand.write.mode("overwrite").saveAsTable(TABLE_PRODUCTION_DEMAND)
print(f"Created {TABLE_PRODUCTION_DEMAND}: {df_demand.count()} rows")
df_demand.show(truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Purchase History

# COMMAND ----------

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

purchases = []
for date, material, supplier, qty, price, oil, avg_pct, outcome in purchase_scenarios:
    purchases.append({
        "purchase_date": date,
        "material": material,
        "supplier": supplier,
        "quantity_tons": qty,
        "price_usd_ton": float(price),
        "oil_price_at_purchase": oil,
        "price_vs_30d_avg_pct": avg_pct,
        "outcome": outcome,
    })

purchase_schema = StructType([
    StructField("purchase_date", StringType()),
    StructField("material", StringType()),
    StructField("supplier", StringType()),
    StructField("quantity_tons", IntegerType()),
    StructField("price_usd_ton", DoubleType()),
    StructField("oil_price_at_purchase", DoubleType()),
    StructField("price_vs_30d_avg_pct", DoubleType()),
    StructField("outcome", StringType()),
])

df_purchases = spark.createDataFrame(purchases, schema=purchase_schema)
df_purchases = df_purchases.withColumn("purchase_date", df_purchases.purchase_date.cast("date"))

df_purchases.write.mode("overwrite").saveAsTable(TABLE_PURCHASE_HISTORY)
print(f"Created {TABLE_PURCHASE_HISTORY}: {df_purchases.count()} rows")
df_purchases.show(10, truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify All Tables

# COMMAND ----------

print("=== All Tables Created ===")
for table in [TABLE_SUPPLIER_QUOTES, TABLE_INVENTORY_LEVELS, TABLE_PRODUCTION_DEMAND, TABLE_PURCHASE_HISTORY]:
    count = spark.table(table).count()
    print(f"  {table}: {count} rows")
