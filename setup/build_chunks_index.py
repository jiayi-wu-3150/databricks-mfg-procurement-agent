"""Prep step — AI-functions ingestion: policy PDFs -> chunks -> Vector Search index.

Runs the recommended Databricks RAG-from-documents pipeline entirely with AI
functions, then builds a Delta Sync Vector Search index the agent retrieves from
(the "Procurement Playbook"):

    read_files(policy_docs volume, binaryFile)
      -> ai_parse_document(content, version 2.0)
      -> ai_prep_search(parsed, version 2.0)         # semantic chunking
      -> explode document.contents
      -> table  <schema>.procurement_doc_chunks       (CDF enabled)
      -> Delta Sync VS index  <schema>.procurement_doc_chunks_index
         (embed chunk_to_embed, return chunk_to_retrieve)

Prereqs: policy PDFs already in the volume (see gen_policy_docs.py); warehouse on
DBR 18.2+ / serverless env v3+ for ai_prep_search; VS endpoint from config.
NOTE: AI-function versions move fast — verify `version` option + output schema
against docs.databricks.com before changing.

Run:  DATABRICKS_CONFIG_PROFILE=azure-demo uv run python -m setup.build_chunks_index
"""

import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

from setup.config import CATALOG, SCHEMA, WAREHOUSE_ID, VS_ENDPOINT, EMBEDDING_ENDPOINT

w = WorkspaceClient()
CHUNKS = f"{CATALOG}.{SCHEMA}.procurement_doc_chunks"
INDEX = f"{CATALOG}.{SCHEMA}.procurement_doc_chunks_index"
VOLUME_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/policy_docs"


def run(sql, label, waits=18):
    r = w.statement_execution.execute_statement(warehouse_id=WAREHOUSE_ID, statement=sql, wait_timeout="50s")
    for _ in range(waits):
        if r.status.state in (StatementState.SUCCEEDED, StatementState.FAILED, StatementState.CANCELED):
            break
        time.sleep(10)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"{label}: {r.status.state} — {r.status.error.message if r.status.error else ''}")
    print(f"  ✓ {label}")
    return r


# 1) Parse + chunk the PDFs into a Delta table (LLM-billed; materialize once).
run(f"""CREATE OR REPLACE TABLE {CHUNKS} AS
WITH prepped AS (
  SELECT path AS source_path,
         ai_prep_search(ai_parse_document(content, map('version','2.0')), map('version','2.0')) AS prep
  FROM read_files('{VOLUME_DIR}/', format => 'binaryFile')
)
SELECT variant_get(chunk,'$.chunk_id','STRING')          AS chunk_id,
       variant_get(chunk,'$.chunk_to_retrieve','STRING') AS chunk_to_retrieve,
       variant_get(chunk,'$.chunk_to_embed','STRING')    AS chunk_to_embed,
       source_path
FROM prepped LATERAL VIEW explode(variant_get(prep,'$.document.contents','ARRAY<VARIANT>')) c AS chunk""",
    "parse + prep_search -> procurement_doc_chunks")

# 2) Change Data Feed is required for a Delta Sync index.
run(f"ALTER TABLE {CHUNKS} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)", "enable CDF")

# 3) Delta Sync Vector Search index — embed chunk_to_embed, return chunk_to_retrieve.
try:
    w.api_client.do("POST", "/api/2.0/vector-search/indexes", body={
        "name": INDEX,
        "endpoint_name": VS_ENDPOINT,
        "primary_key": "chunk_id",
        "index_type": "DELTA_SYNC",
        "delta_sync_index_spec": {
            "source_table": CHUNKS,
            "pipeline_type": "TRIGGERED",
            "embedding_source_columns": [
                {"name": "chunk_to_embed", "embedding_model_endpoint_name": EMBEDDING_ENDPOINT}
            ],
        },
    })
    print(f"  ✓ created index {INDEX}")
except Exception as e:
    print(f"  index note (may already exist): {str(e)[:150]}")

print(f"\nDone. Point the agent's AI_SEARCH_INDEX at {INDEX} once it is ONLINE.")
