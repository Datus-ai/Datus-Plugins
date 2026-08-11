# datus-eks-plugin

Inspect Amazon EKS and provide short-lived Kubernetes authentication without
invoking the AWS CLI. The plugin is independently configured under
`agent.plugins.eks` and can be used directly or as the credential provider for
`datus-k8s-plugin`.

```yaml
agent:
  plugins:
    eks:
      datus-dev:
        default: true
        cluster: datus-dev-eks-cluster
        region: us-east-1
        role_arn: arn:aws:iam::123456789012:role/datus-dev-eks-operator

    k8s:
      datus-dev:
        default: true
        provider: eks
        namespace: analytics
        allowed_namespaces: analytics,analytics-staging
```

The k8s profile defaults `provider_profile` to its own profile name, so the
example resolves the `eks.datus-dev` profile automatically. No kubeconfig or
`aws` executable is needed in provider mode.

## Commands

```bash
datus eks clusters list
datus eks clusters describe
datus eks nodegroups list
datus eks addons list
datus eks access-entries list
datus eks fargate-profiles list
datus eks updates list
datus eks insights list
datus eks auth whoami
```

`datus eks kubernetes cluster` and `datus eks kubernetes credential` are
machine-facing JSON commands consumed by the k8s plugin. The credential command
is denied to the Agent's bash tool in both normal and auto modes so bearer
tokens are not surfaced in model-visible output.

All EKS operational commands in this first version are read-only.
