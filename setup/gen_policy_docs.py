"""Materialize the Procurement Playbook as PDF policy documents in a UC Volume.

Reads the `procurement_docs` table (the rows that back the AI Search index) and
writes one PDF per policy into the volume `<catalog>.<schema>.policy_docs`, i.e.
`/Volumes/<catalog>/<schema>/policy_docs/`. This gives the demo real source
documents (a Volume you can parse / re-index), derived from the same content the
Vector Search index uses.

Run:  DATABRICKS_CONFIG_PROFILE=azure-demo \
      uv run --with fpdf2 python -m setup.gen_policy_docs
"""

import io
import re

from fpdf import FPDF
from databricks.sdk import WorkspaceClient

from setup.config import CATALOG, SCHEMA, WAREHOUSE_ID, TABLE_PROCUREMENT_DOCS

VOLUME_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/policy_docs"
w = WorkspaceClient()


def _latin1(s: str) -> str:
    """fpdf2 core fonts are latin-1; swap common unicode punctuation to ASCII."""
    repl = {"‘": "'", "’": "'", "“": '"', "”": '"',
            "–": "-", "—": "-", "…": "...", "•": "-", "⚠": "!"}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")


def build_pdf(doc_id, title, category, content) -> bytes:
    pdf = FPDF(format="letter")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(150, 90, 40)
    pdf.cell(0, 6, _latin1(f"PROCUREMENT PLAYBOOK  -  {category.upper()}"), ln=True)
    pdf.set_text_color(20, 25, 35)
    pdf.set_font("Helvetica", "B", 17)
    pdf.multi_cell(0, 9, _latin1(title))
    pdf.ln(2)
    pdf.set_draw_color(200, 195, 180)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 11.5)
    pdf.multi_cell(0, 6.5, _latin1(content))
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(130, 130, 130)
    pdf.cell(0, 5, _latin1(f"Policy #{doc_id}  |  {CATALOG}.{SCHEMA}.procurement_docs"), ln=True)
    return bytes(pdf.output())


def main():
    rows = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"SELECT id, title, category, content FROM {TABLE_PROCUREMENT_DOCS} ORDER BY id",
        wait_timeout="30s",
    ).result.data_array or []
    print(f"{len(rows)} policy docs -> {VOLUME_DIR}")
    for doc_id, title, category, content in rows:
        pdf_bytes = build_pdf(doc_id, title, category, content)
        dest = f"{VOLUME_DIR}/{int(doc_id):02d}_{_slug(title)}.pdf"
        w.files.upload(dest, io.BytesIO(pdf_bytes), overwrite=True)
        print(f"  wrote {dest}  ({len(pdf_bytes):,} bytes)")
    print("done.")


if __name__ == "__main__":
    main()
