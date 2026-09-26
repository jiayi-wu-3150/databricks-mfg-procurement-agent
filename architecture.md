# Manufacturing Procurement Agent — Architecture

An AI procurement advisor for a plastics manufacturer buying polyethylene resins
(HDPE, LDPE, PP). It helps a procurement lead decide **whether to buy or wait, how
much, and from which supplier**, by combining internal data, live market data, an
ML deal-quality model, and company policy — all as tools the LLM orchestrates.

- **Workspace:** `adb-984752964297111.11.azuredatabricks.net` (Azure) · profile `azure-demo`
- **Unity Catalog:** `jywu.jywu_mfg_agent`
- **Live app:** https://mfg-procurement-agent-984752964297111.11.azure.databricksapps.com
- **Framework:** OpenAI Agents SDK + MLflow `agent_server`, deployed on **Databricks Apps**

---

## 1. High-level architecture

```
                          ┌──────────────────────────────────────────────┐
                          │        Databricks App: mfg-procurement-agent  │
        user ──chat──▶    │  React chat UI  +  MLflow agent_server (FastAPI)│
                          │           OpenAI Agents SDK (Runner)           │
                          └───────┬───────────────────────┬───────────────┘
                                  │ LLM (plan/act)         │ tools
                                  ▼                        ▼
                   databricks-claude-sonnet-4-5   ┌────────────────────────────┐
                   (Foundation Model serving)     │ 1. get_material_status      │──▶ SQL Warehouse ──▶ UC tables
                                                  │    (@function_tool)         │
                                                  │ 2. EIA oil MCP  (app)       │──▶ api.eia.gov (WTI/Brent)
                                                  │ 3. Pricing MCP  (app)       │──▶ Model Serving: jywu-pricing-model
                                                  │ 4. AI Search playbook (mgd) │──▶ Vector Search index
                                                  └────────────────────────────┘
                                  │ session memory                │ traces
                                  ▼                               ▼
                        Lakebase (Postgres)                 MLflow experiment
```

**Request flow for one question**
1. User sends a message in the chat UI → `agent_server` `/invocations`.
2. The Agents SDK `Runner` calls the LLM (`databricks-claude-sonnet-4-5`), which decides which tools to call.
3. Tools execute (SQL query / EIA API / model serving / vector search) and return results.
4. The LLM synthesizes a grounded buy/wait recommendation.
5. Conversation state is persisted to **Lakebase**; the full trace is logged to **MLflow**.

---

## 2. The agent

| Aspect | Detail |
|---|---|
| Framework | OpenAI Agents SDK (`agents.Runner`) inside MLflow `mlflow.genai.agent_server` (`@invoke` / `@stream`) |
| LLM | `databricks-claude-sonnet-4-5` via **direct Foundation Model serving** (`AsyncDatabricksOpenAI`, `chat_completions`). Env-driven: `USE_AI_GATEWAY=false` on Azure; set `true` (+ a `system.ai.*` model) on workspaces where the FM path is disabled. |
| Instructions | Procurement-advisor system prompt that folds in three "skills" (oil-market-analyst, procurement-advisor, inventory-monitor). |
| Resilience | `connect_healthy_mcp_servers` health-checks each MCP at request time and drops any that are unavailable, so one down tool can't crash a turn. |
| Key file | `agent_server/agent.py` |

---

## 3. The four tools

### 3.1 `get_material_status(material)` — internal data (function tool)
- A `@function_tool` (`agent_server/procurement_tools.py`) that runs a few small parameterized
  SQL queries against the UC tables via the **SQL Warehouse** and returns a concise summary:
  on-hand inventory per warehouse (with safety stock, days of supply, reorder-point warnings),
  the cheapest current supplier quote, and near-term production demand.
- Deterministic, low-latency, easy to explain. (Replaced the original Genie space to keep the demo lean.)

### 3.2 EIA oil-price MCP — `mcp-jywu-eia-oil` (custom Databricks App)
- A **FastMCP** server hosted as its own Databricks App. Tools: `get_current_oil_price`,
  `get_oil_price_history`, `get_oil_price_trend`. Wraps the U.S. EIA API (WTI/Brent spot prices).
- **Auth:** `EIA_API_KEY` injected from a Databricks **secret** (`mfg-agent/eia-api-key`) via
  `app.yaml` `valueFrom` — never hardcoded.
- **Why it matters:** resin prices track crude oil with a lag, so oil direction informs buy timing.

### 3.3 Pricing MCP — `mcp-jywu-pricing` (custom Databricks App)
- A **FastMCP** app exposing `predict_deal_quality`, which calls the **Model Serving endpoint**
  `jywu-pricing-model`.
- **Model:** a `GradientBoostingClassifier` trained on `purchase_history` (good-deal vs bad-deal),
  registered in UC and served scale-to-zero. Logged with **explicit lean `pip_requirements`**
  (mlflow, scikit-learn, numpy, pandas, cloudpickle) so the serving container is tiny and builds fast.
- **Two layers to remember:** the MCP app being up (tool connects/lists) is independent of the
  serving endpoint being READY (actual predictions). The pricing app's service principal needs
  `CAN_QUERY` on the endpoint.

### 3.4 AI Search "Procurement Playbook" — managed MCP
- A **Delta Sync Vector Search index** (`procurement_docs_index`, qwen3 embeddings) over
  `procurement_docs` (≈8 policy/contract snippets, Change Data Feed enabled), exposed via the
  **managed** MCP path `/api/2.0/mcp/ai-search/jywu/jywu_mfg_agent/procurement_docs_index`.
- Grounds recommendations in company policy: approval thresholds, preferred suppliers,
  price-lock/MOQ clauses, safety-stock policy, payment terms, sustainability.

---

## 4. Data layer (`jywu.jywu_mfg_agent`)

| Table | Rows | Purpose |
|---|---|---|
| `supplier_quotes` | 36 | vendor pricing, lead times, MOQ, validity |
| `inventory_levels` | 6 | stock, safety stock, days of supply, reorder point |
| `production_demand` | 9 | upcoming material requirements by month |
| `purchase_history` | 24 | past decisions with outcome labels (ML training data) |
| `procurement_docs` | 8 | Procurement Playbook — source for the Vector Search index |

Materials: HDPE, LDPE, PP · Suppliers: ChemCorp, PolySource, AsiaResin, EuroChem · Warehouses: Houston, Chicago.

---

## 5. Memory & observability

- **Short-term memory:** `AsyncDatabricksSession` → **Lakebase** autoscaling Postgres
  (project `jywu-mfg-agent`), scoped by `session_id`, so multi-turn conversations keep context.
- **Tracing / eval:** MLflow experiment `4490002415372478` (`agents-on-apps`) — every turn is traced.
  Optional **OpenTelemetry dual export** is scaffolded (`MLFLOW_TRACE_ENABLE_OTLP_DUAL_EXPORT=true`)
  to also ship traces to an external OTLP backend without losing Databricks tracing.
- The **ML model** uses a **separate** MLflow experiment (`mfg-pricing-model`), kept distinct from
  the agent's tracing experiment.

---

## 6. Deployment & security

- **Agent app:** Databricks Asset Bundle (`databricks.yml`) → `databricks bundle deploy && run`.
  Runtime config (LLM model, catalog/schema/warehouse, MCP URLs, Lakebase, experiment) lives in
  the bundle's `config.env`.
- **MCP servers:** deployed as separate Databricks Apps (`apps create` + `sync` + `apps deploy`).
- **Auth model:** the agent app's **service principal** is granted: `CAN_USE` on both MCP apps,
  `CAN_USE` on the SQL warehouse, `USE CATALOG`/`USE SCHEMA`/`SELECT` on the schema, and Lakebase
  Postgres privileges. The pricing MCP's SP is granted `CAN_QUERY` on the serving endpoint.
- **Egress note:** on this Azure workspace, app-to-app egress is open, so the agent reaches the
  MCP apps directly via their URLs. On networks where egress is restricted, register the MCP apps
  as **UC MCP Services** (UC HTTP connection, OAUTH_M2M) and consume them over the internal path.

---

## 7. Key design decisions & lessons

| Decision / lesson | Why |
|---|---|
| Custom tools as **MCP servers on Apps** | Field-recommended for reusable tools; lower latency than UC-function tools. |
| **Direct FM serving** vs AI Gateway | Azure has the FM path enabled; env flag switches to the AI Gateway where it's disabled. |
| **Lean `pip_requirements`** on the model | Inferred requirements captured the whole project venv (150+ pkgs incl. PySpark) → huge, slow serving image. Explicit 5-package reqs build fast anywhere. |
| **Managed** AI Search MCP for retrieval | Endorsed retrieval path; internal, no egress concerns. |
| No `uv.lock` in the app deploy | Build's uv version differs from local → `--locked` mismatch; let the build resolve from `pyproject.toml`. |
| Separate MLflow experiments | Agent tracing vs ML model runs are different concerns. |
