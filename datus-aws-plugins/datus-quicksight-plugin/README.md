# datus-quicksight-plugin

A [Datus](https://datus.ai) plugin to **manage Amazon QuickSight end-to-end**
from `datus quicksight ...` — the full lifecycle of datasets, data sources,
dashboards, analyses, templates, themes and folders; users/groups/namespaces;
SPICE refresh (with `--wait`) and schedules; registered & anonymous embed URLs;
asset-bundle export/import; and account info. Backed by boto3.

```bash
pip install datus-aws-plugins
```

> Requires datus-agent >= 0.3.8 — the system-prompt template uses the `config_mutable` render-context variable (older versions skip the whole prompt section).

## Configuration

Profiles live under `agent.plugins.quicksight.<profile>` in Datus' `agent.yml`:

```yaml
agent:
  plugins:
    quicksight:
      prod:
        default: true
        region: us-east-1
        aws_account_id: "123456789012"   # required by every QuickSight API
        namespace: default               # optional
        identity_region: us-east-1        # optional; for users/groups/namespaces
        # credentials: standard AWS chain, or profile / keys / role_arn
```

## Commands

| Group | Subcommands |
|---|---|
| `datasets` | `list`, `describe`, `permissions`, `create`, `update`, `delete`, `set-permissions` |
| `datasources` | `list`, `describe`, `create`, `update`, `delete` |
| `dashboards` | `list`, `describe`, `versions`, `permissions`, `create`, `update`, `publish`, `delete`, `set-permissions`, `embed-url`, `embed-url-anonymous` |
| `analyses` | `list`, `describe`, `create`, `update`, `delete`, `restore` |
| `templates` | `list`, `describe`, `versions`, `create`, `update`, `delete` |
| `themes` | `list`, `describe`, `create`, `update`, `delete` |
| `folders` | `list`, `describe`, `members`, `create`, `delete`, `member-add`, `member-remove` |
| `users` | `list`, `describe`, `register`, `update`, `delete` |
| `groups` | `list`, `describe`, `members`, `create`, `delete`, `member-add`, `member-remove` |
| `namespaces` | `list`, `describe`, `create`, `delete` |
| `refresh` | `list`, `status`, `run [--wait]`, `cancel`, `schedules`, `schedule-put`, `schedule-delete` |
| `account` | `settings`, `subscription` |
| `assets` | `export`, `export-status`, `import`, `import-status` (asset bundles) |

```bash
datus quicksight datasets list
datus quicksight refresh run 1a2b3c4d --wait
datus quicksight dashboards publish dash-1 4
datus quicksight users list
datus quicksight assets export --cli-input '{"AssetBundleExportJobId":"e1","ResourceArns":["arn:..."],"ExportFormat":"QUICKSIGHT_JSON"}'
```

`create`/`update`/`set-permissions`/`schedule-put`/`assets` take `--cli-input`
= the API request body as JSON (without `AwsAccountId`). Reads run everywhere;
mutations always confirm; `refresh run/cancel` are routine.

## Exit codes

`0` success · `1` runtime/API error (also: failed ingestion under `--wait`) ·
`2` usage · `3` config error (also: missing `aws_account_id`).

## Development

```bash
uv run --package datus-quicksight-plugin pytest datus-quicksight-plugin
```

Never imports `datus`; registers the `quicksight` entry point in
`datus.plugins`. Shared boto3 plumbing lives in `datus-aws-common`. Bundled
skills: `quicksight` and `quicksight-setup`.

## Out of scope

VPC connections, IP/network rules, account customization & branding, custom
permissions, Q topics, and template aliases are not wrapped — use the AWS
console or `aws quicksight` for those.
