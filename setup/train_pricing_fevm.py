"""Phase 4 — train, register & deploy the ML pricing model on fevm.

Standalone (no notebook/dbutils): loads purchase_history via the SQL warehouse,
trains a GradientBoostingClassifier locally, logs to a SEPARATE MLflow experiment
(/Users/<me>/mfg-pricing-model — distinct from the agent's tracing experiment),
registers to UC, and creates/updates the `jywu-pricing-model` serving endpoint.

Endpoint readiness is NOT awaited here (can take ~5-10 min); poll separately.

Run:  DATABRICKS_CONFIG_PROFILE=fevm-serverless-stable-r4umw1 uv run python -m setup.train_pricing_fevm
"""

import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow import MlflowClient
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import EndpointCoreConfigInput, ServedEntityInput

from setup.config import CATALOG, SCHEMA, WAREHOUSE_ID, PROFILE, TABLE_PURCHASE_HISTORY

w = WorkspaceClient()
me = w.current_user.me().user_name  # e.g. jiayi.wu@databricks.com

MODEL_NAME = f"{CATALOG}.{SCHEMA}.pricing_model"
ENDPOINT_NAME = "jywu-pricing-model"
# Separate experiment for the ML model (NOT the agent's tracing experiment)
EXPERIMENT_PATH = f"/Users/{me}/mfg-pricing-model"

# --- Load training data via the SQL warehouse --------------------------------
print(f"Loading {TABLE_PURCHASE_HISTORY} ...")
resp = w.statement_execution.execute_statement(
    warehouse_id=WAREHOUSE_ID,
    statement=(
        "SELECT material, price_usd_ton, oil_price_at_purchase, quantity_tons, "
        f"price_vs_30d_avg_pct, outcome FROM {TABLE_PURCHASE_HISTORY}"
    ),
    wait_timeout="50s",
)
cols = ["material", "price_usd_ton", "oil_price_at_purchase", "quantity_tons", "price_vs_30d_avg_pct", "outcome"]
df = pd.DataFrame(resp.result.data_array, columns=cols)
for c in ["price_usd_ton", "oil_price_at_purchase", "quantity_tons", "price_vs_30d_avg_pct"]:
    df[c] = pd.to_numeric(df[c])

# --- Features / target -------------------------------------------------------
material_map = {"HDPE": 0, "LDPE": 1, "PP": 2}
df["material_code"] = df["material"].map(material_map)
df["good_deal"] = df["outcome"].isin(["Great Buy", "Good Buy"]).astype(int)

features = ["material_code", "price_usd_ton", "oil_price_at_purchase", "quantity_tons", "price_vs_30d_avg_pct"]
X, y = df[features], df["good_deal"]
print(f"Training rows: {len(df)} | target distribution: {dict(y.value_counts())}")

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=42)
model = GradientBoostingClassifier(n_estimators=50, max_depth=3, random_state=42)
model.fit(X_train, y_train)
print(classification_report(y_test, model.predict(X_test), target_names=["Bad Deal", "Good Deal"], zero_division=0))
for feat, imp in sorted(zip(features, model.feature_importances_), key=lambda x: -x[1]):
    print(f"  {feat:25s} {imp:.3f}")

# --- Log to MLflow (separate experiment) + register to UC --------------------
mlflow.set_tracking_uri(f"databricks://{PROFILE}")
mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(EXPERIMENT_PATH)
print(f"\nMLflow experiment: {EXPERIMENT_PATH}")

input_example = pd.DataFrame([{
    "material_code": 0, "price_usd_ton": 1300.0, "oil_price_at_purchase": 75.0,
    "quantity_tons": 250, "price_vs_30d_avg_pct": 1.5,
}])

with mlflow.start_run(run_name="pricing_model_v1") as run:
    mlflow.sklearn.log_model(
        model, artifact_path="model", input_example=input_example, registered_model_name=MODEL_NAME,
        # MLflow 3.16 defaults to skops, which rejects sklearn's tree type; we trust our own model.
        serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_CLOUDPICKLE,
        # EXPLICIT lean requirements — otherwise MLflow infers the whole fat project venv
        # (databricks-connect/PySpark, databricks-agents, the agent stack) into the model's
        # serving container, making the endpoint build huge and slow/stall. A 30KB sklearn
        # model only needs these:
        pip_requirements=[
            "mlflow==3.16.1", "scikit-learn==1.9.1", "numpy", "pandas", "cloudpickle",
        ],
    )
    mlflow.log_metric("test_accuracy", float(model.score(X_test, y_test)))
    mlflow.log_param("n_estimators", 50)
    mlflow.log_param("features", str(features))
print(f"Registered UC model: {MODEL_NAME} (run {run.info.run_id})")

client = MlflowClient(registry_uri="databricks-uc")
latest_version = max(int(v.version) for v in client.search_model_versions(f"name='{MODEL_NAME}'"))
print(f"Latest version: {latest_version}")

# --- Create / update serving endpoint (no wait) ------------------------------
served = ServedEntityInput(
    entity_name=MODEL_NAME, entity_version=str(latest_version),
    workload_size="Small", scale_to_zero_enabled=True,
)
existing = [e.name for e in w.serving_endpoints.list()]
if ENDPOINT_NAME in existing:
    print(f"Updating endpoint config: {ENDPOINT_NAME}")
    w.serving_endpoints.update_config(name=ENDPOINT_NAME, served_entities=[served])
else:
    print(f"Creating endpoint: {ENDPOINT_NAME}")
    # This SDK version requires `name` inside EndpointCoreConfigInput.
    w.serving_endpoints.create(
        name=ENDPOINT_NAME,
        config=EndpointCoreConfigInput(name=ENDPOINT_NAME, served_entities=[served]),
    )

print(f"\nPhase 4 trigger complete. Endpoint '{ENDPOINT_NAME}' provisioning — poll for readiness.")
