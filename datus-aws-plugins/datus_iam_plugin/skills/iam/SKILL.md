---
name: iam
description: Inspect AWS IAM read-only — roles, users, managed policies, policy documents, and simulate whether a principal can perform an action (the AccessDenied diagnostic) via the `datus iam` CLI
---

# IAM

`datus iam` inspects AWS IAM **read-only** — there are no mutating commands.
Global usage:

```
datus iam [--profile <env>] <command> [args...]
```

Add `-o json` for full output.

## Identity

```
datus iam whoami          # STS caller identity (account, arn, user id)
```

## Roles / Users / Policies

```
datus iam roles list | get <name> | attached <name> | trust <name>
datus iam users list | get <name> | attached <name>
datus iam policies list [--scope Local|AWS|All] | get <arn> | document <arn>
```

`roles trust` prints the assume-role (trust) policy; `policies document` prints
the default version's JSON document.

## Simulate (the AccessDenied diagnostic)

```
datus iam simulate principal <principal-arn> --action s3:GetObject [--action ...] [--resource <arn> ...]
datus iam simulate custom --policy '<policy JSON>' --action s3:GetObject [--resource <arn> ...]
```

Use `simulate principal` to answer "can this role/user perform this action on
this resource?" — the fastest way to explain a data job's `AccessDenied`. Each
result row shows `EvalActionName`, `EvalDecision` (allowed / explicitDeny /
implicitDeny) and `EvalResourceName`.

## Exit codes

`0` success · `1` runtime/API error · `2` usage · `3` config error.
