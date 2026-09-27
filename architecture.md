# Manufacturing Procurement Agent — Architecture

An AI procurement advisor for a plastics manufacturer buying polyethylene resins
(HDPE, LDPE, PP). It helps a procurement lead decide **whether to buy or wait, how
much, and from which supplier**, by combining internal data, live market data, an
ML deal-quality model, and company policy — all as tools the LLM orchestrates.

- **Workspace:** `adb-984752964297111.11.azuredatabricks.net` (Azure) · profile `azure-demo`
- **Unity Catalog:** `jywu.jywu_mfg_agent`
- **Framework:** OpenAI Agents SDK + MLflow `agent_server`, deployed on **Databricks Apps** (access is workspace-internal)

---

## 1. High-level architecture

```
                        ┌───────────────────────────────────────────────┐
     user ──chat──▶     │ Databricks App: mfg-procurement-agent         │
                        │ React chat UI · MLflow agent_server (FastAPI) │
                        │ OpenAI Agents SDK (Runner)                    │
                        └───────────────────────────────────────────────┘
                             │  LLM (plan/act)                    │  tools
                             ▼                                   ▼
   databricks-claude-sonnet-4-5      ┌──────────────────────────────────────────┐
   (Foundation Model serving)        │ 1. get_material_status  (UC function)    │──▶ UC Functions MCP ──▶ UC tables
                                     │ 2. EIA oil              (UC MCP Service) │──▶ api.eia.gov (WTI/Brent)
                                     │ 3. Pricing              (UC MCP Service) │──▶ Model Serving: jywu-pricing-model
                                     │ 4. AI Search            (managed MCP)    │──▶ VS index (parsed-PDF chunks)
                                     └──────────────────────────────────────────┘

   session memory ──▶ Lakebase (Postgres)              traces ──▶ MLflow experiment
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

### 3.1 `get_material_status(material)` — internal data (UC function via UC Functions MCP)

```
                ┌─ UC Functions MCP ───────┐    ┌─ get_material_status ┐    ┌─ UC tables ┐
                │ managed · built-in       │    │ SQL function (UDF)   │    │ inventory  │
agent ─EXECUTE─▶│ /api/2.0/mcp/functions/… │ ─▶ │ EXECUTE-governed     │ ─▶ │ quotes     │
                └──────────────────────────┘    └──────────────────────┘    │ demand     │
                                                                            └────────────┘
```
*Managed MCP — no app, no connection, no service; the platform hosts the path.*

- A **UC SQL function** `jywu.jywu_mfg_agent.get_material_status(material)` that queries the UC
  tables (inventory / quotes / demand) and returns a concise summary: on-hand inventory,
  cheapest recent supplier quote, and next-month demand.
- Exposed to the agent through the **managed UC Functions MCP** (`/api/2.0/mcp/functions/jywu/jywu_mfg_agent`)
  — so it's UC-governed (EXECUTE grants) and shows under the schema's **Functions** tab. (Replaced the
  original Genie space *and* the earlier in-process `@function_tool` to make it governed.)
- **Gotcha:** reference the parameter **function-qualified** (`get_material_status.material`) with
  table aliases inside the subqueries. A bare param name works via a direct `SELECT` but fails under
  the MCP's named-arg invocation with `UNRESOLVED_COLUMN`.

### 3.2 EIA oil-price MCP — `mcp-jywu-eia-oil` (custom Databricks App)

```
       ┌─ UC MCP Service ┐    ┌─ UC Connection ──────┐    ┌─ Databricks App ───────┐
       │ eia_oil         │    │ eia_conn             │    │ mcp-jywu-eia-oil       │
agent  │ governed handle │ ─▶ │ app URL + M2M        │ ─▶ │ FastMCP server         │ ─▶ api.eia.gov
─EXEC─▶│ /ai-gateway/…   │    │ creds = SP_connector │    │ + EIA_API_KEY (secret) │    (WTI/Brent)
       └─────────────────┘    │ token /oidc/v1/token │    └────────────────────────┘
                              └──────────────────────┘
       what the agent calls          reach + auth                  what runs
```
*Three UC objects chain: the **Service** (what the agent is granted `EXECUTE` on) references the
**Connection** (the app URL + the `SP_connector` M2M credential), which points at the **App** (the
running server). The agent calls the Service over the internal path; UC uses the Connection's creds
to authenticate into the App.*

- A **FastMCP** server hosted as its own Databricks App. Tools: `get_current_oil_price`,
  `get_oil_price_history`, `get_oil_price_trend`. Wraps the U.S. EIA API (WTI/Brent spot prices).
- **Auth:** `EIA_API_KEY` injected from a Databricks **secret** (`mfg-agent/eia-api-key`) via
  `app.yaml` `valueFrom` — never hardcoded.
- **Governed & reached** as a schema-scoped **UC MCP Service** `jywu.jywu_mfg_agent.eia_oil` (backed by
  a UC HTTP OAuth-M2M connection `eia_conn`), consumed over the internal
  `…/ai-gateway/mcp-services/jywu.jywu_mfg_agent.eia_oil` path — not the public app URL.
- **Why it matters:** resin prices track crude oil with a lag, so oil direction informs buy timing.

### 3.3 Pricing MCP — `mcp-jywu-pricing` (custom Databricks App)

```
       ┌─ UC MCP Service ┐    ┌─ UC Connection ──────┐    ┌─ Databricks App ─────┐
agent  │ pricing         │    │ pricing_conn         │    │ mcp-jywu-pricing     │ ─▶ Model Serving
─EXEC─▶│ governed handle │ ─▶ │ app URL + M2M        │ ─▶ │ FastMCP server       │    jywu-pricing-model
       │ /ai-gateway/…   │    │ creds = SP_connector │    │ predict_deal_quality │    (scale-to-zero)
       └─────────────────┘    └──────────────────────┘    └──────────────────────┘
       what the agent calls          reach + auth                 what runs
```
*Same three-object chain as §3.2; the App's tool calls a **Model Serving endpoint** (a fourth hop),
which needs the pricing app's SP granted `CAN_QUERY` on that endpoint.*

- A **FastMCP** app exposing `predict_deal_quality`, which calls the **Model Serving endpoint**
  `jywu-pricing-model`.
- **Model:** a `GradientBoostingClassifier` trained on `purchase_history` (good-deal vs bad-deal),
  registered in UC and served scale-to-zero. Logged with **explicit lean `pip_requirements`**
  (mlflow, scikit-learn, numpy, pandas, cloudpickle) so the serving container is tiny and builds fast.
- **Governed & reached** as a schema-scoped **UC MCP Service** `jywu.jywu_mfg_agent.pricing` (backed by
  connection `pricing_conn`), consumed over the internal `…/ai-gateway/mcp-services/…` path.
- **Two layers to remember:** the MCP app being up (tool connects/lists) is independent of the
  serving endpoint being READY (actual predictions). The pricing app's service principal needs
  `CAN_QUERY` on the endpoint, and the endpoint is scale-to-zero (first prediction after idle is slow).

### 3.4 AI Search "Procurement Playbook" — managed MCP (AI-functions ingestion)

```
ingest:  policy PDFs ─▶ ai_parse_document ─▶ ai_prep_search ─▶ procurement_doc_chunks ─▶ VS index
         (volume)       (parse)              (semantic chunk)  (Delta · CDF on)          …_chunks_index

query:   agent ─▶ AI Search MCP (managed · /api/2.0/mcp/ai-search/…) ─▶ VS index
```
*Managed MCP like §3.1 — no app/connection/service. The **ingest** row is the one-time build
pipeline; the **query** row is what happens per request.*

- Built with the **AI-functions RAG pipeline**: policy **PDFs** in the volume `policy_docs` →
  `ai_parse_document` → `ai_prep_search` (semantic chunking) → table `procurement_doc_chunks`
  (Change Data Feed on) → **Delta Sync Vector Search index** `procurement_doc_chunks_index`
  (qwen3 embeddings; embed `chunk_to_embed`, return `chunk_to_retrieve`).
- Exposed via the **managed** MCP path `/api/2.0/mcp/ai-search/jywu/jywu_mfg_agent/procurement_doc_chunks_index`.
- Grounds recommendations in company policy: approval thresholds, preferred suppliers,
  price-lock/MOQ clauses, safety-stock policy, payment terms, sustainability.
- (The earlier `procurement_docs_index` — a direct index over the `procurement_docs` table — is
  superseded by this PDF-sourced chunks index.)

---

## 4. Data layer (`jywu.jywu_mfg_agent`)

| Table | Rows | Purpose |
|---|---|---|
| `supplier_quotes` | 36 | vendor pricing, lead times, MOQ, validity |
| `inventory_levels` | 6 | stock, safety stock, days of supply, reorder point |
| `production_demand` | 9 | upcoming material requirements by month |
| `purchase_history` | 24 | past decisions with outcome labels (ML training data) |
| `procurement_docs` | 8 | Procurement Playbook rows → rendered to PDFs in the `policy_docs` volume |
| `procurement_doc_chunks` | 8 | `ai_parse_document`+`ai_prep_search` output over the PDFs → source of the Vector Search index |

Materials: HDPE, LDPE, PP · Suppliers: ChemCorp, PolySource, AsiaResin, EuroChem · Warehouses: Houston, Chicago.

**Other schema securables (all UC-governed under `jywu.jywu_mfg_agent`):** Model `pricing_model`;
Function `get_material_status`; MCP Services `eia_oil` / `pricing`; schema-scoped Connections
`eia_conn` / `pricing_conn`; Volume `policy_docs`. (Secrets stay in the workspace scope
`mfg-agent/eia-api-key` — a Spark-less app can't consume a UC schema secret; see §6 and §8.)

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

## 6. Authorization architecture

Every hop is authenticated and short-lived — **no PATs anywhere**. Four kinds of identity are involved:

| Principal | What it is | Used for |
|---|---|---|
| **End user** | The human in the chat UI (Databricks OAuth, U2M) | Reaching the agent app; the app's ACL (`CAN_USE`) gates who may call it. |
| **Agent app SP** | The service principal the `mfg-procurement-agent` app runs as | *All* the agent's outbound calls — LLM, managed MCPs, UC MCP Services, warehouse, Lakebase. |
| **Connector SP** | A separate SP whose static M2M OAuth creds are stored inside the UC connections (`eia_conn`, `pricing_conn`) | Authenticating the internal **UC MCP Service → custom MCP app** hop. |
| **MCP-app SPs** | Each custom MCP app (`mcp-jywu-eia-oil`, `mcp-jywu-pricing`) runs as its own SP | The pricing app SP calls the model-serving endpoint. |

**Token flow for one request**

```
  (1)  end user ──Databricks OAuth (U2M)──▶  agent app        [app ACL: user has CAN_USE]
                                                │  runs as AGENT APP SP
                                                ▼
       agent app SP makes each call with its own token, authorized independently:
         (2) LLM (Foundation Model serving) ...... CAN QUERY on the serving endpoint
         (3) UC Functions MCP  /api/2.0/mcp/… ..... EXECUTE on get_material_status  (+ USE CAT/SCH)
         (4) AI Search MCP     /api/2.0/mcp/… ..... read on the Vector Search index
         (5) UC MCP Services   /ai-gateway/…  ...... EXECUTE on eia_oil / pricing
         (8) Lakebase (Postgres) .................. Postgres grants (session memory)

       (5) then continues outbound to the custom MCP app, over the connection's creds:
         UC MCP Service ─(6) connector SP static M2M OAuth · CAN_USE on app─▶ EIA / pricing app
         pricing app    ─(7) pricing app SP · CAN_QUERY──────────────────────▶ model-serving endpoint
```

**Grants matrix — who needs what**

| Grantee | Grant | On |
|---|---|---|
| End user | `CAN_USE` | the agent app |
| Agent app SP | `CAN QUERY` | the LLM serving endpoint (Foundation Model) |
| Agent app SP | `USE CATALOG` + `USE SCHEMA` | `jywu.jywu_mfg_agent` |
| Agent app SP | `SELECT` | schema tables + the `procurement_doc_chunks_index` (AI Search MCP) |
| Agent app SP | `EXECUTE` | function `get_material_status` (UC Functions MCP) |
| Agent app SP | `EXECUTE` | MCP Services `eia_oil`, `pricing` |
| Agent app SP | `CAN_USE` | the SQL warehouse (if the function/queries route through it) |
| Agent app SP | Postgres `USAGE`/`CREATE` + table DML | Lakebase schemas (`scripts/grant_lakebase_permissions.py`) |
| Connector SP | `CAN_USE` | both custom MCP apps (creds live in the connections) |
| Pricing app SP | `CAN_QUERY` | serving endpoint `jywu-pricing-model` |

**Secrets.** The EIA API key lives in a **workspace secret scope** (`mfg-agent/eia-api-key`) and is
injected into the EIA app via `app.yaml` `valueFrom` — never hardcoded, never in git. (A Spark-less
app can't consume a UC *schema* secret; see §8.) The connector SP's client secret is passed to
`setup/create_uc_governance.py` via env (`CONNECTOR_SP_SECRET`) and stored inside the UC connection
object, so it isn't in the repo either.

### 6.1 How the agent reaches the custom MCP servers — and when a UC connection is needed

A custom MCP server is a Databricks App with a public URL (`https://<app>.databricksapps.com/mcp`).
There are two ways to reach it:

**Via a UC connection + UC MCP Service — what this deployment uses.** The two custom MCPs are
registered as schema-scoped **UC MCP Services** (`eia_oil`, `pricing`) backed by HTTP OAuth-M2M
**connections** (`eia_conn`, `pricing_conn`). The agent calls the internal
`…/ai-gateway/mcp-services/<catalog.schema.name>` path: UC checks the agent SP's `EXECUTE` grant on
the service, then uses the connection's connector-SP M2M credentials to authenticate outbound to the
app (steps 5→6 above). This keeps the tools UC-governed and doesn't depend on public app egress.

**Direct by URL — the simpler alternative.** The agent's `McpServer(url=...)` calls the app's public
URL over OAuth directly. It works only where app-to-app egress is open (as it happens to be on this
Azure workspace) and gives you no UC governance. Prefer the UC MCP Service path whenever any of these hold:

| Situation | Why the UC MCP Service path is required |
|---|---|
| **Restricted app egress** (NCC/Private Link, or workspaces that block outbound to `*.databricksapps.com`) | A URL-calling agent can't reach the public app — you hit `serverless network policy` / connection-refused. The internal path is fetched by the control plane, which isn't subject to the app's egress block. (This was the blocker on the earlier fevm workspace.) |
| **UC governance** over the tool | Grants, ownership, and auditing on the tool like any other catalog object. |

**Auth for the connection:** Databricks does **not** support Dynamic Client Registration for custom
MCP servers, so the connection uses **static M2M OAuth** — the connector SP's `client_id` + secret with
`CAN_USE` on the app, wired as `client_credentials` against the workspace `/oidc/v1/token` endpoint.
(PATs are not accepted for custom MCPs on Apps.)

> **Note:** the **AI Search Playbook** and **UC Functions** tools use built-in managed-MCP paths
> (`/api/2.0/mcp/ai-search/…`, `/api/2.0/mcp/functions/…`) and need no connection — the UC-connection
> question only concerns the two **custom** app-hosted MCP servers (EIA, pricing).

---

## 7. Deployment

- **Agent app:** Databricks Asset Bundle (`databricks.yml`) → `databricks bundle deploy && run`.
  Runtime config (LLM model, catalog/schema/warehouse, MCP URLs, Lakebase, experiment) lives in
  the bundle's `config.env`.
- **MCP servers:** deployed as separate Databricks Apps (`apps create` + `sync` + `apps deploy`).
- **Prep runbook:** `setup/README.md` has the full ordered sequence — data, pricing model, policy
  PDFs, AI-functions chunks index, UC governance (connections/services/function), and grants.

---

## 8. Key design decisions & lessons

| Decision / lesson | Why |
|---|---|
| Custom tools as **MCP servers on Apps** | Field-recommended for reusable tools; lower latency than UC-function tools. |
| **Direct FM serving** vs AI Gateway | Azure has the FM path enabled; env flag switches to the AI Gateway where it's disabled. |
| **Lean `pip_requirements`** on the model | Inferred requirements captured the whole project venv (150+ pkgs incl. PySpark) → huge, slow serving image. Explicit 5-package reqs build fast anywhere. |
| **Managed** AI Search MCP for retrieval | Endorsed retrieval path; internal, no egress concerns. |
| No `uv.lock` in the app deploy | Build's uv version differs from local → `--locked` mismatch; let the build resolve from `pyproject.toml`. |
| Separate MLflow experiments | Agent tracing vs ML model runs are different concerns. |
| **Schema-scoped UC MCP Services** for the custom MCPs | Field guidance (Unity Gateway): metastore MCP connections are being deprecated; register as `catalog.schema.name` MCP Services (GRANTs, usage tracking, internal path). |
| **UC function via the Functions MCP** for internal data | Governed + discoverable; function-qualify the param inside subqueries or it fails under MCP named-arg invocation. |
| **EIA key stays in a workspace secret scope** | A Spark-less MCP app can't consume a UC schema secret (`dbutils.secrets.get` is notebook/Spark-only; no Apps binding); the workspace scope injects via `valueFrom`. |
| **AI-functions ingestion** for the Playbook | `ai_parse_document`+`ai_prep_search` over PDFs → chunks → index is the canonical RAG path; verify function `version` options against docs (they move fast). |
