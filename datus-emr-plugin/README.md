# datus-emr-plugin

A [Datus](https://datus.ai) plugin to submit and monitor **steps on existing
Amazon EMR (on EC2) clusters** from `datus emr ...`, inspect clusters/instances,
and read step logs from S3. Backed by boto3. Cluster provisioning/termination is
out of scope (use IaC).

```bash
pip install datus-emr-plugin
```

## Configuration

Profiles live under `agent.plugins.emr.<profile>` in Datus' `agent.yml`:

```yaml
agent:
  plugins:
    emr:
      prod:
        default: true
        region: us-east-1
        cluster_id: j-XXXXXXXXXXXXX      # optional default cluster
        log_uri: s3://my-emr-logs/emr/    # optional; for `steps logs`
        # credentials: standard AWS chain, or profile / keys / role_arn
```

## Commands

| Group | Subcommands |
|---|---|
| `clusters` | `list`, `describe`, `instances` |
| `steps` | `list`, `describe`, `add [--wait]`, `cancel`, `logs` |

```bash
datus emr clusters list --state WAITING
datus emr steps add j-XXX --name load --command 'spark-submit s3://b/job.py' --wait
datus emr steps logs j-XXX s-YYY --stderr
```

`steps add` submits billed work (always confirmed); `steps cancel` is routine.

## Exit codes

`0` success · `1` runtime/API error (also: failed step under `--wait`) · `2`
usage · `3` config error.

## Development

```bash
uv run --package datus-emr-plugin pytest datus-emr-plugin
```

Never imports `datus`; registers the `emr` entry point in `datus.plugins`.
Shared boto3 plumbing lives in `datus-aws-common`. Bundled skills: `emr` and
`emr-setup`.
