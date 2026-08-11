---
name: adls
description: Browse, inspect, preview, upload, download, copy, sync, move, delete, and sign Azure Blob or ADLS Gen2 paths, and inspect filesystems or manage ACLs with datus adls. Use for abfss:// data movement, artifacts, SAS URLs, or hierarchical namespace ACL operations.
---

# ADLS

Use `datus adls` for Azure Blob and ADLS Gen2 data.

```bash
datus adls [--profile <env>] <command> [args...]
```

Use `abfss://filesystem/path` URIs (`abfs://` and `adls://` are also parsed).
A bare path uses the profile's default `container`. A trailing `/` marks a
directory/prefix and determines that directory clients are used for recursive
delete and ACL operations. Commands with `-o` accept
`table|json|yaml|plain`.

## Command catalogue

Browse and read:

```bash
datus adls ls [uri] [-r|--recursive] [--limit N] [-o json]
datus adls stat <uri> [-o json]
datus adls cat <uri> [--max-bytes N]
datus adls head <uri> [-n|--lines N] [--bytes N]
datus adls filesystems list [-o json]
datus adls acl get <uri> [-o json]
```

`ls` without a URI lists filesystems. `cat` writes decoded text; `head`
downloads at most 65536 bytes and prints 10 lines by default. Use `stat` and
`acl get` before changes.

Move and publish data:

```bash
datus adls cp <src> <dst> [-r|--recursive]
datus adls sync <local-dir> <abfss://filesystem/prefix/>
datus adls mv <src> <dst> [-r|--recursive]
datus adls rm <uri> [-r|--recursive] [-y|--yes]
```

- `cp` supports local-to-ADLS, ADLS-to-local, and ADLS-to-ADLS; at least one
  side must be an ADLS URI. Upload/copy overwrites destination files.
- `sync` uploads local files whose remote size differs. It does not download,
  delete remote-only paths, or compare checksums.
- `mv` copies and then deletes the source. Treat it as destructive.
- `rm` prompts; non-interactive callers must pass `-y`. With `-r`, it deletes
  the directory named by the path. Confirm the trailing slash/path semantics
  and run `ls -r` before deletion.

SAS and ACL changes:

```bash
datus adls sas <uri> [--permissions r] [--expires 3600]
datus adls acl set <uri> --acl '<acl-spec>' [-y|--yes]
```

SAS output is a credential until expiry; permissions containing writes or
deletes materially increase risk. Never expose the URL in logs or chat. SAS
uses the configured account key or a user-delegation key; it requires the Blob
SDK and appropriate delegation permission.

`acl set` replaces the complete ACL rather than merging an entry. Run
`acl get`, preserve required owner/group/mask entries, and review before
confirming. ACLs and real directories require a hierarchical-namespace (HNS)
account. Blob-only accounts may support object data operations but not ADLS
directory/ACL semantics.

## Permission posture and workflow

Reads are allowed directly. In normal mode, `cp`, `sync`, `mv`, `rm`, `sas`,
and `acl set` require approval. Auto mode permits data writes/moves but still
asks for deletion, SAS generation, and ACL replacement.

```bash
datus adls --profile prod sync ./artifacts/ abfss://builds/app/v1.4.0/
datus adls --profile prod ls abfss://builds/app/v1.4.0/ -r -o json
datus adls --profile prod stat abfss://builds/app/v1.4.0/app.tar.gz -o json
```

Azure RBAC grants data-plane access; on HNS accounts, POSIX-like ACL traversal
and access can additionally restrict paths. Account keys and SAS bypass or
change parts of that authorization model, so prefer Entra identities.

## Exit codes

`0` success · `1` runtime/API error · `2` usage/cancelled confirmation ·
`3` config error · `8` missing dependency · `130` interrupted.
