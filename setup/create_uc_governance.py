"""Prep step — UC governance for the agent's tools: connections, MCP services, function, grants.

Registers the two app-hosted MCP servers as governed, schema-scoped UC MCP
Services (recommended over metastore-level MCP connections), creates the
internal-data SQL function, and grants the agent's service principal access.

    schema-scoped UC Connection (HTTP OAUTH_M2M)   <schema>.eia_conn / .pricing_conn
      -> UC MCP Service                            <schema>.eia_oil / .pricing
    SQL function                                   <schema>.get_material_status(material)
    grants: agent app SP gets EXECUTE on the services + the function

Consumed by the agent as internal paths (no public-URL egress):
  - MCP services : {host}/ai-gateway/mcp-services/<schema>.eia_oil | .pricing
  - UC functions : {host}/api/2.0/mcp/functions/<catalog>/<schema>   (exposes get_material_status)

PREREQS (one-time, not done here because they involve credentials/humans):
  1. The two MCP apps are deployed: mcp-jywu-eia-oil, mcp-jywu-pricing.
  2. A "connector" service principal exists with an OAuth (M2M) secret and CAN_USE
     on both MCP apps. Provide its creds via env:
         CONNECTOR_SP_CLIENT_ID, CONNECTOR_SP_SECRET
  3. NOTE: schema-level connections CANNOT set is_mcp_connection — the MCP Service
     wraps a plain HTTP OAUTH_M2M connection.

Run:  DATABRICKS_CONFIG_PROFILE=azure-demo \
      CONNECTOR_SP_CLIENT_ID=... CONNECTOR_SP_SECRET=... \
      uv run python -m setup.create_uc_governance
"""

import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from setup.config import CATALOG, SCHEMA, WORKSPACE_URL, WAREHOUSE_ID

w = WorkspaceClient()
SCHEMA_FQN = f"{CATALOG}.{SCHEMA}"
TOKEN_ENDPOINT = f"{WORKSPACE_URL}/oidc/v1/token"

# app name -> (connection leaf, service leaf)
APPS = {
    "mcp-jywu-eia-oil": ("eia_conn", "eia_oil"),
    "mcp-jywu-pricing": ("pricing_conn", "pricing"),
}
AGENT_APP = "mfg-procurement-agent"

# Final, MCP-safe SQL function: parameter is function-qualified inside the subqueries
# (get_material_status.material) with table aliases, so it resolves under the named-arg
# invocation the UC Functions MCP uses.
GET_MATERIAL_STATUS = f"""CREATE OR REPLACE FUNCTION {SCHEMA_FQN}.get_material_status(material STRING)
RETURNS STRING
COMMENT 'Procurement status for a PE resin (HDPE/LDPE/PP): total on-hand inventory, cheapest recent supplier quote, and next-month demand.'
RETURN (SELECT concat_ws(char(10),
  concat('Material status for ', upper(material), ':'),
  concat('- Inventory on hand: ', coalesce(cast((SELECT sum(i.quantity_tons) FROM {SCHEMA_FQN}.inventory_levels i WHERE i.material=upper(get_material_status.material)) AS STRING),'n/a'), ' tons'),
  concat('- Cheapest recent quote: ', coalesce((SELECT concat(q.supplier,' at $',cast(round(q.price_usd_ton,2) AS STRING),'/ton') FROM {SCHEMA_FQN}.supplier_quotes q WHERE q.material=upper(get_material_status.material) ORDER BY q.quote_date DESC, q.price_usd_ton ASC LIMIT 1),'none')),
  concat('- Next-month demand: ', coalesce(cast((SELECT d.required_tons FROM {SCHEMA_FQN}.production_demand d WHERE d.material=upper(get_material_status.material) ORDER BY d.production_month ASC LIMIT 1) AS STRING),'n/a'), ' tons')))"""


def sql(statement, label):
    r = w.statement_execution.execute_statement(warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="40s")
    if r.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"{label}: {r.status.state} — {r.status.error.message if r.status.error else ''}")
    print(f"  ✓ {label}")


def app_url(name):
    return w.apps.get(name).url.rstrip("/")


def app_sp(name):
    return w.apps.get(name).service_principal_client_id


def main():
    client_id = os.environ["CONNECTOR_SP_CLIENT_ID"]
    secret = os.environ["CONNECTOR_SP_SECRET"]
    agent_sp = app_sp(AGENT_APP)

    for app, (conn, svc) in APPS.items():
        host = app_url(app).replace("https://", "")
        # 1) schema-scoped HTTP OAUTH_M2M connection (no is_mcp_connection at schema level)
        try:
            w.api_client.do("POST", "/api/2.1/unity-catalog/connections",
                query={"parent": f"schemas/{SCHEMA_FQN}"},
                body={"name": conn, "connection_type": "HTTP", "options": {
                    "host": f"https://{host}", "port": "443", "base_path": "/mcp",
                    "client_id": client_id, "client_secret": secret,
                    "oauth_scope": "all-apis", "token_endpoint": TOKEN_ENDPOINT}})
            print(f"  ✓ connection {SCHEMA_FQN}.{conn}")
        except Exception as e:
            print(f"  connection {conn} note: {str(e)[:120]}")
        # 2) UC MCP Service wrapping that connection
        try:
            w.api_client.do("POST", "/api/2.1/unity-catalog/mcp-services",
                query={"parent": f"schemas/{SCHEMA_FQN}", "mcp_service_id": svc},
                body={"config": {"source_connection": {"name": f"connections/{SCHEMA_FQN}.{conn}"}}})
            print(f"  ✓ mcp-service {SCHEMA_FQN}.{svc}")
        except Exception as e:
            print(f"  mcp-service {svc} note: {str(e)[:120]}")
        # 3) grant agent SP EXECUTE on the service
        w.api_client.do("PATCH", f"/api/2.1/unity-catalog/permissions/mcp_service/{SCHEMA_FQN}.{svc}",
            body={"changes": [{"principal": agent_sp, "add": ["EXECUTE"]}]})
        print(f"  ✓ EXECUTE {SCHEMA_FQN}.{svc} -> agent SP")

    # 4) the internal-data SQL function + EXECUTE grant
    sql(GET_MATERIAL_STATUS, "function get_material_status")
    sql(f"GRANT EXECUTE ON FUNCTION {SCHEMA_FQN}.get_material_status TO `{agent_sp}`", "EXECUTE function -> agent SP")

    print("\nDone. Agent env: MCP_EIA_URL / MCP_PRICING_URL -> the /ai-gateway/mcp-services/ paths; "
          "the UC Functions MCP (/api/2.0/mcp/functions/) exposes get_material_status.")


if __name__ == "__main__":
    main()
