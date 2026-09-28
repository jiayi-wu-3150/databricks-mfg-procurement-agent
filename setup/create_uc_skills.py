"""Prep step — publish the agent's domain skills as Unity Catalog Skills (Beta).

Registers each skill folder under `./skills/` as a governed UC Skill securable
(`catalog.schema.skill`) in `jywu.jywu_mfg_agent`, using the same three-step REST
flow the official "sync-git-skills-to-uc" notebook uses:

    1. create    POST /api/2.1/unity-catalog/skills?parent=schemas/<cat>.<sch>&skill_id=<leaf>
    2. upload    PUT  /api/2.0/fs/files/Skills/<cat>/<sch>/<leaf>/<relpath>   (per file)
    3. finalize  POST /api/2.1/unity-catalog/skills/<cat>.<sch>.<leaf>/finalize
                 (reads the uploaded SKILL.md frontmatter: name: must equal the
                  folder name, description: <= 1024 bytes)

Unlike the marketplace notebook, this reads the local `./skills/` folder directly
(no .claude-plugin/marketplace.json, no Git-folder checkout) — the skills already
live in this repo.

PREREQS:
  - UC Skills (Beta) enabled on the workspace (account console preview).
  - USE SCHEMA + CREATE VOLUME on jywu.jywu_mfg_agent (skills are volume-backed).

Run:  DATABRICKS_CONFIG_PROFILE=azure-demo uv run python -m setup.create_uc_skills
      DATABRICKS_CONFIG_PROFILE=azure-demo uv run python -m setup.create_uc_skills --list
"""

import re
import sys
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import AlreadyExists

from setup.config import CATALOG, SCHEMA

w = WorkspaceClient()
api = w.api_client
SCHEMA_FQN = f"{CATALOG}.{SCHEMA}"
SKILLS_API = "/api/2.1/unity-catalog/skills"
FILES_API = "/api/2.0/fs/files"
AGENT_APP = "agent-mfg-procurement"  # its SP gets READ_VOLUME so the deployed agent can load skills
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
# Skill leaf: lowercase alphanumerics + inner hyphens, up to 64 chars (per docs).
LEAF_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")


def read_bundle(skill_dir: Path) -> dict[str, bytes]:
    """All files under the skill folder, keyed by path relative to that folder."""
    return {
        str(f.relative_to(skill_dir)): f.read_bytes()
        for f in sorted(skill_dir.rglob("*"))
        if f.is_file() and ".DS_Store" not in f.name
    }


def publish(leaf: str, bundle: dict[str, bytes]) -> str:
    # 1) create the securable (re-publish surfaces AlreadyExists — that's fine)
    try:
        api.do("POST", SKILLS_API,
               query={"parent": f"schemas/{SCHEMA_FQN}", "skill_id": leaf}, body={})
        action = "created"
    except AlreadyExists:
        action = "updated"
    # 2) upload every file in the bundle to the skill's /Skills/ path
    for rel_path, content in bundle.items():
        api.do("PUT", f"{FILES_API}/Skills/{CATALOG}/{SCHEMA}/{leaf}/{rel_path}",
               headers={"Content-Type": "application/octet-stream"}, data=content)
    # 3) finalize — reads SKILL.md frontmatter (name/description)
    api.do("POST", f"{SKILLS_API}/{SCHEMA_FQN}.{leaf}/finalize")
    return action


def grant_agent_read(leaves: list[str]) -> None:
    """Grant the agent app's SP READ_VOLUME on each skill (skills reuse volume privileges),
    so the deployed agent can list/load them over the managed skills MCP."""
    try:
        agent_sp = w.apps.get(AGENT_APP).service_principal_client_id
    except Exception as e:
        print(f"  (skip grants — app {AGENT_APP} not found: {str(e)[:80]})")
        return
    for leaf in leaves:
        api.do("PATCH", f"/api/2.1/unity-catalog/permissions/skill/{SCHEMA_FQN}.{leaf}",
               body={"changes": [{"principal": agent_sp, "add": ["READ_VOLUME"]}]})
    print(f"  ✓ READ_VOLUME on {len(leaves)} skill(s) -> agent SP")


def list_skills():
    resp = api.do("GET", SKILLS_API, query={"parent": f"schemas/{SCHEMA_FQN}"})
    for s in resp.get("skills", []):
        print(f"  {s['name']}  (bundle={s.get('bundle_name','?')})  {s.get('description','')[:70]}")
    if not resp.get("skills"):
        print("  (none)")


def main():
    if "--list" in sys.argv:
        print(f"Skills in {SCHEMA_FQN}:")
        list_skills()
        return

    skill_dirs = sorted(d for d in SKILLS_DIR.iterdir()
                        if d.is_dir() and (d / "SKILL.md").is_file())
    print(f"Publishing {len(skill_dirs)} skill(s) to {SCHEMA_FQN}\n")
    errors = []
    for d in skill_dirs:
        leaf = d.name
        if not LEAF_RE.match(leaf):
            print(f"  skipped  {leaf}  (name fails {LEAF_RE.pattern})")
            continue
        bundle = read_bundle(d)
        try:
            action = publish(leaf, bundle)
            print(f"  {action:8} {leaf}  ({len(bundle)} file(s))")
        except Exception as e:  # keep one bad bundle from blocking the rest
            errors.append(leaf)
            print(f"  error    {leaf}: {str(e)[:140]}")

    published = [d.name for d in skill_dirs if d.name not in errors and LEAF_RE.match(d.name)]
    if published:
        grant_agent_read(published)

    print(f"\nDone. UC Skills under {SCHEMA_FQN} (see the schema's Skills tab).")
    if errors:
        raise SystemExit(f"{len(errors)} skill(s) failed: {', '.join(errors)}")


if __name__ == "__main__":
    main()
