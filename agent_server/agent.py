import logging
import os
from contextlib import AsyncExitStack
from datetime import datetime
from typing import AsyncGenerator

import mlflow
from agents import Agent, Runner, function_tool, set_default_openai_api, set_default_openai_client
from agents.tracing import set_trace_processors
from databricks.sdk import WorkspaceClient
from databricks_openai import AsyncDatabricksOpenAI
from databricks_openai.agents import AsyncDatabricksSession, McpServer
from agents.mcp import create_static_tool_filter
from fastapi import HTTPException
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from agent_server.utils import (
    deduplicate_input,
    get_databricks_host_from_env,
    get_lakebase_access_error_message,
    get_session_id,
    get_user_workspace_client,
    lakebase_config,
    process_agent_stream_events,
)


# LLM transport is env-driven: on workspaces where the Foundation-Model serving path is
# disabled (e.g. fevm) set USE_AI_GATEWAY=true to route via the Unity AI Gateway with a
# system.ai.* model; where FM serving is enabled (e.g. Azure) use the direct path (default).
_USE_AI_GATEWAY = os.environ.get("USE_AI_GATEWAY", "false").lower() == "true"
set_default_openai_client(
    AsyncDatabricksOpenAI(use_ai_gateway=True) if _USE_AI_GATEWAY else AsyncDatabricksOpenAI()
)
set_default_openai_api("chat_completions")
set_trace_processors([])  # only use mlflow for trace processing
mlflow.openai.autolog()
logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

# Default to the direct FM serving model on Azure; override via env per workspace.
LLM_MODEL = os.environ.get("AGENT_LLM_MODEL", "databricks-claude-sonnet-4-5")

# AI Search "Procurement Playbook" managed MCP (Delta Sync index over procurement_docs).
AI_SEARCH_INDEX = os.environ.get(
    "AI_SEARCH_INDEX", "jywu.jywu_mfg_agent.procurement_docs_index"
)

AGENT_INSTRUCTIONS = """You are a procurement advisor for a plastics manufacturer that buys \
polyethylene resins (HDPE, LDPE, PP). You help procurement leads decide whether to buy or wait, \
how much, and from which supplier.

Tools available to you:
- get_material_status(material): internal data — on-hand inventory (with safety stock, reorder \
  points, days of supply per warehouse), the cheapest current supplier quote, and near-term \
  production demand.
- Oil price tools (EIA): current WTI/Brent spot price, history, and trend. Resin prices track \
  crude oil with a lag, so oil direction informs buy timing.
- Pricing tools: an ML model that predicts whether a given price is a Good Deal or a Bad Deal.
- AI Search "Procurement Playbook": company procurement policy and contract rules — approval \
  thresholds, preferred suppliers, price-lock/MOQ clauses, safety-stock policy, payment terms.

For a buy/wait recommendation, combine the sources: (1) call get_material_status to see whether \
the material is even needed and what's quoted; (2) check the oil price/trend for timing; (3) run \
the pricing model on the quoted price; (4) ALWAYS consult the Procurement Playbook via AI Search \
for the relevant policy (approval thresholds, preferred supplier, contract terms) and ground your \
advice in it. Then give a concise recommendation: buy or wait, quantity, supplier, and any \
approvals required — citing the specific numbers you used. If a tool is unavailable, proceed with \
the others and note what's missing."""


@function_tool
def get_current_time() -> str:
    """Get the current date and time."""
    return datetime.now().isoformat()


def build_mcp_servers(workspace_client: WorkspaceClient) -> list[McpServer]:
    """MCP servers to offer the agent. Unavailable ones are dropped by the health check, so the
    EIA/pricing app MCPs can be listed before those apps are deployed (set via env once they are).
    """
    host = (get_databricks_host_from_env() or "").rstrip("/")
    servers: list[McpServer] = []

    # AI Search "Procurement Playbook" (managed MCP)
    cat, sch, idx = AI_SEARCH_INDEX.split(".")
    servers.append(McpServer(
        url=f"{host}/api/2.0/mcp/ai-search/{cat}/{sch}/{idx}",
        name="procurement_playbook_search",
        workspace_client=workspace_client,
    ))

    # UC Functions MCP — governed UC functions in the schema (e.g. get_material_status)
    servers.append(McpServer(
        url=f"{host}/api/2.0/mcp/functions/{cat}/{sch}",
        name="uc_functions",
        workspace_client=workspace_client,
    ))

    # UC Skills (Beta) — governed catalog.schema.skill objects, loaded live over the
    # managed skills MCP (docs: "load a schema live"). Restricted to the read tools so the
    # agent can list/load skills but never create/update/delete them.
    if os.environ.get("SKILLS_MCP_ENABLED", "true").lower() == "true":
        servers.append(McpServer(
            url=f"{host}/ai-gateway/skills/{cat}.{sch}",
            name="uc_skills",
            workspace_client=workspace_client,
            tool_filter=create_static_tool_filter(
                allowed_tool_names=["list_skills", "load_skill", "get_skill_files"]
            ),
        ))

    # Custom app MCPs (EIA oil prices, ML pricing) — URLs provided post-deploy via env.
    for env_key, name in [("MCP_EIA_URL", "eia_oil"), ("MCP_PRICING_URL", "pricing")]:
        url = os.environ.get(env_key)
        if url:
            servers.append(McpServer(url=url, name=name, workspace_client=workspace_client))

    return servers


async def connect_healthy_mcp_servers(
    stack: AsyncExitStack, servers: list[McpServer]
) -> tuple[list[McpServer], list[str]]:
    """Connect each MCP server and verify it can actually list its tools.

    The Agents SDK lists each server's tools lazily inside ``Runner.run``, so a server that
    connects but fails at list time (e.g. an unauthorized Genie space) would otherwise crash
    the whole request — including unrelated turns. We list tools here, per server: healthy
    servers are kept; any that fails to connect OR to list is dropped and its name returned,
    so the agent runs with whatever is available instead of erroring out.

    Returns (healthy_servers, unavailable_names).
    """
    healthy: list[McpServer] = []
    unavailable: list[str] = []
    for server in servers:
        name = getattr(server, "name", "MCP server")
        try:
            connected = await stack.enter_async_context(server)
            await connected.list_tools()  # forces the connectivity + authorization check now
            healthy.append(connected)
        except Exception:
            logger.warning("MCP server %r unavailable; continuing without it.", name, exc_info=True)
            unavailable.append(name)
    return healthy, unavailable


def create_agent(mcp_servers: list[McpServer] | None = None) -> Agent:
    return Agent(
        name="Procurement Advisor",
        instructions=AGENT_INSTRUCTIONS,
        model=LLM_MODEL,
        tools=[get_current_time],  # get_material_status now served via the UC Functions MCP
        mcp_servers=mcp_servers or [],
    )


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    try:
        # Create session for stateful, short-term conversation history with your Databricks Lakebase instance
        session_id = get_session_id(request)
        if session_id:
            mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})
        session = AsyncDatabricksSession(
            session_id=session_id,
            autoscaling_endpoint=lakebase_config.autoscaling_endpoint,
            project=lakebase_config.autoscaling_project,
            branch=lakebase_config.autoscaling_branch,
            schema=lakebase_config.memory_schema,
            create_tables=False,  # Tables created at startup in start_server.py
        )

        # The agent runs inside an AsyncExitStack so any MCP servers stay open for the whole
        # request. To give the agent MCP tools, connect them with connect_healthy_mcp_servers,
        # which health-checks each server so one unavailable server can't crash the request
        # (the Agents SDK lists each server's tools lazily inside Runner.run):
        #   servers, unavailable = await connect_healthy_mcp_servers(
        #       stack, [await init_mcp_server(WorkspaceClient())])
        #   agent = create_agent(mcp_servers=servers)
        # WorkspaceClient() uses service principal credentials; use get_user_workspace_client()
        # for on-behalf-of user authentication.
        async with AsyncExitStack() as stack:
            servers, unavailable = await connect_healthy_mcp_servers(stack, build_mcp_servers(WorkspaceClient()))
            if unavailable:
                logger.info("MCP servers unavailable (continuing without): %s", unavailable)
            agent = create_agent(mcp_servers=servers)
            messages = await deduplicate_input(request, session)
            result = await Runner.run(agent, messages, session=session)
        return ResponsesAgentResponse(
            output=[item.to_input_item() for item in result.new_items],
            custom_outputs={"session_id": session.session_id},
        )
    except Exception as e:
        error_msg = str(e).lower()
        if any(
            keyword in error_msg
            for keyword in ["lakebase", "pg_hba", "postgres", "database instance", "insufficient privilege"]
        ):
            logger.error("Lakebase access error: %s", e)
            raise HTTPException(
                status_code=503,
                detail=get_lakebase_access_error_message(lakebase_config.description),
            ) from e
        raise


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    try:
        # Create session for stateful, short-term conversation history with your Databricks Lakebase instance
        session_id = get_session_id(request)
        if session_id:
            mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})
        session = AsyncDatabricksSession(
            session_id=session_id,
            autoscaling_endpoint=lakebase_config.autoscaling_endpoint,
            project=lakebase_config.autoscaling_project,
            branch=lakebase_config.autoscaling_branch,
            schema=lakebase_config.memory_schema,
            create_tables=False,  # Tables created at startup in start_server.py
        )

        # The agent runs inside an AsyncExitStack so any MCP servers stay open for the whole
        # request. To give the agent MCP tools, connect them with connect_healthy_mcp_servers,
        # which health-checks each server so one unavailable server can't crash the request
        # (the Agents SDK lists each server's tools lazily inside Runner.run):
        #   servers, unavailable = await connect_healthy_mcp_servers(
        #       stack, [await init_mcp_server(WorkspaceClient())])
        #   agent = create_agent(mcp_servers=servers)
        # WorkspaceClient() uses service principal credentials; use get_user_workspace_client()
        # for on-behalf-of user authentication.
        async with AsyncExitStack() as stack:
            servers, unavailable = await connect_healthy_mcp_servers(stack, build_mcp_servers(WorkspaceClient()))
            if unavailable:
                logger.info("MCP servers unavailable (continuing without): %s", unavailable)
            agent = create_agent(mcp_servers=servers)
            messages = await deduplicate_input(request, session)
            result = Runner.run_streamed(agent, input=messages, session=session)

            async for event in process_agent_stream_events(result.stream_events()):
                yield event
    except Exception as e:
        error_msg = str(e).lower()
        if any(
            keyword in error_msg
            for keyword in ["lakebase", "pg_hba", "postgres", "database instance", "insufficient privilege"]
        ):
            logger.error("Lakebase access error: %s", e)
            raise HTTPException(
                status_code=503,
                detail=get_lakebase_access_error_message(lakebase_config.description),
            ) from e
        raise
