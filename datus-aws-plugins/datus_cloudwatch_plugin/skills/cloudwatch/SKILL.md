---
name: cloudwatch
description: Query AWS CloudWatch logs (groups/streams/tail/Logs Insights), metrics, alarms and dashboards through the `datus cloudwatch` CLI
---

# CloudWatch

`datus cloudwatch` reads AWS CloudWatch through boto3. Global usage:

```
datus cloudwatch [--profile <env>] <group> <subcommand> [args...]
```

`--profile` (before the group) selects the configured environment; add `-o json`
to any command for machine-readable output (tables show a curated subset).
Everything here is read-only except `alarms set-state`.

## Logs

```
datus cloudwatch logs groups [-p /aws/glue] [--limit N]
datus cloudwatch logs streams <group> [--order-by LastEventTime|LogStreamName] [--limit N]
datus cloudwatch logs get <group> <stream> [--limit N] [--start-from-head]
datus cloudwatch logs tail <group> [--filter 'ERROR'] [--since 15m] [--follow] [--limit N]
datus cloudwatch logs insights <group>... -q '<query>' --start -1h [--end now] [--limit N]
```

- `tail --follow` polls for new events until interrupted; `--since` takes
  `30s/15m/2h/1d`. `--filter` uses CloudWatch **filter pattern** syntax.
- `insights` runs a Logs Insights query and **waits** for results (exit 1 if the
  query does not reach `Complete`). `--start`/`--end` accept ISO 8601, `now`, or
  relative `-1h/-30m/-2d`. Example query:
  `fields @timestamp, @message | filter @message like /ERROR/ | sort @timestamp desc | limit 20`.
- Glue job logs live under `/aws-glue/jobs/output` and `/aws-glue/jobs/error`.

## Metrics

```
datus cloudwatch metrics list [--namespace AWS/Lambda] [--name Errors]
datus cloudwatch metrics get --namespace AWS/Lambda --name Errors --stat Sum \
    --start -1h [--end now] [--period 300] [-d FunctionName=my-fn]
```

`--stat` is one of Average/Sum/Minimum/Maximum/SampleCount; `-d Name=Value` adds
a dimension (repeatable).

## Alarms

```
datus cloudwatch alarms list [-p prefix] [--state ALARM]
datus cloudwatch alarms get <name>
datus cloudwatch alarms history <name> [--limit N]
datus cloudwatch alarms set-state <name> --state ALARM|OK|INSUFFICIENT_DATA [--reason TEXT]
```

`set-state` is for testing composite alarms or temporary suppression — it does
not change the alarm definition and the next evaluation overwrites it.

## Dashboards

```
datus cloudwatch dashboards list [-p prefix]
datus cloudwatch dashboards get <name>
```

## Exit codes

`0` success · `1` runtime/API error (also: an insights query that did not
complete) · `2` usage · `3` profile/config error.
