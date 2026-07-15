# Contributing to Datus Plugins

Thanks for contributing! This repo is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
of independently published Datus plugins. This guide covers the development
setup, the plugin contract, and how to add, test, and release a plugin.

New to writing a plugin? The fastest path is the bundled
**`datus-plugin-development`** skill — install it in Claude Code or Codex (see
the [README](README.md#develop-your-own-plugin)) and it scaffolds a
contract-compliant plugin from your SDK / API / docs, design-draft first.

## Development environment

One lockfile and one `.venv` at the repo root cover every member:

```bash
uv sync --all-extras          # set up everything (all plugins + dev deps)
```

Run tests **per package** — the value passed to `--package` is the distribution
name, the path is its directory:

```bash
uv run --package datus-airflow-plugin pytest datus-airflow-plugin
uv run --package datus-s3-plugin      pytest datus-aws-plugins/datus-s3-plugin
```

> ⚠️ **Do not run `pytest` from the repo root.** Every plugin has an identically
> named `tests/test_plugin_contract.py`; collecting them together triggers a
> pytest module-name collision error. Always scope to one package.

## Repository layout

Two shapes, picked up automatically by the workspace member globs in the root
`pyproject.toml`:

- **Standalone plugins** (`datus-airflow-plugin`, `datus-statsig-plugin`) sit at
  the repo root as `datus-*-plugin`.
- **AWS plugins** live under [`datus-aws-plugins/`](datus-aws-plugins/) — one
  `datus-<service>-plugin/` distribution each, all depending on the shared
  [`datus-aws-common`](datus-aws-plugins/datus-aws-common/) library (boto3
  session/AssumeRole, config, error mapping, output rendering, CLI helpers).
  `datus-aws-common` is a library, not a plugin: it registers no entry point.

A standalone plugin follows the **naming triple** — directory, distribution, and
import package all agree:

```
datus-<name>-plugin/            # directory & PyPI distribution name
├── pyproject.toml
├── README.md
├── datus_<name>_plugin/        # import package
│   ├── datus-plugin.yml        # the declarative manifest (the contract)
│   ├── prompts/                # system-prompt Jinja2 template (system.md.j2)
│   ├── cli/                    # one module per command group, each exposing register(sub)
│   └── skills/                 # bundled agent skills (SKILL.md per skill)
└── tests/
    └── test_plugin_contract.py # manifest conformance tests
```

## The plugin contract

Three non-negotiable rules:

- **No `datus` import**, and no dependency on `datus` or a shared plugin SDK in
  `pyproject.toml`. The whole contract is the declarative `datus-plugin.yml`
  manifest; plugins run inside Datus' own interpreter (Python ≥ 3.12).
- **No cross-plugin imports.** Plugins never import each other. Extract shared
  code into a dedicated **library** distribution (its own `pyproject.toml`, no
  entry point) — like `datus-aws-common` — only once several plugins need it.
  Until then, prefer copying small helpers.
- **One distribution, one plugin.** Each `pyproject.toml` declares **exactly
  one** `datus.plugins` entry point.

### Entry point

The entry-point *name* determines everything: the CLI command (`datus <name>`)
and the config key (`agent.plugins.<name>`). Its *value* is the **package name**
— a pure name→package mapping, no `:Class` reference:

```toml
[project.entry-points."datus.plugins"]
s3 = "datus_s3_plugin"
```

Reserved names — never usable: `upgrade`, `skill`, `plugin`. Names starting with
`-` cannot be dispatched.

### The `datus-plugin.yml` manifest

Lives at the import-package root. Only `manifest_version` is required; a code ref
is a dotted `module.path:function` string relative to the package.

| Key | Type | Purpose |
|---|---|---|
| `manifest_version` | int, **required** | Must be `1`. |
| `description` | string | One-line summary shown by `datus plugin info`. |
| `cli` | code ref | `module.path:function` called as `main(argv, profile)` on `datus <name> ...`. Without it, `datus <name>` exits 2. |
| `tool_transformers` | mapping | Tool pattern → code ref(s) that rewrite or deny the agent's tool calls. |
| `permissions` | mapping | Bash-permission rules for the plugin's CLI namespace, per permission profile — pure YAML, no code. |
| `system_prompt` | path | Package-relative Jinja2 template rendered into the agent's system prompt. |
| `skills` | path | Package-relative bundled-skill directory. |
| `config_schema` | JSON Schema | Inline object schema for one profile — drives the `/plugins` TUI form and validates profiles before saving. |

The `cli` function signature is `main(argv: list[str], profile: dict) -> int | None`
and must not call `sys.exit()` on the success path. Follow the exit-code
convention: `0` success · `1` runtime/API error · `2` usage · `3` config error ·
`8` missing optional dependency.

## Adding a new plugin

1. Copy the reference implementation (`datus-airflow-plugin` for a standalone
   plugin; an existing `datus-aws-plugins/datus-*-plugin` for an AWS one), or run
   the `datus-plugin-development` skill to scaffold it.
2. Pick `<name>` and rename the directory / import package / entry point to match
   the naming triple. A standalone plugin is picked up by the `datus-*-plugin`
   glob; an AWS plugin goes under `datus-aws-plugins/` and depends on
   `datus-aws-common` (`{ workspace = true }` under `[tool.uv.sources]`).
3. Write the `datus-plugin.yml` manifest, the CLI modules, the system-prompt
   template, and any bundled skills.
4. Copy `tests/test_plugin_contract.py` — it pins the contract down.
5. `uv sync --all-extras`, then run the plugin's tests (see above).

## Testing

- **Contract test.** Every plugin ships `tests/test_plugin_contract.py`, which
  validates the manifest, the entry point, and packaging. Copy it into new
  plugins and keep it green.
- **Integration tests are deterministic** — no real LLM or live-service calls.
- Test-only deps (`pytest`, `jinja2`, `jsonschema`) live in the `dev` optional
  dependencies of each `pyproject.toml`.

### Pre-submit checklist

- [ ] The package does **not** `import datus` (`grep -rn "import datus" your_pkg/`).
- [ ] No dependency on `datus` or a shared plugin SDK in `pyproject.toml`.
- [ ] **Exactly one** `datus.plugins` entry point; its value is the package name
      (no `:Class`), its name isn't reserved and doesn't start with `-`.
- [ ] `datus-plugin.yml` at the package root, `manifest_version: 1`, and shipped
      in the wheel (`unzip -l dist/*.whl`).
- [ ] `cli` is `main(argv, profile) -> int | None` and never `sys.exit()`s on
      success.
- [ ] Secret config fields marked `x-secret: true` in `config_schema`; every
      non-secret field the prompt references is declared in the schema.
- [ ] The system-prompt template handles `profiles == {}` via `{% if profiles %}`
      and points at `<name>-setup` in the else branch.
- [ ] `permissions` patterns are namespace-relative (no `datus <name>` prefix),
      and state-changing subcommands are `ask` under the `normal` profile.
- [ ] Skill files and the prompt template are packaged into the wheel.
- [ ] The entry-point name matches the intended `datus <name>` command and the
      `agent.plugins.<name>` config key.

## Local install & packaging

```bash
datus plugin install src:./datus-<name>-plugin   # installs into ~/.datus/plugins/<name>/
datus plugin pack -o ./dist                       # distributable wheelhouse .zip (--with-deps for offline)
datus plugin export <name>                        # re-materialize a .zip from an installed plugin
```

`datus plugin install` also accepts `pip:<requirement>`, `whl:<wheel>`,
`git:<url>`, and `zip:<bundle>` sources. For a tight edit-run loop,
`pip install -e datus-<name>-plugin` into Datus' environment also works — such
plugins are discovered as a fallback.

## Pull requests

- Branch from `main`; `main` is protected and merges require review.
- Keep commits focused; use an imperative subject (an optional `[Category]`
  prefix — `[Doc]`, `[Refactor]`, … — is used in this repo's history).
- Scope each PR to one plugin or one concern where practical, and keep the
  plugin's tests green.

## Releases

Distributions are tagged and released independently as
`<distribution>/v<version>` — e.g. `datus-airflow-plugin/v0.1.0`,
`datus-s3-plugin/v0.1.0`. Bumping `datus-aws-common` may require dependent
plugins to widen their version constraint.

### Versioning & maturity

Every distribution follows [SemVer](https://semver.org) and is versioned
independently:

- **🧪 Experimental (`0.1.x`)** — the default. Functional and contract-tested,
  but not yet production-validated; the command surface, profile schema, and
  permission posture may still change below `1.0`.
- **✅ Stable (`1.0.0`+)** — promoted after real-world usage. From `1.0.0` on,
  the CLI and profile schema carry SemVer compatibility guarantees.

Graduate a distribution by bumping its version to `1.0.0` in its
`pyproject.toml`. Each plugin (and `datus-aws-common`) is promoted independently.

## License

By contributing, you agree that your contributions are licensed under the
[Apache-2.0](LICENSE) license.
