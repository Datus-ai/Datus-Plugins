# 面向任务的数据生态 Plugin Adapter 架构分析

> 状态：架构分析草案
> 日期：2026-07-30
> 范围：Datus Agent、当前 `datus-plugin.yml` plugin 机制，以及计算引擎、运行时、外部资源和调度/管控平台的组合接入

## 1. 结论摘要

对于 Flink、Spark、Hive、MaxCompute、Snowflake、Databricks 等任务型产品，
**适合抽象统一协议，不适合抽象成一个实现所有产品的万能 Adapter 类**。

推荐采用：

1. **统一任务模型和生命周期协议**：统一描述任务意图、提交计划、运行句柄、
   状态、日志和取消等通用语义。
2. **按能力组合 Provider**：一个产品声明自己能提供哪些能力，而不是被强制归入
   唯一类别。Databricks、MaxCompute、StreamPark 等都可能同时承担多个角色。
3. **保留产品原生扩展**：Savepoint、Spark UI、Hive Session、Snowflake Warehouse、
   Databricks Cluster Policy 等能力通过扩展字段或产品原生命令保留。
4. **由 Skill 负责编译和编排语义，由 Plugin 负责确定性执行**：Skill 理解用户意图、
   生成配置并选择路径；CLI/plugin 负责认证、API 调用、输出、错误码和权限。
5. **短期在 Agent 层组合，长期在 Datus Core 增加 capability registry 和结构化
   plugin invocation**。

核心判断可以概括为：

> 统一的是“任务控制协议、句柄和可观察性”，不是所有引擎的配置模型，也不是所有
> 产品的底层 API。

不建议现在创建一个持有所有平台凭据的 `datus task` 大型 plugin。当前 plugin profile
彼此隔离、禁止跨 plugin import，CLI catalogue 也只是描述信息；贸然加入统一门面会导致
配置复制、权限绕过、输出解析和依赖耦合。

## 2. 问题为什么会出现组合爆炸

一项数据任务至少存在四个相互独立、但在具体产品中又可能重叠的维度：

| 维度 | 示例 | 主要职责 |
|---|---|---|
| 计算语义 | Flink、Spark、Hive SQL、MaxCompute SQL、Snowflake SQL | 代码、SQL、依赖、并行度、引擎参数 |
| 执行运行时 | Kubernetes、YARN、Docker、Standalone、SaaS | 资源分配、进程/容器生命周期、网络与身份 |
| 外部资源 | Hive Metastore、S3、HDFS、Iceberg Catalog、Kafka/MQ | 输入输出、元数据、状态、制品和凭据可达性 |
| 调度/管控面 | Airflow、MWAA、DolphinScheduler、StreamPark、自建平台 | 定时、依赖、重试、审批、发布、运行记录 |

如果为每一种排列单独开发 plugin，会很快出现：

```text
引擎数 × 运行时数 × 资源组合数 × 调度平台数
```

但这些维度并不总能完全拆开：

- Flink Kubernetes Operator 把 Flink 生命周期映射为 Kubernetes CR。
- EMR Serverless 同时提供 Spark 运行时、任务 API 和运行观测。
- Databricks 同时提供 Spark、托管运行时、Jobs 控制面和部分数据治理能力。
- MaxCompute 是 SaaS 引擎与运行时的组合，用户通常接触不到底层调度资源。
- StreamPark 可以替用户管理 Flink on Kubernetes/YARN，底层运行时未必应由 Datus
  再次直接操作。
- MWAA 是托管 Airflow 环境；Airflow 提供调度语义，MWAA 主要增加环境、认证和托管边界。
- Iceberg 是表格式与 catalog 协议，不天然等于独立服务或任务运行时。

因此，架构必须允许“一个 Provider 实现多个能力”和“一个任务由多个 Provider 协作”，
不能假设每个维度永远对应一个独立产品。

## 3. 当前 Datus plugin 机制提供了什么

当前 plugin 契约具备以下有利基础：

- 每个 distribution 只注册一个 `datus.plugins` entry point。
- `datus-plugin.yml` 以声明方式提供 CLI、skills、system prompt、profile schema、
  permissions 和 commands catalogue。
- Profile 由 Datus 解析并展开环境变量，secret 字段不会进入 system prompt。
- Plugin 不导入 `datus`，也不跨 plugin import。
- Skill-only plugin 是合法形态。当前 Flink plugin 就只提供
  `flink-k8s-operator` skill，并把 Kubernetes 操作交给 `datus k8s`。
- 共享实现可以提取到非 plugin 的 library distribution；`datus-aws-common` 已验证
  这种方式适合共享认证、输出、错误处理和轮询等基础设施。
- 权限规则可以区分只读、常规变更、任务提交、代码发布和高风险操作。

仓库中的现有模式已经覆盖三种典型执行模型：

| 模式 | 当前例子 | 特征 |
|---|---|---|
| Reconcile/声明式资源 | Flink skill + k8s plugin | `apply` CR，Operator 异步收敛，资源名是主要句柄 |
| Run/调用式任务 | EMR Serverless、Glue Jobs、EMR Steps | 调 API 返回 run ID，随后轮询状态 |
| Orchestrated run | Airflow DAG Run、MWAA | 外层调度器拥有重试、依赖和运行历史 |

但当前契约尚未原生提供：

- 可被 Datus 自动索引的 task capability 声明；
- 统一的 `TaskSpec`、`TaskPlan`、`TaskHandle` 和状态 envelope；
- plugin 依赖、skill 依赖和 capability 依赖图；
- 结构化的 plugin-to-plugin 调用；
- 跨 plugin profile 引用和由 Core 代理的凭据解析；
- 高层操作拆成多个底层操作后的统一权限计划；
- 幂等键、关联 ID、父子运行句柄和任务状态存储；
- commands 的机器可验证输入/输出 schema。

`commands` catalogue 当前用于补全和说明，不能充当类型安全的 Adapter 注册表。

## 4. 推荐的分层架构

```text
用户意图 / SQL / 代码 / 运维请求
                |
                v
        Task Composition Skill
   解析意图、选 Provider、生成并展示计划
                |
                v
        Unified Task Protocol
 TaskSpec / TaskPlan / TaskHandle / Capabilities
       /             |              \
      v              v               v
 Engine Compiler  Execution       Orchestrator
 Skill/Provider   Provider         Provider
      |              |               |
      +-------- Resource Bindings ----+
                     |
                     v
         原生产品 Plugin / API / CRD
```

### 4.1 Engine Compiler

负责把引擎语义转为可执行制品或平台配置：

- SQL、JAR、Python、wheel、镜像和入口点；
- Flink checkpoint/savepoint、Spark conf、Hive session 参数；
- 引擎版本、依赖兼容性和任务模式；
- Application、Session、batch、streaming 等形态；
- 将通用 TaskSpec 编译成 CR、API payload、CLI 参数或 DAG task。

它通常更适合以 Skill 为主，因为构建过程依赖项目结构、用户意图和上下文判断。
脆弱且确定性的步骤可以放入资源模板或脚本。

### 4.2 Execution Provider

负责真正创建和运维一次执行：

- `validate`、`plan`、`submit/apply`；
- `get/list/status/wait`；
- `cancel`、`delete`；
- 可选的 `update`、`restart`、`suspend/resume`、`scale`、`retry`；
- 获取日志、事件、指标和 UI 链接。

Kubernetes Operator、YARN、EMR Serverless、Databricks Jobs、MaxCompute Instance
都可以是 Execution Provider，但支持的能力不同。

### 4.3 Orchestrator Provider

负责外层计划：

- DAG/工作流发布；
- 调度时间、依赖、重试、补数和 SLA；
- 触发 DAG run，查看 task instance；
- 记录父任务与实际计算任务之间的关系。

当任务经 Airflow 或 DolphinScheduler 启动时，调度器拥有外层生命周期；底层 Flink、
Spark 或 SQL run 仍可作为 child handle 提供诊断。Datus 不应同时绕过调度器直接重试
底层任务，否则会破坏调度平台的状态机。

### 4.4 Resource Provider

负责外部资源的发现、验证和有限运维，而不是假装自己是任务运行时：

- S3/HDFS：制品、checkpoint、日志和数据路径；
- Hive Metastore/Glue Catalog/Unity Catalog：库表和 schema；
- Iceberg：catalog、warehouse、table format 与版本能力；
- Kafka/MQ：topic、endpoint、consumer group 和连通性；
- Secret/IAM/ServiceAccount：身份引用与授权验证。

TaskSpec 只保存逻辑引用和 URI，不保存真实 secret。

### 4.5 Observability Provider

日志和状态通常由 Execution Provider 直接提供，但复杂部署可能需要单独能力：

- CloudWatch、Prometheus、Loki、厂商日志平台；
- Spark/Flink UI；
- Kubernetes events；
- lineage、审计记录和成本数据。

它可以作为独立 Provider，也可以由执行平台一并实现。

## 5. 统一协议应该包含什么

### 5.1 Capability，而不是固定继承树

Provider 声明细粒度能力，例如：

```text
task.validate
task.plan
task.submit
task.apply
task.status
task.list
task.wait
task.cancel
task.delete
task.logs
task.events
task.ui
task.update
task.restart
task.suspend
task.resume
task.scale
task.retry
task.snapshot
task.restore
orchestrator.deploy
orchestrator.trigger
resource.resolve
resource.validate
resource.inspect
```

每项能力至少携带：

- 协议版本；
- 支持的 engine、execution kind 和 mode；
- 输入/输出 schema；
- 是否改变状态、是否计费、是否发布代码；
- 是否支持幂等键、dry-run、wait；
- 产品原生扩展 schema；
- 运行时限制和前置依赖。

组合器根据 capability 求解，不按 plugin 名称硬编码所有逻辑。

### 5.2 TaskSpec

下面是建议模型的示意，不是当前 Datus manifest 可直接执行的配置：

```yaml
apiVersion: task.datus.ai/v1alpha1
kind: TaskSpec
metadata:
  name: orders-enrichment
spec:
  executionKind: streaming
  engine:
    type: flink
    version: "1.20"
    artifact:
      uri: project://target/orders-job.jar
    entrypoint: com.example.OrdersJob
    args: ["--env", "prod"]
    config:
      parallelism: 4
  target:
    provider: flink-k8s-operator
    profile: prod
    namespace: analytics
  orchestration:
    provider: airflow
    profile: prod
    schedule: "0 * * * *"
  bindings:
    - role: checkpoint-store
      provider: s3
      profile: prod
      uri: s3://data-prod/flink/checkpoints/orders
    - role: source
      provider: kafka
      profile: prod
      resource: orders-v2
    - role: catalog
      provider: iceberg-rest
      profile: prod
      resource: lakehouse
  policy:
    updateMode: savepoint
    retry:
      owner: orchestrator
  extensions:
    flink.apache.org:
      upgradeMode: savepoint
```

设计要求：

- `profile` 只是 profile 名称，不能展开或复制凭据。
- `extensions` 必须命名空间化，避免不同厂商字段冲突。
- `artifact` 和 binding URI 必须说明由哪个 actor 读取。
- TaskSpec 表达意图，不假装所有字段都能跨引擎迁移。

### 5.3 TaskPlan

在任何变更发生前，组合器应生成可审计计划：

```yaml
protocolVersion: task.datus.ai/v1alpha1
correlationId: task-20260730-001
providers:
  - plugin: flink
    capability: engine.compile.flink
  - plugin: k8s
    capability: task.apply
    profile: prod
  - plugin: s3
    capability: resource.validate
    profile: prod
steps:
  - action: build
    changesState: false
  - action: publish-image
    changesState: true
    requiresConfirmation: true
  - action: apply-flinkdeployment
    changesState: true
    requiresConfirmation: true
  - action: wait-ready
    changesState: false
warnings: []
```

权限必须落在每个实际步骤上。高层的一个 `submit` 不能掩盖镜像推送、集群级资源创建、
任务提交和外部数据写入等多个动作。

### 5.4 TaskHandle

所有执行模型都返回一个可序列化句柄：

```yaml
protocolVersion: task.datus.ai/v1alpha1
correlationId: task-20260730-001
provider: emr-serverless
profile: prod
executionKind: batch
native:
  applicationId: 00abc
  runId: jr-123
resourceRef: emr-serverless://00abc/jobs/jr-123
state:
  phase: RUNNING
  health: HEALTHY
  terminal: false
  rawState: RUNNING
links:
  ui: https://example.invalid/job/jr-123
parent: null
children: []
```

对 Kubernetes reconcile 模型，`native` 可以保存 `apiVersion/kind/namespace/name`；
对 Airflow，保存 `dag_id/run_id`；对同步 SQL，可以保存 query ID。

### 5.5 状态模型

建议最小统一状态：

```text
QUEUED
STARTING
RUNNING
SUCCEEDED
FAILED
CANCELLING
CANCELLED
SUSPENDED
UNKNOWN
```

同时保留：

- `rawState`：产品原始状态；
- `health`：`UNKNOWN/HEALTHY/DEGRADED/UNHEALTHY`；
- `terminal`：是否终态；
- `reason/message`；
- `observedAt`、开始和结束时间；
- batch、streaming、service、session 等 `executionKind`。

流任务长期处于 `RUNNING` 并不代表未完成；应通过 `health`、稳定性和 reconciliation
状态判断。Flink 的 `RUNNING/STABLE` 不能被错误映射成 batch 的“尚未结束”。

### 5.6 不应强行统一的语义

以下内容应通过 capability 或 extension 暴露：

- Flink savepoint/checkpoint、upgrade mode；
- Spark dynamic allocation、Spark UI、cluster policy；
- Hive/Trino session property；
- Snowflake warehouse、query tag、resource monitor；
- Databricks notebook/task graph、job cluster；
- MaxCompute quota、project、instance tunnel；
- 调度器的 backfill、clear、materialize、SLA；
- Kubernetes CRD 字段和 Operator reconciliation 状态。

`cancel` 和 `delete` 也必须分开：取消一次 run 与删除声明式资源不是同一操作。

## 6. 产品如何映射到能力模型

| 产品/组合 | 承担的角色 | 推荐接入边界 |
|---|---|---|
| Flink + Kubernetes Operator | Engine Compiler + Execution | Flink skill 生成 CR；k8s plugin 执行通用资源操作；Flink 解释状态与升级语义 |
| Flink + YARN | Engine Compiler + YARN Execution | 复用 Flink 构建语义，新增 YARN provider；保留 application/session 差异 |
| Spark + EMR Serverless | Engine + 托管 Execution | EMR Serverless plugin 直接实现 submit/status/cancel/logs/ui |
| Spark + Databricks | Engine + SaaS Execution + Control Plane | Databricks plugin 暴露 Jobs/Clusters/SQL 能力，Unity Catalog 可作为同产品的 resource capability |
| Hive/Spark + YARN/HDFS/HMS | Engine + Execution + Resources | Hive/Spark skill、YARN provider、HDFS provider、Metastore provider 组合 |
| MaxCompute | SaaS Engine + Execution + Resource | 一个产品 plugin 可同时声明 SQL、instance、table/catalog 能力，不强拆底层不可见运行时 |
| Snowflake | SQL Engine + SaaS Execution + Warehouse | 以 query/task/warehouse capability 为主，不强行模拟长生命周期计算集群 |
| Airflow | Orchestrator | DAG 发布、trigger、run/task 状态和日志；底层任务作为 child handle |
| MWAA | 托管环境/认证 + Airflow Orchestrator | MWAA plugin 负责环境和 token；尽量复用 Airflow 语义，避免把 opaque CLI passthrough 当统一协议 |
| DolphinScheduler | Orchestrator | 工作流定义、发布、调度和实例状态；具体 task type 使用产品扩展 |
| StreamPark | Flink Control Plane + Execution Broker | 由 StreamPark 作为生命周期 owner；底层 k8s/YARN 只用于经授权的诊断 |
| 自建平台 | 取决于其 API | 声明实际提供的 capabilities；不要根据产品类别预设能力 |
| S3/HDFS/Kafka/Iceberg/Metastore | Resource | 提供 resolve/validate/inspect，必要时提供资源 CRUD，但不伪装成 task runtime |

## 7. 典型数据架构的组合

### 7.1 Flink + Kubernetes + Kafka + Iceberg + S3 + Airflow

1. Flink skill 构建 JAR/镜像并生成 Operator CR。
2. Kafka/Iceberg/S3 provider 验证引用、权限和运行时可达性。
3. k8s plugin dry-run、apply 并读取 CR、Pod、events 和 logs。
4. 如果由 Airflow 调度，Airflow 保存外层 run；Flink CR 作为 child handle。
5. Savepoint 是 Flink 扩展能力，不进入所有引擎都必须实现的接口。

### 7.2 Spark + EMR Serverless + Glue Catalog + S3 + MWAA

1. Spark compiler 生成入口点和参数。
2. S3 plugin 验证制品；Glue plugin检查库表和 schema。
3. EMR Serverless plugin 提交并返回 application/run ID。
4. MWAA/Airflow 作为外层 orchestrator 时拥有调度和重试策略。
5. CloudWatch 可以补充日志和指标，但不能替代 EMR 原始运行状态。

### 7.3 Hive/Spark + YARN + HDFS + Hive Metastore + DolphinScheduler

1. Engine skill 生成 SQL、JAR 或 `spark-submit` 计划。
2. YARN provider 返回 application ID。
3. HDFS 和 Metastore provider 做路径/schema/权限 preflight。
4. DolphinScheduler 返回 workflow instance ID，并关联 YARN child handle。

### 7.4 Databricks 或 MaxCompute 一体化 SaaS

一个厂商 plugin 可以实现多个 capability。组合器不应为了架构形式强制拆出虚构的
Kubernetes/YARN adapter。外部 S3、catalog 或 orchestrator 只在实际跨产品时参与。

## 8. 外部资源不能只作为 URI 字符串处理

任务制品或数据路径能被 Datus 本机读取，不代表运行时能读取。每个 binding 应验证：

1. **读取 actor**：Datus、Operator、JobManager、Driver、Executor、调度器中的谁读取；
2. **身份传递**：ServiceAccount、IAM Role、STS、Hadoop credential、平台 connection；
3. **网络可达性**：VPC、DNS、proxy、private endpoint；
4. **协议能力**：filesystem plugin、S3 scheme allowlist、Hadoop connector；
5. **版本兼容性**：Flink/Spark 与 Iceberg、Kafka、Hadoop 版本；
6. **所有权和清理**：任务取消是否删除 checkpoint、savepoint、临时制品；
7. **敏感性**：TaskSpec 和日志中不能出现实际 secret。

当前 Flink SessionJob 的 `local://` 限制就是典型例子：JAR 在 Session Cluster 镜像中，
不代表 Operator Pod 能读取该路径。统一 Adapter 不能抹掉这类 actor-specific 语义。

## 9. 在当前 Datus 机制下如何落地

### 9.1 不新增 Core 能力时

近期可以只靠现有机制实现第一版：

1. 定义 `task.datus.ai/v1alpha1` 的 JSON Schema 和 conformance fixtures。
2. 为任务型 plugin 约定结构化 JSON 输出和统一错误 envelope。
3. 保留每个 plugin 的原生命令，在适合的 plugin 中逐步增加一致的任务命令：

   ```text
   capabilities
   tasks validate
   tasks plan
   tasks submit|apply
   tasks get|list|wait
   tasks cancel
   tasks logs|events|ui
   ```

4. 新增一个 composition skill，读取用户意图和已安装 skills，选择已有 plugin 命令。
5. Flink 暂时继续保持 skill-only：它编译 Operator CR，再调用 k8s plugin；不需要为了
   接口整齐立即增加空洞的 `datus flink` CLI。
6. 每一步仍通过实际 plugin 的权限规则执行，不由 composition skill 绕过确认。
7. 将非敏感 TaskHandle 保存为项目工件或会话工件，便于后续诊断；在 Core 原生支持前，
   不把它当成强一致状态库。

这一阶段的统一主要发生在 Skill 指令、输出 envelope、schema 和测试，不需要 plugin
互相 import。

### 9.2 为什么暂不推荐 `datus task` 门面 plugin

当前 CLI 调用只收到本 plugin 的 resolved profile。一个门面 plugin 无法安全、自然地
获取其他 plugin 的 profile，除非：

- 复制所有平台配置和凭据；
- 跨 plugin import；
- 启动嵌套的 `datus <provider>` 子进程并解析文本输出。

三种方式都会损害 profile 隔离、权限审计、错误处理或 plugin 独立发布，不应成为正式
架构。`datus task` 更适合在 Datus Core 提供结构化 invocation 后成为核心命令或薄门面。

### 9.3 Shared library 的边界

可以像 `datus-aws-common` 一样提取成熟的共享基础设施，但应遵守：

- 先在至少三个实现中验证重复模式，再提取；
- 共享 JSON schema、状态映射、错误 envelope 和 conformance test；
- 不包含所有厂商业务逻辑；
- 不成为必须导入 Datus Core 的 plugin SDK；
- provider-specific payload 和扩展仍留在各 plugin。

## 10. 建议的 Datus Core 演进

### 10.1 Manifest capability 声明

未来 manifest 版本可增加机器可读能力，例如：

```yaml
capabilities:
  - id: task.execution.spark.emr-serverless
    protocol: task.datus.ai/v1alpha1
    operations: [validate, submit, status, list, cancel, logs, ui]
    executionKinds: [batch]
    inputSchema: schemas/emr-serverless-task.json
    handler: datus_emr_serverless_plugin.task_adapter:invoke
```

需要同时定义向后兼容、schema 打包、权限映射和 handler 加载规则。当前
`manifest_version: 1` 不应直接写入未受支持字段并假设 Core 会读取。

### 10.2 结构化 plugin invocation

Core 提供类似以下内部工具：

```text
invoke_plugin(
  plugin,
  profile,
  capability,
  operation,
  arguments,
  correlation_id,
  idempotency_key
) -> structured result
```

它应负责：

- 解析目标 plugin 的 profile，但不把 secret 暴露给组合 Skill；
- 执行目标 plugin 的 permissions；
- 校验输入/输出 schema；
- 传播关联 ID、幂等键和取消信号；
- 记录实际调用链和审计日志；
- 避免通过 Bash 文本解析实现组合。

### 10.3 Capability resolver

Resolver 根据以下条件选择 Provider：

1. 用户显式指定的产品、profile 和环境；
2. TaskSpec 所需 engine/mode/execution kind；
3. 已安装并配置的 plugin capabilities；
4. 外部资源和网络约束；
5. 权限、成本、数据驻留和组织策略；
6. 版本兼容性；
7. 若存在多个候选，展示计划并让用户选择，而不是静默切换平台。

### 10.4 Profile 和资源引用

Core 应解析 `plugin/profile/resource` 引用，并只把对应 profile 传给实际 provider。
组合器只能看到脱敏元数据，例如 region、namespace、endpoint 类型和 profile 名称。

### 10.5 运行图

一次用户任务可能包含：

```text
Airflow DAG Run
└── Airflow Task Instance
    └── EMR Serverless Job Run
        ├── S3 artifact
        ├── Glue tables
        └── CloudWatch log streams
```

Core 应保存 parent/child handle，而不是只保留最后一个 run ID。这样才能回答“为什么
任务失败”“谁拥有重试”“应该在哪里取消”等问题。

## 11. 安全、权限和治理

统一协议不能削弱现有 plugin 权限：

- `plan/validate/status/logs` 通常只读；
- submit、发布代码、触发 DAG、写数据、产生费用应要求确认；
- cancel、restart、resume 的风险由 Provider 和执行类型决定；
- delete、资源清理、savepoint disposal、cluster-scoped 变更应单独确认；
- 高层计划必须显示所有底层写操作；
- 每个 Provider 继续使用自己的最小权限身份；
- profile secret 不进入 TaskSpec、TaskHandle、system prompt 或项目工件；
- 运行时 RBAC/IAM 是最终安全边界，default namespace/默认 profile 只是便利配置；
- 调度器已经拥有任务时，Agent 不应无授权地直接修改 child runtime。

还需要区分：

- **控制面权限**：谁能提交、取消、部署；
- **数据面权限**：任务进程能读写哪些数据；
- **资源面权限**：谁能创建 CRD、集群、topic、table；
- **可观察性权限**：日志可能包含敏感数据。

## 12. 测试和兼容性

建议建立五层测试：

1. **Schema contract**：TaskSpec、Plan、Handle、capability descriptor。
2. **Provider conformance**：必选操作、状态映射、错误码、幂等性和脱敏。
3. **Golden mapping**：TaskSpec 到 CR/API payload/DAG 的固定样例。
4. **真实环境集成**：minikube、Docker/YARN 测试环境和厂商 sandbox。
5. **组合测试**：父子句柄、失败传播、取消、超时、部分成功和权限拒绝。

Provider 必须保留 raw state/payload 的安全子集，避免统一映射隐藏重要诊断信息。
协议和产品 plugin 分别使用 SemVer；`v1alpha1` 阶段允许快速迭代，但每个 capability
必须声明自己经过了哪些真实环境验证。

## 13. 推荐演进路线

### Phase 0：协议 RFC

- 确定 TaskSpec、TaskPlan、TaskHandle、状态模型和 capability 命名。
- 明确 submit/apply、cancel/delete、outer/child run 的语义。
- 选择三个差异足够大的试点：
  - Flink + Kubernetes Operator；
  - Spark + EMR Serverless；
  - Airflow DAG Run。

### Phase 1：不改 Datus Core 的试点

- 增加 schema、golden fixtures 和 conformance tests。
- 统一 JSON 输出 envelope。
- 编写 composition skill。
- 继续使用现有 plugin profile 和权限。
- 验证同一用户意图能生成三个不同执行模型的清晰计划。

### Phase 2：扩展产品覆盖

- 增加 Databricks、YARN/Hive、DolphinScheduler 或 StreamPark 中至少两个。
- 验证一体化 SaaS 与可拆分开源栈都能表达。
- 验证 S3/HDFS/catalog/MQ 的 actor-specific preflight。
- 稳定协议后再提取共享 conformance 工具。

### Phase 3：Datus Core capability registry

- 增加 manifest capability、结构化 invocation、profile reference 和运行图。
- 让 Agent 基于机器可读能力选择 Provider。
- 将 `datus task` 实现为 Core 命令或薄门面，而不是持有所有平台凭据的超级 plugin。

### Phase 4：生态化

- 发布 Provider 开发规范和认证测试套件。
- Marketplace 显示支持的 engines、runtimes、operations 和验证等级。
- 支持组织策略对 Provider、region、数据位置和计费行为进行约束。

## 14. 不推荐的方案

### 14.1 一个巨大的 `TaskAdapter` ABC

所有实现被迫提供相同方法会产生大量 `NotImplemented`，同时丢失 savepoint、backfill、
warehouse 等关键语义。应改为 capability 集合和可选扩展。

### 14.2 为每种排列开发一个 plugin

会复制认证、资源访问、状态解析和调度逻辑，无法承受生态数量增长。

### 14.3 只让 Agent 自由拼接命令

适合早期探索，但长期缺少 schema、幂等性、句柄、权限计划和稳定错误处理。Skill 应负责
推理，不应成为唯一的运行时协议。

### 14.4 让 plugin 互相 import 或共享全部凭据

这破坏当前独立分发和 profile 隔离，也让升级、审计与权限边界变得不可控。

### 14.5 过度追求可移植 TaskSpec

同一份配置无法无损迁移 Flink、Spark、Snowflake 和 MaxCompute。TaskSpec 应统一意图和
生命周期，将不可移植部分明确放入 engine/provider extensions，而不是假装差异不存在。

## 15. 最终建议

1. 接受“统一 Adapter”的方向，但将其定义为**统一任务协议 + capability-based
   providers**。
2. 维持现有“一 distribution 一 plugin、profile 隔离、无跨 plugin import”的边界。
3. 继续采用 Flink skill + k8s executor 这种可组合模式，同时允许 EMR Serverless、
   Databricks、MaxCompute 等一体化产品在一个 Provider 中实现多项能力。
4. 先用 Flink/Kubernetes、EMR Serverless、Airflow 三个试点验证 reconcile、run 和
   orchestrated-run 三种模型。
5. 在协议和真实测试稳定前，不创建持有所有凭据的 `datus task` 超级 plugin。
6. 中期把结构化 capability、invocation、profile reference 和运行图加入 Datus Core，
   让 Datus Agent 能可靠对接大规模数据生态，而不是依赖排列组合式硬编码。

这一方案既保留 Agent/Skill 对复杂项目语义的理解能力，也给 plugin 执行层提供可测试、
可审计、可扩展的稳定边界。

## 16. 仓库内参考实现

- [Plugin contract 与开发约束](../README.md#the-plugin-contract)
- [Flink skill-only plugin](../datus-flink-plugin/README.md)
- [Flink Kubernetes Operator skill](../datus-flink-plugin/datus_flink_plugin/skills/flink-k8s-operator/SKILL.md)
- [Kubernetes plugin manifest](../datus-k8s-plugin/datus_k8s_plugin/datus-plugin.yml)
- [EMR Serverless plugin manifest](../datus-aws-plugins/datus-emr-serverless-plugin/datus_emr_serverless_plugin/datus-plugin.yml)
- [Airflow plugin manifest](../datus-airflow-plugin/datus_airflow_plugin/datus-plugin.yml)
- [共享非 plugin library：datus-aws-common](../datus-aws-plugins/datus-aws-common/README.md)
