# Setup / prep runbook

Reproducible prep for the Manufacturing Procurement Agent, in order. All scripts read
`setup/config.py` (workspace, catalog=`jywu`, schema=`jywu_mfg_agent`, warehouse, VS endpoint,
embedding model). Run each with the workspace profile, e.g. `DATABRICKS_CONFIG_PROFILE=azure-demo`.

> AI-function versions move fast — verify `ai_parse_document` / `ai_prep_search` `version` options
> and output schema against docs.databricks.com before editing `build_chunks_index.py`.

| # | Step | Command | Creates |
|---|------|---------|---------|
| 1 | Config | edit `setup/config.py` | workspace/catalog/schema/warehouse/VS/embedding |
| 2 | Auth + Lakebase + MLflow | `uv run quickstart --profile <p> --lakebase-create-new jywu-mfg-agent` | `.env`, experiment, Lakebase project |
| 3 | Data (5 tables) | `uv run python -m setup.gen_data_fevm` | supplier_quotes, inventory_levels, production_demand, purchase_history, procurement_docs |
| 4 | Pricing model + endpoint | `uv run --with scikit-learn --with azure-storage-file-datalake --with azure-identity python -m setup.train_pricing_fevm` | UC model `pricing_model` + serving endpoint `jywu-pricing-model` (lean pip_requirements) |
| 5 | Policy PDFs → Volume | `uv run --with fpdf2 python -m setup.gen_policy_docs` | volume `policy_docs` + 8 PDFs |
| 6 | AI-functions ingestion | `uv run python -m setup.build_chunks_index` | `procurement_doc_chunks` (parse→prep_search) + VS index `procurement_doc_chunks_index` |
| 7 | MCP apps | see **MCP apps** below | apps `mcp-jywu-eia-oil`, `mcp-jywu-pricing` |
| 8 | Connector SP + secret | see **Connector SP** below | SP + OAuth M2M secret, `CAN_USE` on the MCP apps |
| 9 | UC governance | `CONNECTOR_SP_CLIENT_ID=… CONNECTOR_SP_SECRET=… uv run python -m setup.create_uc_governance` | schema-scoped connections, UC MCP Services (eia_oil/pricing), UC function `get_material_status`, EXECUTE grants |
| 10 | Deploy agent | `databricks bundle deploy && databricks bundle run agent_openai_advanced` | app `mfg-procurement-agent` (no `uv.lock`; no stale `.databricks/`) |
| 11 | Grants (agent SP) | warehouse `CAN_USE`, UC `USE/SELECT` on schema, Lakebase perms | `uv run python scripts/grant_lakebase_permissions.py <sp> --memory-type openai` |

The agent (`databricks.yml` `config.env`) then points at: the UC MCP Services
(`MCP_EIA_URL`/`MCP_PRICING_URL` → `…/ai-gateway/mcp-services/jywu.jywu_mfg_agent.{eia_oil,pricing}`),
the chunks index (`AI_SEARCH_INDEX`), and the UC Functions MCP (built in code, exposing
`get_material_status`). LLM = `databricks-claude-sonnet-4-5` (direct FM serving; `USE_AI_GATEWAY=false`).

## MCP apps (step 7)
Each MCP server is a Databricks App (`mcp_server_eia/`, `mcp_server_pricing/`):
```bash
# EIA app needs its API key as a secret resource (workspace scope mfg-agent/eia-api-key):
databricks apps create --json '{"name":"mcp-jywu-eia-oil","resources":[{"name":"eia-api-key","secret":{"scope":"mfg-agent","key":"eia-api-key","permission":"READ"}}]}'
databricks apps create mcp-jywu-pricing
# for each: sync source to a workspace path, then deploy
databricks sync ./mcp_server_eia /Workspace/Users/<you>/mfg-mcp/mcp_server_eia
databricks apps deploy mcp-jywu-eia-oil --source-code-path /Workspace/Users/<you>/mfg-mcp/mcp_server_eia
# (repeat for mcp_server_pricing; grant the pricing app SP CAN_QUERY on jywu-pricing-model)
```
The EIA key lives in the **workspace secret scope** `mfg-agent/eia-api-key` (a Spark-less app
can't consume a UC schema secret; see architecture.md §Secrets).

## Connector SP (step 8)
```bash
databricks service-principals create --display-name mfg-mcp-connector      # -> application_id
databricks service-principal-secrets-proxy create <sp-id>                   # -> secret (save it)
# grant that SP CAN_USE on both MCP apps (permissions API), then use its id/secret in step 9
```

## Notes / gotchas
- **No `uv.lock`** in the agent deploy (build's uv version trips `--locked`); it's gitignored.
- **Clear `.databricks/`** if copied from another workspace (stale bundle state → CLI panic).
- **Model serving image**: keep `pip_requirements` lean in `train_pricing_fevm.py` (else the
  serving container drags PySpark/150+ pkgs and the build stalls).
- Azure UC managed storage is `abfss://` — training needs `azure-storage-file-datalake` + `azure-identity`.
