---
name: ack-setup
description: Configure or troubleshoot an Alibaba Cloud ACK profile and its paired datus k8s provider profile, including the default credential chain, access keys or STS, RAM roles, RRSA/external credentials, endpoint selection, and verification.
requires_mutable_config: true
---

# ACK Setup

Add profiles under `agent.plugins.ack.<profile>` in the active Datus config.
Prefer the Alibaba Cloud default credential chain, an attached RAM/ECS role,
RRSA, or another short-lived identity over static keys.

## Collect and configure

Collect the profile name, `region_id`, `cluster_id`, authentication mode,
endpoint choice, and Kubernetes namespace.

```yaml
agent:
  plugins:
    ack:
      prod:
        default: true
        region_id: cn-hangzhou
        cluster_id: c123456789

        # Omit for the default credential chain, otherwise choose one mode.
        # access_key_id: ${ALIBABA_CLOUD_ACCESS_KEY_ID}
        # access_key_secret: ${ALIBABA_CLOUD_ACCESS_KEY_SECRET}
        # security_token: ${ALIBABA_CLOUD_SECURITY_TOKEN}  # with temporary keys
        # role_arn: acs:ram::123456789012:role/datus-ack
        # role_session_name: datus-ack
        # credentials_uri: ${ALIBABA_CLOUD_CREDENTIALS_URI}

        # endpoint: cs.cn-hangzhou.aliyuncs.com
        use_private_endpoint: "false"
        credential_ttl_minutes: "15"
        timeout: "60"
        max_attempts: "3"

    k8s:
      prod:
        provider: ack
        namespace: analytics
        allowed_namespaces: analytics
```

Write secrets only as `${ENV_VAR}` references and require those variables in
the Datus process environment. Configure `access_key_id` and
`access_key_secret` together; add `security_token` only for STS credentials.
`role_arn` assumes a RAM role through the source identity. `credentials_uri`
selects an external credential provider and is itself treated as sensitive.

`endpoint` overrides the ACK OpenAPI endpoint. `use_private_endpoint` selects
the cluster's private Kubernetes endpoint. `credential_ttl_minutes` controls
the requested temporary kubeconfig duration and must be between 15 and 4320
(3 days), the range ACK accepts.

Grant only the RAM permissions needed to read clusters/node pools/add-ons/tasks
and retrieve temporary user kubeconfig. Kubernetes RBAC separately limits the
returned cluster identity. Temporary kubeconfigs containing either a bearer
token or a client certificate/private key pair are supported. The complete
kubeconfig is never persisted; certificate credentials are held in owner-only
temporary files for the lifetime of one `datus k8s` process.

If k8s and ACK profile names differ, add `provider_profile: prod` to k8s. For a
non-default provider config file, also set `provider_config`. Keep Alibaba Cloud
credentials only in the ACK profile.

## Verify

```bash
datus ack --profile prod auth check -o json
datus ack --profile prod clusters describe -o json
datus k8s --profile prod version
datus k8s --profile prod auth can-i get pods -n analytics
```

For failures, distinguish credential-chain/RAM errors, malformed temporary
kubeconfig, Kubernetes RBAC denial, and private endpoint reachability.

If this environment cannot edit the active config, ask the deployment
administrator to make the change.
