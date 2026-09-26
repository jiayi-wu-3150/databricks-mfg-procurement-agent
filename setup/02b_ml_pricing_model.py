# Databricks notebook source
# MAGIC %md
# MAGIC # Step 2c: Train, Register & Deploy ML Pricing Model
# MAGIC
# MAGIC Trains a simple model on purchase history to predict whether a given price
# MAGIC is a good deal, registers it in Unity Catalog, and deploys to Model Serving.

# COMMAND ----------

# MAGIC %run ./config

# COMMAND ----------

import mlflow
import mlflow.sklearn
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import pandas as pd
import numpy as np

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load & Prepare Training Data

# COMMAND ----------

df = spark.table(TABLE_PURCHASE_HISTORY).toPandas()

# Encode material as numeric
material_map = {"HDPE": 0, "LDPE": 1, "PP": 2}
df["material_code"] = df["material"].map(material_map)

# Binary target: 1 = good deal (Great Buy or Good Buy), 0 = bad deal (Overpaid or Neutral)
df["good_deal"] = df["outcome"].isin(["Great Buy", "Good Buy"]).astype(int)

features = ["material_code", "price_usd_ton", "oil_price_at_purchase", "quantity_tons", "price_vs_30d_avg_pct"]
X = df[features]
y = df["good_deal"]

print(f"Training data: {len(df)} rows")
print(f"Target distribution:\n{y.value_counts().to_string()}")
print(f"\nFeatures: {features}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Train Model

# COMMAND ----------

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=42)

model = GradientBoostingClassifier(n_estimators=50, max_depth=3, random_state=42)
model.fit(X_train, y_train)

y_pred = model.predict(X_test)
print(classification_report(y_test, y_pred, target_names=["Bad Deal", "Good Deal"]))

# Feature importance
for feat, imp in sorted(zip(features, model.feature_importances_), key=lambda x: -x[1]):
    print(f"  {feat:25s} {imp:.3f}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Log Model to MLflow

# COMMAND ----------

mlflow.set_registry_uri("databricks-uc")
model_name = f"{CATALOG}.{SCHEMA}.pricing_model"

# Input example for signature
input_example = pd.DataFrame([{
    "material_code": 0,
    "price_usd_ton": 1300.0,
    "oil_price_at_purchase": 75.0,
    "quantity_tons": 250,
    "price_vs_30d_avg_pct": 1.5,
}])

with mlflow.start_run(run_name="pricing_model_v1") as run:
    mlflow.sklearn.log_model(
        model,
        artifact_path="model",
        input_example=input_example,
        registered_model_name=model_name,
    )
    mlflow.log_metric("test_accuracy", model.score(X_test, y_test))
    mlflow.log_param("n_estimators", 50)
    mlflow.log_param("features", str(features))
    run_id = run.info.run_id

print(f"Model registered: {model_name}")
print(f"Run ID: {run_id}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Deploy to Model Serving

# COMMAND ----------

import requests
import time

workspace_url = WORKSPACE_URL.rstrip("/")
token = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

endpoint_name = "jywu-pricing-model"

# Get latest model version
from mlflow import MlflowClient
client = MlflowClient(registry_uri="databricks-uc")
versions = client.search_model_versions(f"name='{model_name}'")
latest_version = max(v.version for v in versions)
print(f"Latest model version: {latest_version}")

# Create or update serving endpoint
served_entity = {
    "entity_name": model_name,
    "entity_version": str(latest_version),
    "workload_size": "Small",
    "scale_to_zero_enabled": True,
}

resp = requests.get(f"{workspace_url}/api/2.0/serving-endpoints/{endpoint_name}", headers=headers)
if resp.status_code == 200:
    print(f"Updating existing endpoint: {endpoint_name}")
    requests.put(
        f"{workspace_url}/api/2.0/serving-endpoints/{endpoint_name}/config",
        headers=headers,
        json={"served_entities": [served_entity]},
    )
else:
    print(f"Creating new endpoint: {endpoint_name}")
    requests.post(
        f"{workspace_url}/api/2.0/serving-endpoints",
        headers=headers,
        json={"name": endpoint_name, "config": {"served_entities": [served_entity]}},
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Wait for Endpoint & Test

# COMMAND ----------

print(f"Waiting for endpoint '{endpoint_name}' (may take up to 20 min)...")
for i in range(5):
    resp = requests.get(f"{workspace_url}/api/2.0/serving-endpoints/{endpoint_name}", headers=headers)
    state = resp.json().get("state", {})
    ready = state.get("ready", "")
    config_update = state.get("config_update", "")
    if ready == "READY" and config_update != "IN_PROGRESS":
        print(f"Endpoint ready after {i * 5} min")
        break
    print(f"  [{i * 5} min] ready={ready}, config={config_update}")
    time.sleep(300)
else:
    print("Endpoint not ready after 25 min — check the Serving UI.")

# COMMAND ----------

test_payload = {
    "dataframe_records": [
        {"material_code": 0, "price_usd_ton": 1300.0, "oil_price_at_purchase": 75.0, "quantity_tons": 250, "price_vs_30d_avg_pct": 1.5},
        {"material_code": 0, "price_usd_ton": 1220.0, "oil_price_at_purchase": 68.0, "quantity_tons": 500, "price_vs_30d_avg_pct": -5.0},
        {"material_code": 1, "price_usd_ton": 1430.0, "oil_price_at_purchase": 80.0, "quantity_tons": 200, "price_vs_30d_avg_pct": 3.0},
    ]
}

resp = requests.post(
    f"{workspace_url}/serving-endpoints/{endpoint_name}/invocations",
    headers=headers,
    json=test_payload,
)
print(f"Status: {resp.status_code}")

materials = ["HDPE", "HDPE", "LDPE"]
for i, pred in enumerate(resp.json().get("predictions", [])):
    deal = "Good Deal" if pred == 1 else "Bad Deal"
    rec = test_payload["dataframe_records"][i]
    print(f"  {materials[i]} @ ${rec['price_usd_ton']}/ton (oil ${rec['oil_price_at_purchase']}, {rec['price_vs_30d_avg_pct']:+.1f}% vs avg) -> {deal}")
