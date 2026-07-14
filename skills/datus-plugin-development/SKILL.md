---
name: datus-plugin-development
description: Wrap a user-provided SDK / REST API / documentation into an installable Datus plugin. Produces a design draft FIRST (config parameters, CLI command list with doc citations + permission rules, bundled-skill plan), then STOPS for user confirmation before writing any code. Fully self-contained — the entire plugin contract is inlined below.
triggers:
  - datus plugin
  - build a datus plugin
  - wrap sdk into plugin
  - wrap api into plugin
  - create datus plugin
  - plugin development
argument-hint: "[path or URL to the SDK / API / docs to wrap, plus the desired command name]"
---

# Datus Plugin Development Skill

## Goal

Turn documentation the user hands you (an SDK reference, a REST/OpenAPI spec, a
CLI man page, a README, a set of function signatures, …) into an installable
**Datus plugin** — a Python package discovered through the `datus.plugins`
entry-point group that adds a `datus <name>` command plus optional bundled
skills, prompt awareness, bash permissions, and tool transformers.

**This skill is design-first.** It NEVER writes plugin code on the first pass.
It reads the provided documentation, produces a **design draft** the user can
review, and then **stops and waits for explicit confirmation**. Only after the
user approves (or amends) the draft does it implement the package.

This skill is fully self-contained: the complete plugin contract is inlined in
the **Contract Reference** appendix below. Do not depend on external docs or on
reading Datus source code — everything needed to design and build a plugin is
here.

## Non-negotiable ground rules

Every design and every line of generated code must honor these (details in the
appendix):

- **A plugin never imports `datus.*`** and never depends on `datus` (or any
  shared SDK) in `pyproject.toml`. Datus is the config broker; the plugin only
  implements method names Datus calls by duck typing.
- **One distribution ships exactly one plugin.** Each `pyproject.toml` declares a
  single `datus.plugins` entry point — never bundle several plugins into one
  distribution. Code shared across plugins goes into a separate **library**
  distribution (it registers no entry point) that the plugins depend on, so every
  plugin is versioned, released, and installed on its own.
- **Constructor takes `profile` as a keyword argument** named exactly
  `profile` — `PluginClass(profile=...)`. Datus passes the resolved,
  `${VAR}`-expanded `agent.plugins.<name>.<profile>` dict.
- **`run_cli(self, argv) -> int | None`** runs the subcommand. `argv` is
  everything after `datus <name>` with Datus' own `--profile` / `--config`
  already stripped. Return an exit code; `None` means `0`; do not call
  `sys.exit()` on the success path.
- **Class-reachable hooks.** `skills_dir()`, `system_prompt(profiles)`,
  `cli_permissions()`, and `tool_transformers()` are optional but, when
  present, MUST be `@classmethod` / `@staticmethod` / class attribute —
  Datus resolves them at startup **without a profile instance**.
- **Reserved names.** The entry-point name may not be `upgrade` or `skill`, and
  may not start with `-`. It doubles as both the `datus <name>` command and the
  `agent.plugins.<name>` config key, so pick one clean identifier.
- **Never surface secrets.** `system_prompt` emits only non-secret fields
  (endpoints, region, environment names) via an explicit allow-list. Secrets in
  config are always `${ENV_VAR}` references, never literals.

If the requested command name is reserved / collides / starts with `-`, flag it
in the draft and propose an alternative.

## Input

- `$ARGUMENTS` — path or URL to the source documentation, plus (ideally) the
  desired command name. If the docs location or the command name is missing,
  ask for it before drafting.
- The user may instead paste the docs directly into the conversation.

---

## Phase A — DESIGN DRAFT (always first; then STOP)

### Step A1: Ingest the source documentation

Read the user-provided docs. Extract:

- **Capabilities** — the concrete operations the plugin could expose (API
  endpoints, SDK methods, CLI verbs). For each, note inputs, outputs, and
  whether it is read-only or state-changing. **Inventory them ALL first** — when
  the source exposes a large surface (an OpenAPI spec, dozens of resource
  groups), enumerate every group/endpoint before deciding anything; never
  silently skip parts of the API.
- **Authentication & connection** — base URL / host, auth scheme (bearer token,
  API key, basic, OAuth), region, workspace/tenant, timeouts. These become
  profile fields.
- **Dependencies** — which third-party libraries the wrapper needs (`requests`,
  `httpx`, the vendor SDK, `typer`, …). Never `datus`.

**Relevance filter — Datus is a data-analysis / SQL / warehouse / ETL / metrics
agent.** These plugins exist to serve that agent, not to mirror a vendor's whole
API. After inventorying every capability, tier each by how well it serves
data-analysis workflows and pick a **preliminary scope** that leads with the
high-relevance operations. The discriminating axis is **analysis-relevant vs.
management/config**, NOT read-vs-write:

- Keep analysis-relevant **writes** — authoring metric/query SQL, triggering an
  ingestion/ETL run, managing a warehouse connection — gated as `ask`. Do not
  let a "read-only is safe" bias quietly shrink the scope; include the
  authoring/ETL writes the agent genuinely needs.
- Drop analysis-irrelevant capabilities — feature-flag toggles, role/permission
  admin, project settings — even when they offer tidy CRUD.

You never finalize the scope alone. The draft must present, side by side, the
**selection rationale** for what you kept AND the **full list of excluded
capabilities** with reasons, so the user makes the final call (Step A2 §5).

If the docs are ambiguous or incomplete, collect the open questions — do not
guess silently.

### Step A2: Produce the draft

Render the draft in the conversation using this exact structure. Keep it
concrete — every CLI command must trace back to a specific part of the source
docs.

```
## Datus Plugin Design Draft: <name>

- CLI command / config key: `datus <name>`  ·  `agent.plugins.<name>`
- Purpose: <one line>
- Source docs: <what was provided>
- Scope: <K of N inventoried capabilities selected — rationale + exclusions in §5>
- run_cli routing style: <dict dispatch | argparse | REST wrapper | Typer>
- Third-party dependencies: <libs, none = pure stdlib>  (never datus)

### 1. Configuration parameters (profile schema)

| Field | Type | Required | Secret | Default | Source doc ref | Notes |
|-------|------|----------|--------|---------|----------------|-------|
| api_base_url | str | yes | no | — | <spec §> | endpoint root |
| token | str | yes | YES -> `${VAR}` | — | <auth §> | bearer token |
| ...   |      |          |        |         |                |       |

- Secret fields are configured as `${ENV_VAR}` references, expanded by Datus.
- A `name` key is injected by Datus (= profile name); do not declare it.

### 2. CLI command list

For each subcommand:

#### `datus <name> <subcommand> [args...]`
- Function: <what it does, in one or two lines>
- Analysis value: <the data-analysis / SQL / warehouse / ETL / metrics workflow
  this serves — if you cannot state one, it probably belongs in §5 exclusions>
- Doc ref: <SDK method / endpoint / section it maps to>
- Permission:
  - normal: allow | ask | deny  — <pattern, e.g. `list-pets:*`>
  - auto:   allow | ask | deny  — <pattern>
  - rationale: <read-only => allow; state-changing => ask under normal>

(Repeat per subcommand. Group read-only vs. state-changing so the permission
posture is obvious.)

Permission patterns are namespace-relative — Datus prefixes `datus <name> `
automatically. Syntax: `cmd` exact, `cmd:*` prefix, `cmd:glob` prefix + first-arg
glob, `:*` whole namespace. Only `normal` and `auto` profiles are accepted.

### 3. Bundled skills

- Main skill `<name>` — description + when the agent should invoke it.
- Setup skill `<name>-setup` — the profile fields it collects from the user,
  and that it writes `${VAR}` references for secrets (never literals).
- system_prompt injection — the NON-SECRET fields to surface per environment
  (e.g. base URL, region, env name). Explicit allow-list; secrets excluded.
  Plan the installed-but-unconfigured message pointing at `<name>-setup`.

### 4. Tool transformers (only if applicable)

- If the plugin should rewrite/deny the agent's tool calls (e.g. SQL scoping),
  list the tool patterns and the transform/deny rule. Otherwise: "none".

### 5. Scope selection & excluded capabilities

For any non-trivial API, the reader must be able to audit — and overturn — your
scoping. Show both halves:

- **Selection rationale** — why the §2 commands were kept: which data-analysis /
  SQL / warehouse / ETL / metrics workflows they serve. Tie back to the
  relevance filter (Step A1).
- **Excluded / deferred capabilities** — EVERY capability inventoried in Step A1
  but left out. Group by resource, one-line reason each (analysis-irrelevant /
  redundant / low priority / needs clarification). Never silently drop parts of
  a large API — the user decides the final scope from this list, and may pull
  any excluded item back in.

| Capability / resource group | In scope? | Reason |
|-----------------------------|-----------|--------|
| metrics — read + warehouse-native SQL authoring | yes | core metric analysis |
| ingestions — trigger / backfill | yes | ETL the agent drives (`ask`) |
| experiments — read results / pulse | yes | analysis output |
| feature gates / segments / holdouts | no — deferred | feature-flag mgmt, not data analysis |
| roles / users / project settings | no — deferred | access & project admin, not analysis |
| ... (list every remaining group) | | |

End with an explicit prompt: "Confirm this scope, or tell me which excluded
capabilities to include."

### 6. Open questions

- <anything the docs did not resolve: auth details, pagination, rate limits,
  command-name conflicts, or scope calls you are unsure about …>
```

### Step A3: STOP and wait for confirmation

After presenting the draft:

- Explicitly ask the user to **confirm, amend, or reject** the draft, to
  **confirm the scope or pull any excluded capability (§5) back in**, and to
  answer any open questions.
- **Do not create any files, do not write any code, do not scaffold the
  package.** End the turn here.
- Proceed to Phase B **only** after the user gives explicit approval. If the
  user requests changes, revise the draft and stop again.

---

## Phase B — IMPLEMENTATION (only after explicit confirmation)

Use the recipes and semantics from the **Contract Reference** appendix.
Implement in this order:

1. **Package layout** — `datus-plugin-<name>/` with `pyproject.toml` and a
   `datus_plugin_<name>/` package (`__init__.py`, `plugin.py`). See
   [Package layout & entry point](#package-layout--entry-point).
2. **Entry point** — register under `[project.entry-points."datus.plugins"]`
   as `<name> = "datus_plugin_<name>.plugin:<Class>"`. Dependencies list the
   plugin's own libs, **never `datus`**.
3. **Plugin class** — `__init__(self, profile=None)`, `run_cli(self, argv)`
   using the routing style from the draft (see
   [run_cli recipes](#run_cli-recipes)). Read endpoint/credentials from
   `self.profile`; map subcommands to operations; return conventional exit
   codes (`0` ok, `1` runtime, `2` usage, `3` config, `8` missing optional dep).
4. **Class-level hooks** (as designed):
   - `skills_dir()` → the bundled `skills/` directory
     ([Bundling skills](#bundling-skills)).
   - `system_prompt(profiles)` → non-secret allow-listed fields only; an
     "installed, not configured" note for `{}` pointing at `<name>-setup`
     ([System-prompt injection](#system-prompt-injection)).
   - `cli_permissions()` → the per-profile `allow`/`ask`/`deny` rules,
     namespace-relative ([CLI bash permissions](#cli-bash-permissions)).
   - `tool_transformers()` → only if the draft included them; fail closed
     ([Tool argument transformers](#tool-argument-transformers)).
5. **Bundled skills** — `skills/<name>/SKILL.md` and
   `skills/<name>-setup/SKILL.md` (see [Bundling a setup skill](#bundling-a-setup-skill)).
6. **Packaging** — ensure skill files ship in the wheel (Hatchling packages them
   by default; with setuptools add `[tool.setuptools.package-data]`).
7. **Tests** — construct the plugin with a plain dict (no `agent.yml`, no Datus
   imports). See [Testing your plugin](#testing-your-plugin).

### Verify against the constraints checklist

Before declaring done, confirm every item in
[Constraints checklist](#constraints-checklist).

### End-to-end verification

- **CLI dispatch**: `pip install -e datus-plugin-<name>` then `datus <name> ...`.
  If it falls through to the REPL, the entry point is missing or misnamed.
- **Skills**: start `datus`, run `/skill list` — bundled skills appear.
- **Prompt injection**: unit-test `system_prompt()` directly; optionally start a
  session and ask the agent "which plugins are configured?".

## Report

Summarize: the package created, entry-point name, config schema, the CLI
commands and their permission posture, bundled skills, and the checklist +
verification results. Note any open questions still outstanding.

---

# Contract Reference (inlined — the single source of truth for this skill)

A plugin is an installable Python package discovered through the
`datus.plugins` entry-point group. The defining constraint: **a plugin never
imports `datus.*` and depends on no shared SDK**. The contract is a small set
of method names that Datus calls by structure (duck typing). Datus is the
*config broker* — it reads `agent.yml`, expands `${VAR}` references, resolves
the active profile, constructs the plugin with a plain `dict`, and calls it.

## The contract members

Datus calls these members **by name** on the class the entry point resolves to.
The plugin class does not import or subclass anything from Datus.

| Member | Kind | Purpose |
|---|---|---|
| `PluginClass(profile: dict)` | constructor | Datus passes the resolved `agent.plugins.<name>.<profile>` dict (env-expanded) as a **keyword argument** — `PluginClass(profile=...)` — so the parameter must be named `profile`. A config-free plugin may ignore its value. |
| `run_cli(self, argv: list[str]) -> int \| None` | instance method | Runs the subcommand. `argv` is everything after `datus <plugin>`, with Datus' own `--profile` / `--config` already stripped. Return an exit code; `None` means `0`. |
| `skills_dir() -> str \| None` | **optional**, class-level | Returns the bundled skill directory. |
| `system_prompt(profiles: dict[str, dict]) -> str \| None` | **optional**, class-level | Returns a markdown block injected into the agent's system prompt. |
| `cli_permissions() -> dict \| None` | **optional**, class-level | Declares bash-permission rules for the plugin's own CLI namespace, per permission profile. |
| `tool_transformers() -> dict \| None` | **optional**, class-level | Declares tool argument transformers that rewrite or deny the agent's tool calls before execution. |

**`skills_dir`, `system_prompt`, `cli_permissions`, and `tool_transformers`
must be class-reachable.** Datus resolves them **at startup, without an active
profile** (skill discovery and prompt building happen before any command runs).
Declare them as `@classmethod` / `@staticmethod` (or a plain class attribute for
`skills_dir`) — they must not depend on `__init__`.

## Package layout & entry point

```
datus-plugin-hello/
├── pyproject.toml
└── datus_plugin_hello/
    ├── __init__.py
    └── plugin.py
```

Minimal plugin class (`datus_plugin_hello/plugin.py`):

```python
from __future__ import annotations

from typing import Any, Dict, List, Optional


class HelloPlugin:
    def __init__(self, profile: Optional[Dict[str, Any]] = None) -> None:
        # `profile` is the resolved agent.plugins.hello.<profile> dict
        # (already ${VAR}-expanded by datus). Empty dict is fine.
        self.profile: Dict[str, Any] = profile or {}

    def run_cli(self, argv: List[str]) -> int:
        greeting = self.profile.get("greeting", "Hello")
        name = argv[0] if argv else "world"
        print(f"{greeting}, {name}!")
        return 0
```

Register the entry point (`pyproject.toml`):

```toml
[project]
name = "datus-plugin-hello"
version = "0.1.0"
dependencies = []                      # note: NOT datus

[project.entry-points."datus.plugins"]
hello = "datus_plugin_hello.plugin:HelloPlugin"
```

The entry-point name (`hello`) alone determines the CLI command
(`datus hello`) and the config key (`agent.plugins.hello`) — the class and
module names are free. Two names are **reserved** and never dispatched to
plugins: `upgrade` and `skill`. A plugin registered under either is silently
unreachable, and names starting with `-` cannot be dispatched at all.

Declare **exactly one** `datus.plugins` entry point per `pyproject.toml` — one
distribution, one plugin. Datus can technically load several entry points from a
single wheel, but bundling them couples their versions and releases; keep each
plugin as its own distribution. When several plugins share plumbing, extract it
into a separate **library** distribution (its own `pyproject.toml`, no entry
point) that each plugin lists in `dependencies`, rather than adding a second
entry point here.

Install and run:

```bash
pip install -e datus-plugin-hello
datus hello Ada          # -> Hello, Ada!
```

## Configuration & profile resolution

Users configure the plugin under `agent.plugins.<name>`, where each key below
`<name>` is a **profile** (an environment):

```yaml
agent:
  plugins:
    hello:
      prod:
        default: true
        greeting: Hi
        token: ${HELLO_TOKEN}      # prefer ${ENV_VAR} for secrets
      staging:
        greeting: Yo
```

Datus parses this into `agent.plugins.<name>.<profile> -> dict`, **expands
`${VAR}` per profile**, and injects a `name` key equal to the profile name.

The active profile is resolved in this order:

1. Explicit `--profile <p>` on the command line.
2. Project pin in `./.datus/config.yml` (`plugins: {<name>: <profile>}`).
3. The profile flagged `default: true` (more than one is an error).
4. The sole profile, if only one is configured.
5. No `agent.plugins.<name>` section at all → the plugin runs with an empty
   `{}` configuration (config-free plugins still work).
6. Multiple profiles with no way to disambiguate → Datus errors, asking for
   `--profile`.

Datus resolves the config file in this order: explicit `--config` →
`./conf/agent.yml` (project) → `~/.datus/conf/agent.yml` (user default). Config
edits take effect on the next `datus <plugin>` invocation; no restart needed.
`agent.plugins_enabled: false` is a master switch turning off all plugin
functionality (dispatch, skills, prompt injection, permissions, transformers).

## `run_cli`: argv and exit codes

`argv` is the command tail with Datus' global flags removed:

```
datus hello --profile staging greet Ada
                └── stripped ──┘ └── argv = ["greet", "Ada"] ──┘
```

Only `--profile` / `--config` appearing **before the first non-option token**
are consumed as Datus globals; from the first command token onward everything
belongs to the plugin. `datus hello greet --profile staging` therefore passes
`["greet", "--profile", "staging"]` through untouched.

Return an integer exit code. Conventions:

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | runtime error |
| `2` | usage error |
| `3` | config error |
| `8` | missing optional dependency |

Raising is also fine — Datus catches exceptions from `run_cli` and maps them to
exit code `1` — but returning an explicit code gives users clearer signals. Do
not call `sys.exit()` on the success path.

## `run_cli` recipes

### A. Dict dispatch — a few functions, zero dependencies

```python
class ToolboxPlugin:
    def __init__(self, profile=None):
        self.profile = profile or {}

    def run_cli(self, argv):
        if not argv:
            print("usage: datus toolbox <add|upper> ...")
            return 2
        cmd, rest = argv[0], argv[1:]
        handlers = {"add": self._add, "upper": self._upper}
        handler = handlers.get(cmd)
        if handler is None:
            print(f"unknown command: {cmd}")
            return 2
        return handler(rest)

    def _add(self, args):          # datus toolbox add 1 2 3
        print(sum(float(a) for a in args))
        return 0

    def _upper(self, args):        # datus toolbox upper hello
        print(" ".join(args).upper())
        return 0
```

### B. argparse — typed args, flags, auto usage/`-h`

`argparse` prints usage and raises `SystemExit` on `-h` or a bad invocation;
Datus surfaces that as the exit code (0 for `-h`, 2 for usage errors).

```python
import argparse

class ToolboxPlugin:
    def __init__(self, profile=None):
        self.profile = profile or {}

    def run_cli(self, argv):
        parser = argparse.ArgumentParser(prog="datus toolbox")
        sub = parser.add_subparsers(dest="cmd", required=True)

        p_add = sub.add_parser("add", help="sum numbers")
        p_add.add_argument("nums", nargs="+", type=float)

        p_grep = sub.add_parser("grep", help="filter lines in a file")
        p_grep.add_argument("pattern")
        p_grep.add_argument("path")
        p_grep.add_argument("-i", "--ignore-case", action="store_true")

        ns = parser.parse_args(argv)      # SystemExit on -h / bad usage
        if ns.cmd == "add":
            print(sum(ns.nums))
            return 0
        if ns.cmd == "grep":
            return self._grep(ns.pattern, ns.path, ns.ignore_case)

    def _grep(self, pattern, path, ignore_case):
        needle = pattern.lower() if ignore_case else pattern
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                hay = line.lower() if ignore_case else line
                if needle in hay:
                    print(line.rstrip())
        return 0
```

### C. Wrapping a REST API

Read endpoint and credentials from the profile (Datus already expanded
`${VAR}`), then map subcommands to requests. Keep credentials in the profile —
never hard-code them, and never echo them.

```python
import argparse
import json

class PetstorePlugin:
    def __init__(self, profile=None):
        self.profile = profile or {}

    def run_cli(self, argv):
        import requests  # a plugin may depend on its own libraries

        base = self.profile.get("api_base_url")
        if not base:
            print("no api_base_url configured for the profile")
            return 3
        headers = {}
        if self.profile.get("token"):
            headers["Authorization"] = f"Bearer {self.profile['token']}"

        parser = argparse.ArgumentParser(prog="datus petstore")
        sub = parser.add_subparsers(dest="cmd", required=True)
        sub.add_parser("list-pets")
        p_get = sub.add_parser("get-pet")
        p_get.add_argument("id")
        ns = parser.parse_args(argv)

        base = base.rstrip("/")
        if ns.cmd == "list-pets":
            resp = requests.get(f"{base}/pets", headers=headers, timeout=30)
        else:
            resp = requests.get(f"{base}/pets/{ns.id}", headers=headers, timeout=30)

        if resp.status_code >= 400:
            print(f"error {resp.status_code}: {resp.text}")
            return 1
        print(json.dumps(resp.json(), indent=2))
        return 0
```

Corresponding config:

```yaml
agent:
  plugins:
    petstore:
      prod:
        default: true
        api_base_url: https://api.example.com/v1
        token: ${PETSTORE_TOKEN}
```

### D. Typer / Click — richest UX, one extra dependency

Because Datus constructs the plugin per-invocation but the Typer app is a
module-level object, expose the active profile through a module global.

```python
import typer

app = typer.Typer(add_completion=False)
_ACTIVE_PROFILE: dict = {}


@app.command("greet")
def greet(name: str, loud: bool = False):
    greeting = _ACTIVE_PROFILE.get("greeting", "Hello")
    msg = f"{greeting}, {name}!"
    print(msg.upper() if loud else msg)


class GreeterPlugin:
    def __init__(self, profile=None):
        self.profile = profile or {}

    def run_cli(self, argv):
        global _ACTIVE_PROFILE
        _ACTIVE_PROFILE = self.profile
        try:
            # standalone_mode=False stops Click from calling sys.exit itself,
            # so we can return a code and always clear the profile.
            app(args=argv, standalone_mode=False)
            return 0
        except SystemExit as exc:      # -h / usage
            return int(exc.code or 0)
        except typer.Exit as exc:
            return exc.exit_code
        finally:
            _ACTIVE_PROFILE = {}
```

Add `typer` to the package's own `dependencies` (a plugin's deps are its own —
just not `datus`).

## Bundling skills

Expose a bundled skill directory via a class-level `skills_dir()`; Datus
discovers the skills at startup (they show up in `/skill list`).

```python
class HelloPlugin:
    @classmethod
    def skills_dir(cls) -> str:
        from pathlib import Path
        return str(Path(__file__).parent / "skills")
```

Layout and packaging:

```
datus_plugin_hello/
└── skills/
    └── hello/
        └── SKILL.md
```

A minimal `SKILL.md` is YAML frontmatter plus markdown instructions (the
frontmatter follows the agentskills.io spec used by the Skills system):

```markdown
---
name: hello
description: Say hello to someone via the `datus hello` CLI
---

# Hello

Run `datus hello <name>` to greet someone. ...
```

Make sure the skill files are included in the wheel (they are data, not Python).
Hatchling packages every file under the package directory by default. With
setuptools you must opt in:

```toml
[tool.setuptools.package-data]
datus_plugin_hello = ["skills/**/*"]
```

Verify with `unzip -l dist/*.whl | grep SKILL.md`.

## System-prompt injection

A plugin can tell the agent, up front, what it is and which environments are
configured. Expose a class-level `system_prompt(profiles)`:

```python
class HelloPlugin:
    @classmethod
    def system_prompt(cls, profiles):
        if not profiles:
            # Installed but unconfigured: point the agent at the setup skill
            # instead of disappearing from the prompt.
            return (
                "## Hello (installed, not configured)\n"
                "The `datus hello` CLI is installed but has no environment "
                "configured.\nRun the `hello-setup` skill to configure one."
            )
        envs = "\n".join(
            f"- {name}: {cfg.get('greeting', '?')}"
            for name, cfg in profiles.items()
        )
        return (
            "## Hello\n"
            "Say hello via `datus hello <name>`.\n"
            f"Environments ({len(profiles)}):\n{envs}"
        )
```

Datus passes the plugin's **full** profile mapping (all environments) and
appends the returned markdown to the system prompt of every agentic node. An
installed-but-unconfigured plugin receives `{}` — return a short "installed,
not configured" note pointing at the bundled setup skill. Return `None` only
when there is truly nothing to say. Datus prepends its own `## Plugins` preamble
naming the loaded config file, so the plugin text must not hard-code config
paths.

**Never surface secrets.** The returned text enters the LLM context. Datus
hands you the full profile dicts — which include `password`, secret keys,
access keys — but emit **only non-secret fields** (endpoints, region,
environment names) via an explicit allow-list.

## CLI bash permissions

When the **agent** (not a human) runs the CLI through its bash tool, the command
goes through Datus' permission layer. Without a declaration, every such command
prompts for confirmation. A class-level `cli_permissions()` declares, per
permission profile, which subcommands are safe to auto-run (`allow`), which must
be confirmed (`ask`), and which are blocked (`deny`):

```python
class HelloPlugin:
    @classmethod
    def cli_permissions(cls):
        return {
            "normal": {"allow": ["greet:*"], "ask": ["config set:*"]},
            "auto":   {"allow": ["greet:*", "config set:*"]},
        }
```

Semantics:

- **Patterns are relative to the namespace.** Datus prefixes each pattern with
  `datus <name> `, so `greet:*` becomes `datus hello greet:*`. A plugin can
  never affect commands outside `datus <name>`.
- **Pattern syntax**: `cmd` exact match, `cmd:*` prefix match, `cmd:glob`
  prefix match whose first argument must satisfy the glob (e.g. `greet:A*`). A
  bare `:*` covers the whole namespace.
- **Profile keys**: only `normal` and `auto` are accepted. The `dangerous`
  profile ignores all command-level bash rules by design; a `dangerous` key is
  warned about and dropped.
- **Users always win.** A user `deny` rule in `agent.yml` overrides a plugin
  `allow` (deny > ask > allow, regardless of declaration order); plugin
  declarations can never change a profile's default posture.
- **`ask` rules can be relaxed per project.** The confirmation prompt offers
  "allow (project)", which persists the exact matched pattern to the project's
  `.datus/config.yml` `bash_allow` list (exact match only; never widens; `deny`
  unaffected).
- **Scope**: only the agent's bash tool is gated. A human typing
  `datus hello ...` in a terminal is never affected.
- **`--profile` is transparent to matching**; `--config <path>` is not
  normalized and always falls back to confirmation.
- Malformed declarations (wrong types, unknown keys, empty patterns) are logged
  and skipped — never fatal.

Declare read-only subcommands as `allow` and state-changing ones as `ask` under
`normal`; promote routine state changes to `allow` under `auto` only when
re-running them is harmless.

## Tool argument transformers

A class-level `tool_transformers()` lets a plugin intercept the **agent's tool
calls** — inspect and rewrite the arguments before the tool executes, or deny
the call. The canonical use case is SQL policy enforcement.

```python
class ScopedSqlPlugin:
    @classmethod
    def tool_transformers(cls):
        return {"db_tools.execute_sql": enforce_tenant_scope}


def enforce_tenant_scope(tool_name, args, context):
    tenant_id = (context.get("principal") or {}).get("tenant", {}).get("id")
    if not tenant_id:
        raise PermissionError("missing principal.tenant.id; cannot scope query")
    args["sql"] = add_where_predicate(args["sql"], f"tenant_id = '{tenant_id}'")
    return args
```

Semantics:

- **Declaration shape**: a dict mapping tool patterns to a transformer or a list
  of transformers. Patterns use the proxy syntax — a bare tool name
  (`execute_sql`), or `category.method` with fnmatch globs (`db_tools.*`).
- **Transformer signature**: `transformer(tool_name, args, context) -> dict`,
  sync or async. Return the (possibly modified) argument dict to continue.
  **Raise to deny**: the tool never runs and the model receives the exception
  message as a normal tool failure. Returning anything that is not a dict also
  denies (fail closed).
- **`context`** is a plain dict with `node_name`, `principal` (request-scoped
  caller attributes, empty when the deployment sets none), `project_root`, and
  `agent_config` (read your own profile via
  `context["agent_config"].get_plugin_profile("<name>")`, duck-typed — never
  import `datus.*`). It is rebuilt on every call.
- **Coverage**: transformers wrap the agent's `FunctionTool` layer (both the SDK
  Runner and the native loop). They do **not** cover direct Python invocations
  of tool methods or tools proxied to an external client.
- **Trust model**: transformers run in-process with full access to matched tool
  call arguments — trusted code, gated by `plugins_enabled`.
- Use a SQL parser or a database-safe query builder when rewriting SQL — never
  string concatenation for policy predicates.
- Malformed declarations are logged and skipped. A declaration that collects but
  fails to apply aborts the agent node rather than silently running without
  enforcement.

## Bundling a setup skill

Editing YAML by hand is the main friction after `pip install`. Ship a
`<name>-setup` skill next to the main skill so the agent can collect the values
and write the profile itself:

```
datus_plugin_hello/
└── skills/
    ├── hello/
    │   └── SKILL.md
    └── hello-setup/
        └── SKILL.md
```

The setup `SKILL.md` should cover, in order:

1. **When to use** — the plugin is unconfigured, or the user wants another
   environment.
2. **Config structure** — a complete YAML template for
   `agent.plugins.<name>.<profile>`, with comments marking required / optional /
   secret fields.
3. **Ask the user** — list the fields that must come from the user (endpoint,
   auth choice, ...). For secrets, instruct the agent to have the user export an
   environment variable and reference it as `${VAR}` in the YAML — never write
   literal secrets.
4. **Write the config** — into the file named by the `## Plugins` prompt
   preamble, marking the first profile `default: true`.
5. **Verify** — a cheap read-only command (e.g. `datus hello version`).

Add a guard note: if the current environment cannot edit the config file (API /
VSCode / web deployment), the agent should tell the user to edit `agent.yml` on
the server instead.

A complete minimal `hello-setup/SKILL.md`:

````markdown
---
name: hello-setup
description: Configure an environment profile for the `datus hello` plugin
---

# Hello Setup

Use this skill when `datus hello` is installed but has no configured
environment, or when the user wants to add another one.

## Config structure

Profiles live under `agent.plugins.hello.<profile>` in the config file named
by the `## Plugins` section of the system prompt:

```yaml
agent:
  plugins:
    hello:
      prod:
        default: true            # mark the first profile as default
        greeting: Hi             # required
        token: ${HELLO_TOKEN}    # secret — reference an env var, never a literal
```

## Steps

1. Ask the user for `greeting` and which environment variable holds the token.
   Have the user export the variable; write `${VAR}` into the YAML — never a
   literal secret.
2. Write the profile into the config file above; mark the first profile
   `default: true`.
3. Verify with a cheap read-only call: `datus hello Ada`.

If this environment cannot edit the config file (API / web deployment), tell the
user to edit `agent.yml` on the server instead.
````

## Testing your plugin

Because Datus is the broker, unit tests construct the plugin with a plain dict —
no `agent.yml`, no Datus imports:

```python
from datus_plugin_hello.plugin import HelloPlugin

def test_run_cli_uses_profile_greeting(capsys):
    rc = HelloPlugin(profile={"name": "prod", "greeting": "Hi"}).run_cli(["Ada"])
    assert rc == 0
    assert "Hi, Ada!" in capsys.readouterr().out

def test_system_prompt_lists_envs_without_secrets():
    text = HelloPlugin.system_prompt({
        "prod": {"name": "prod", "greeting": "Hi", "token": "s3cr3t"},
    })
    assert "## Hello" in text
    assert "s3cr3t" not in text          # secrets must never leak

def test_system_prompt_unconfigured_points_to_setup_skill():
    text = HelloPlugin.system_prompt({})
    assert "not configured" in text
    assert "hello-setup" in text
```

## Constraints checklist

Before publishing, verify:

- [ ] The package does **not** `import datus` anywhere (`grep -rn "import datus" your_pkg/`).
- [ ] The package does **not** depend on `datus` or a shared plugin SDK in `pyproject.toml`.
- [ ] The `pyproject.toml` declares **exactly one** `datus.plugins` entry point (one distribution = one plugin); shared code lives in a separate library distribution.
- [ ] `__init__` accepts the profile as a keyword argument named `profile`.
- [ ] The entry-point name is not a reserved name (`upgrade`, `skill`) and does not start with `-`.
- [ ] `skills_dir`, `system_prompt`, `cli_permissions`, and `tool_transformers` are class-reachable.
- [ ] `system_prompt` emits only non-secret fields.
- [ ] `cli_permissions` patterns are namespace-relative (no `datus <name>` prefix), and state-changing subcommands are `ask` under `normal`.
- [ ] `run_cli` returns an int (or `None`) and does not call `sys.exit()` on the success path.
- [ ] Skill files are packaged into the wheel.
- [ ] The `datus.plugins` entry-point name matches the intended `datus <name>` command and the `agent.plugins.<name>` config key.
