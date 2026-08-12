# datus-ack-plugin

Read-only Alibaba Cloud ACK inspection and temporary Kubernetes authentication
for `datus-k8s-plugin`. The plugin does not invoke the Alibaba Cloud CLI and
does not persist complete kubeconfig or bearer tokens. Client certificate
credentials are exposed only to the k8s plugin through `ExecCredential`; the
k8s plugin keeps them in owner-only temporary files for one command process.
