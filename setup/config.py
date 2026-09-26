# Manufacturing Procurement Agent — Configuration (Azure rebuild)
#
# Plain importable module (no notebook magic) so both the standalone setup
# scripts and the agent code can `from setup.config import ...`.

import os

# --- Databricks workspace ----------------------------------------------------
WORKSPACE_URL = "https://adb-984752964297111.11.azuredatabricks.net"
PROFILE = "azure-demo"

# --- Unity Catalog -----------------------------------------------------------
CATALOG = "jywu"
SCHEMA = "jywu_mfg_agent"

# --- SQL Warehouse (Shared Endpoint on this workspace) -----------------------
WAREHOUSE_ID = "148ccb90800933a1"

# --- LLM ---------------------------------------------------------------------
# Azure has the Foundation-Model serving path ENABLED, so call the model
# endpoint directly (no AI Gateway needed, unlike fevm).
USE_AI_GATEWAY = False
LLM_MODEL = "databricks-claude-sonnet-4-5"

# --- Vector Search / embeddings ----------------------------------------------
VS_ENDPOINT = "one-env-shared-endpoint-15"  # reuse an existing ONLINE endpoint
EMBEDDING_ENDPOINT = "databricks-qwen3-embedding-0-6b"

# --- EIA API (oil prices) — key read from environment, never hardcoded -------
EIA_API_KEY = os.environ.get("EIA_API_KEY", "")
EIA_API_BASE = "https://api.eia.gov/v2"

# --- Table names -------------------------------------------------------------
TABLE_SUPPLIER_QUOTES = f"{CATALOG}.{SCHEMA}.supplier_quotes"
TABLE_INVENTORY_LEVELS = f"{CATALOG}.{SCHEMA}.inventory_levels"
TABLE_PRODUCTION_DEMAND = f"{CATALOG}.{SCHEMA}.production_demand"
TABLE_PURCHASE_HISTORY = f"{CATALOG}.{SCHEMA}.purchase_history"
TABLE_PROCUREMENT_DOCS = f"{CATALOG}.{SCHEMA}.procurement_docs"
