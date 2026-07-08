"""Request-body templates for the write commands.

One source of truth used two ways: as the ``--help`` epilog of a write
subcommand, and as the output of the read-only ``datus statsig describe <group>
<subcommand>`` command (which the agent can run without a confirmation prompt to
learn the body shape before composing a mutating call).

Keys are ``"<group> <subcommand>"`` — the same paths the permission layer uses.
Templates are illustrative JSON with ``//`` comments (``*`` marks required
fields); they are documentation, not validated schemas.
"""

from __future__ import annotations

from typing import Dict, List

BODY_TEMPLATES: Dict[str, str] = {
    "metrics create": """\
{
  "name":            "string*      // metric name",
  "type":            "string*      // e.g. user_warehouse | count | mean | ratio | funnel",
  "description":     "string",
  "tags":            ["string"],
  "directionality":  "increase | decrease",
  "isPermanent":     false,
  "unitTypes":       ["userID"],
  "warehouseNative": { "..." : "warehouse-native config (aggregation, metric_source, columns)" },
  "dryRun":          false        // set via --dry-run: validate without persisting
}""",
    "metrics update": """\
{
  // all fields optional — send only what changes
  "name":            "string",
  "description":     "string",
  "tags":            ["string"],
  "isVerified":      true,
  "directionality":  "increase | decrease",
  "warehouseNative": { "...": "..." },
  "dryRun":          false        // set via --dry-run
}""",
    "metric-source create": """\
{
  "name":            "string*      // metric source name",
  "sql":             "string*      // the SELECT that defines this source",
  "timestampColumn": "string*      // timestamp column in the SQL result",
  "idTypeMapping":   { "userID": "user_id_column" },  // * at least one unit-id -> column",
  "description":     "string",
  "tags":            ["string"],
  "timestampAsDay":  false,
  "customFieldMapping": { "col": "field" },
  "dryRun":          false        // set via --dry-run
}""",
    "metric-source update": """\
{
  // partial update — send only what changes (sql/idTypeMapping usually required together)
  "sql":             "string",
  "timestampColumn": "string",
  "idTypeMapping":   { "userID": "user_id_column" },
  "description":     "string",
  "tags":            ["string"],
  "dryRun":          false        // set via --dry-run
}""",
    "warehouse-connections update": """\
{
  // supply exactly ONE warehouse block; values are credentials — keep them in a
  // file passed via --json-file, never on the command line
  "snowflake":  { "account": "...", "user": "...", "password": "...", "role": "...", "warehouse": "...", "database": "..." },
  "databricks": { "host": "...", "path": "...", "token": "..." },
  "bigquery":   { "...": "service-account credentials" },
  "redshift":   { "...": "..." },
  "athena":     { "...": "..." }
}""",
}

# Top-level keys that must be present for create-style commands (validated before
# the request goes out, so the model gets a clear exit-2 message it can correct).
REQUIRED_FIELDS: Dict[str, List[str]] = {
    "metrics create": ["name", "type"],
    "metric-source create": ["name", "sql", "timestampColumn", "idTypeMapping"],
}

# Commands whose body is one-of these keys (at least one required).
ONE_OF_FIELDS: Dict[str, List[str]] = {
    "warehouse-connections update": ["snowflake", "databricks", "bigquery", "redshift", "athena"],
}


def describe_text(key: str) -> str:
    """Return the annotated body template for ``"<group> <subcommand>"``."""
    template = BODY_TEMPLATES.get(key)
    if template is None:
        raise KeyError(key)
    return (
        f"# datus statsig {key} — request body\n"
        f"# pass with --json '<inline>' or --json-file <path>\n\n"
        f"{template}\n"
    )


def epilog_for(key: str) -> str:
    """The ``--help`` epilog for a write subcommand: its body template."""
    template = BODY_TEMPLATES.get(key)
    if template is None:
        return ""
    return (
        f"request body (--json / --json-file), also via "
        f"`datus statsig describe {key}`:\n\n{template}"
    )
