# 数据领域 Plugin 候选清单（决策稿）

> 用途：用来**挑选**要实现哪些 plugin，不是实现说明。勾选后再对每个候选跑
> `/datus-plugin-development`，由 skill 产出正式 design draft（config schema、
> 命令逐条 + 文档引用、permission 规则、skill 计划）。
>
> 状态：待决策 · 最后更新 2026-08-10

---

## 0. 先划边界：什么该做 plugin，什么不该

Datus 生态里有四类扩展点。**已决定：bi adapter 与 scheduler adapter 后续废弃，统一由 plugin 机制承接**（迁移方案见 §13）。

| 扩展点 | 仓库 | 职责 | 已有 | 走向 |
|---|---|---|---|---|
| db adapter | `datus-db-adapters` | 连接 + 执行 SQL + 元数据 + 方言 → 成为 agent 的数据源 | athena, bigquery, clickhouse, clickzetta, greenplum, hive, mysql, postgresql, redshift, snowflake, spark, sqlalchemy, starrocks | **保留** |
| ~~bi adapter~~ | `datus-bi-adapters` | BI 平台读写抽象（dashboard/chart/dataset） | superset, grafana | **废弃 → plugin** |
| ~~scheduler adapter~~ | `datus-scheduler-adapters` | 统一调度 API（submit/trigger/pause/runs/logs） | airflow, mwaa, mwaa-serverless | **废弃 → plugin** |
| semantic adapter | `datus-semantic-adapter` | 语义层/指标模型 | metricflow | 保留（暂） |
| **plugin（本仓库）** | `Datus-Plugins` | `datus <name>` CLI + bundled skills；**且接管 BI / 调度的全部能力面** | 14 个（airflow, statsig, k8s, flink, 9×aws） | **唯一扩展方式** |

**判定规则（四条）**

1. 需求是"把这个库当数据源查询/取元数据" → 做 **db adapter**，不要做 plugin。
2. 需求是 **BI 平台或调度平台** → 一律做 **plugin**，且必须**全量覆盖**原 adapter 的能力（见 §13 的映射表），不能只做运维面。
3. 其他"运维它、诊断它、把东西部署进去、审计它" → 做 **plugin**。
4. "一套方法论/操作剧本，不需要新 API 调用" → 只做 **skill**，挂到已有 plugin 上（如 `datus-flink-plugin` 是纯 skill 插件，`k8s-jvm-classpath` 挂在 k8s 上）。

> 仍有 db adapter 的产品**依然值得做 plugin**——两者不重叠。例：Snowflake adapter 让 agent 能查表；
> Snowflake plugin 让 agent 能查 `QUERY_HISTORY` 定位慢查询、resize warehouse、审计 grants、算 credit 花销。
>
> 但 BI / 调度类**不存在这种分工了**：plugin 就是全部。原本 adapter 暴露给 agent 的 31 个 function tool
> 必须在 plugin 的命令面里找到等价物，否则是能力回退。

### 0.1 有 db adapter 的产品，额外能力做 skill 还是 CLI？（配置重复问题）

两棵配置树是**分开的**，这决定了答案：

| 形态 | 配置位置 | 是否与 db adapter 重复 |
|---|---|---|
| 纯 skill（markdown，无代码） | **无配置** | **零重复**——skill 不持有配置，走 agent 已有的 `execute_sql`（即 `services.datasources.<name>` 那份连接） |
| skill + `tool_transformers`（无 CLI、无 `config_schema`） | **无配置** | **零重复**，且能补回权限门禁 |
| plugin 带 CLI | `agent.plugins.<name>.<profile>` | **必然多一份配置块**——plugin 禁止 import datus，读不到 `services.datasources` |

重复的是**配置块**，不是密钥（两处都写 `${SNOWFLAKE_ACCOUNT}`）。代价是"用户配两遍、两边会漂移"。

**判据一句话：这个额外能力能不能用 SQL 表达？** 能 → skill（零重复、零代码、零维护）；不能 → plugin（认下一份配置块重复）。

| 产品 | 额外能力的 SQL 可达性 | 结论 |
|---|---|---|
| **Snowflake** | `ACCOUNT_USAGE.QUERY_HISTORY` / `WAREHOUSE_METERING_HISTORY`（成本）、`ALTER WAREHOUSE SET WAREHOUSE_SIZE`（resize）、`SHOW GRANTS`、task/stream DDL、`COPY INTO`、`CLONE`、time travel —— **≈100%** | **skill 形态** |
| **ClickHouse** | `system.query_log` / `parts` / `merges` / `mutations` / `replicas` 全是表；`OPTIMIZE TABLE` —— **≈100%** | **skill 形态** |
| **PostgreSQL / MySQL 运维** | `pg_stat_activity`、`pg_locks`、`EXPLAIN ANALYZE`、`VACUUM`、`SHOW PROCESSLIST` —— **100%** | **skill 形态** |
| **StarRocks** | `SHOW ROUTINE LOAD` / `PROC` / `TABLET`、`REFRESH MV` 可达；stream load、profile 下载走 FE HTTP | 混合：skill 为主 + 小 CLI |
| **Redshift** | `STL_*` / `SYS_*` 视图可达；clusters / WLM / datashare 运维要 boto3 | 混合 |
| **BigQuery** | `INFORMATION_SCHEMA.JOBS` 可达；`--dry-run` 预估、`load` / `extract`、reservations、IAM **不是 SQL** | plugin |
| **Databricks** | UC grants 是 SQL；clusters / jobs / DLT / workspace / secrets 是 REST | plugin |
| **Kafka / Airflow / k8s / S3 / Iceberg(pyiceberg)** | 无 SQL 通道 | plugin |

**skill 形态的真实代价**：走 `execute_sql` 就**绕过了 manifest permissions 的 `ask` 门禁**——
`ALTER WAREHOUSE`、`OPTIMIZE TABLE`、`VACUUM FULL` 在 agent 眼里只是普通 SQL，bash-pattern 权限管不到。
用 `tool_transformers` 拦 `db_tools.execute_sql` 补回来（这正是它的 canonical use case）：

```yaml
manifest_version: 1        # cli 与 config_schema 都是可选的
skills: skills
tool_transformers:
  "db_tools.execute_sql": datus_snowflake_ops.transformers:gate_ddl
```

`datus-flink-plugin` 已是"无 CLI 纯 skill"的先例，这只是多挂一个 transformer。

**一个例外**：`ACCOUNT_USAGE` 需要 ACCOUNTADMIN 或显式 grant，adapter 里配的 role 可能不够，
可能要在 `services.datasources` 下加第二个连接（如 `snowflake_admin`）。那仍在 datasources 树内，不构成与 plugin 的重复。

**明确不建议做 plugin 的：** DuckDB / SQLite（core 内置）；纯 SQL 方言接入类（→ adapter）；
Jupyter / Deepnote 之类 notebook 托管；通用协作工具（Jira / Slack / Notion，非数据方向）。

---

## 1. 能力轴（用来判断一个 plugin 做没做全）

每个候选都标注它应覆盖的能力轴。**低于 3 轴的候选，通常应该降级成 skill 或合并进别的 plugin。**

| 轴 | 含义 | 典型命令 |
|---|---|---|
| **A** 元数据/资产 | catalog、schema、资产清单、依赖 | `catalogs`, `tables`, `assets`, `describe` |
| **B** 作业生命周期 | 提交、触发、取消、重跑、状态 | `submit`, `trigger`, `cancel`, `retry`, `status` |
| **C** 运行时诊断 | 日志、profile、history、指标、explain | `logs`, `history`, `profile`, `explain`, `top` |
| **D** 资源与运维控制 | 集群/warehouse 起停扩缩、配置、维护 | `scale`, `resume`, `restart`, `compact`, `vacuum` |
| **E** 数据搬运与产出 | load/unload、导入导出、部署发布 | `load`, `copy`, `export`, `deploy`, `publish` |
| **F** 治理 | 权限、血缘、质量、成本 | `grants`, `lineage`, `usage`, `cost`, `checks` |

**Skill 约定**（与现有插件一致）：每个 plugin 出 `<name>`（使用参考）+ `<name>-setup`（配置引导），
再加 0–2 个**专项 skill**——专项 skill 才是 agent 价值密度最高的地方（对标 `flink-local-dev`、`k8s-jvm-classpath`）。

**工作量粗估**：S = ≤10 命令 / 1–2 人日 · M = 10–25 命令 / 3–5 人日 · L = 25+ 命令或需新 common 库 / 1–2 周。

---

## 2. 云数据仓库 / Lakehouse 平台

这一档 ROI 最高：API 成熟、单产品覆盖面极广、运维痛点集中在成本与慢查询。

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 接入 | 优先级 | 量 |
|---|---|---|---|---|---|:--:|:--:|
| **Snowflake** | **`datus-snowflake-ops-plugin`（skill 形态，无 CLI）** — 见 §0.1 | 不需要 CLI：全部能力用 SQL 表达，走 db adapter 已有连接 | `snowflake-cost-triage`、`snowflake-slow-query-triage`、`snowflake-warehouse-sizing`、`snowflake-grants-audit` | A C D E F | 复用 `datus-snowflake` adapter 的 `execute_sql`；`tool_transformers` 做 DDL 门禁 | **P0** | **S**（原估 L） |
| **Databricks** | `datus-databricks-plugin` | `clusters` `jobs` `sql-warehouse` `uc`(catalog/schema/table/grants/lineage) `dlt` `fs`(volumes) `workspace` `query-history` `secrets` | `databricks-job-failure-triage`、`uc-permission-audit` | A B C D E F | `databricks-sdk` + REST 2.x + system tables | **P0** | L |
| **BigQuery** | `datus-bigquery-plugin` | `jobs` `query --dry-run` `datasets` `tables` `load` `extract` `routines` `reservations` `iam` `usage` | `bq-dryrun-costing`（跑前算钱）、`bq-slot-contention-triage` | A B C E F | `google-cloud-bigquery`；`INFORMATION_SCHEMA.JOBS` | **P0** | M |
| **Redshift** | 扩 `datus-aws-plugins`（混合） | CLI 留 `clusters` `wlm` `datashare`（boto3）；`STL_*`/`SYS_*` 诊断、`UNLOAD`/`COPY`、`grants` 走 skill | `redshift-copy-error-triage`、`redshift-queue-contention-triage` | B C D E F | boto3 + 复用 `datus-redshift` adapter | P1 | S–M |
| **ClickHouse** | **`datus-clickhouse-ops-plugin`（skill 形态）** | 不需要 CLI：`system.*` 全是表，`OPTIMIZE TABLE` 是 SQL | `clickhouse-merge-backlog-triage`、`clickhouse-parts-health`、`clickhouse-replication-triage` | A C D E | 复用 `datus-clickhouse` adapter + transformer 门禁 | P1 | **S**（原估 M） |
| **StarRocks / Doris** | `datus-starrocks-plugin`（混合：skill 为主 + 小 CLI） | CLI 只留 SQL 到不了的：`stream-load` `profile download` `fe-http`；其余（`SHOW ROUTINE LOAD`/`PROC`/`TABLET`、`REFRESH MV`）走 skill | `starrocks-load-failure-triage`、`starrocks-mv-tuning`、`starrocks-tablet-health` | A B C D E | MySQL 协议（adapter）+ FE HTTP API（CLI 部分，可参考本地 `mcp-server-starrocks`） | P1 | S–M |
| **Athena** | 扩 `datus-aws-plugins` | `queries` `workgroups` `named-queries` `data-catalog` `usage` | `athena-scan-cost-reduction` | B C F | boto3；复用 aws-common | P2 | S |
| **Trino / Starburst** | `datus-trino-plugin` | `queries`(list/kill/explain) `catalogs` `nodes` `runtime` `resource-groups` | `trino-query-plan-triage` | A B C D | REST API + `trino` client（**注意：db adapter 侧还没有 trino，建议先补 adapter**） | P1 | M |
| Dremio / Firebolt / Vertica / Teradata / Greenplum | — | — | — | — | — | P3 | — |

---

## 3. 湖仓表格式与元数据目录

对"数据工程 agent"契合度最高的一档——表维护是纯手工活，最适合托给 agent。

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 接入 | 优先级 | 量 |
|---|---|---|---|---|---|:--:|:--:|
| **Apache Iceberg** | `datus-iceberg-plugin` | `catalog` `namespaces` `table`(describe/schema/snapshots/history/files/partitions/manifests) `maintenance`(expire-snapshots/rewrite-data-files/remove-orphan-files) `rollback` `time-travel` `migrate` | `iceberg-table-maintenance`（小文件/快照膨胀处置）、`iceberg-schema-evolution` | A C D E | `pyiceberg` + REST catalog / Glue / Nessie | **P0** | M |
| **Delta Lake** | `datus-delta-plugin` | `history` `describe-detail` `vacuum` `optimize`(zorder) `restore` `convert` `checkpoint` | `delta-optimize-planning` | A C D | `deltalake`(rust) 或 Databricks SQL；与 databricks plugin 有交集 | P1 | S |
| **Unity Catalog** | 合并进 `datus-databricks-plugin` | `uc` 命令组 | 见 databricks | A F | 同上 | P0（随 DBX） | — |
| **DataHub** | `datus-datahub-plugin` | `search` `entity` `lineage`(up/down) `glossary` `tags` `owners` `domains` `ingest`(recipe run) `assertions` | `lineage-impact-analysis`（改表前算影响面） | A E F | REST/GraphQL + `datahub` CLI | P1 | M |
| **OpenMetadata** | `datus-openmetadata-plugin` | 同上量级 `services` `ingestion-pipelines` `data-quality` | `metadata-coverage-audit` | A E F | REST API | P2 | M |
| **Hive Metastore** | `datus-hms-plugin` | `databases` `tables` `partitions`(add/drop/repair) `stats` `locations` | `hms-partition-repair` | A D | Thrift（`pymetastore`）；老栈仍普遍 | P2 | S |
| **Nessie / Polaris** | 合并进 iceberg plugin（catalog 后端） | — | — | — | — | P2 | — |
| Apache Atlas / Amundsen | — | — | — | — | — | P3 | — |

---

## 4. 计算引擎 / 处理框架

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 接入 | 优先级 | 量 |
|---|---|---|---|---|---|:--:|:--:|
| **Apache Spark** | `datus-spark-plugin` | `submit`(Connect/Livy/spark-submit/k8s) `apps` `stages` `tasks` `sql` `executors` `kill` `conf` | `spark-slow-stage-triage`（倾斜/溢写/GC）、`spark-oom-triage` | B C D | History Server REST + Spark Connect / Livy；`datus k8s` 联动 | **P0** | M |
| **Apache Flink** | 已有 `datus-flink-plugin`（纯 skill） | 建议补 CLI：`jobs` `savepoint` `checkpoint` `metrics` `sql-gateway` | 已有 `flink-local-dev`、`flink-k8s-operator`；建议加 `flink-backpressure-triage`、`flink-state-recovery` | B C D | JobManager REST + SQL Gateway REST | **P0**（补 CLI） | M |
| **dbt Core + dbt Cloud** | `datus-dbt-plugin` | `run` `build` `test` `compile` `ls` `docs` `source-freshness` `snapshot` `manifest`(解析/血缘/选择器) `cloud jobs|runs|artifacts` | `dbt-model-authoring`、`dbt-test-failure-triage`、`dbt-selector-cookbook` | A B C E F | 本地 dbt CLI（子进程 + `manifest.json`/`run_results.json` 解析）+ dbt Cloud API v2/v3 | **P0（首推）** | L |
| **Ray** | `datus-ray-plugin` | `jobs`(submit/status/logs/stop) `cluster` `actors` `autoscaler` | `ray-data-pipeline-triage` | B C D | Ray Jobs REST | P2 | S |
| **Materialize / RisingWave** | `datus-streamdb-plugin`（二选一先做） | `sources` `sinks` `mv` `clusters` `lag` `subscribe` | `stream-mv-lag-triage` | A C D | pg 协议 + 内置 catalog | P3 | S |
| Dask / Beam(直连) / ksqlDB | — | — | — | — | — | P3 | — |

> dbt 是这份清单里我最推荐先做的一个：它同时是**建模工具、测试工具、血缘来源、文档来源**，
> 一个插件能同时喂到 A/B/C/E/F 五轴，而且 agent 写 SQL 之后天然要 `dbt build` + `dbt test` 收口。

---

## 5. 调度 / 编排

> **scheduler adapter 废弃后**，这一档的 plugin 必须承担原 adapter 的职责：不只是"运维已有 DAG"，
> 还要能**从一段 SQL / SparkSQL 生成并注册定时作业**（原 `submit_sql_job` / `submit_sparksql_job` +
> DAG 模板生成器）。逐条映射见 §13.2。

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 接入 | 优先级 | 量 |
|---|---|---|---|---|---|:--:|:--:|
| **Airflow** | 已有 `datus-airflow-plugin` `0.3.0` | 已有全套；**需补** `jobs submit-sql` `jobs submit-sparksql` `jobs update` `connections list`（承接 adapter，见 §13.2） | 已有 `airflow`、`airflow-setup`；建议加 `airflow-sql-job-authoring`（替代 adapter 的 DAG 模板）、`airflow-dag-failure-triage`、`airflow-backfill-planning` | 全 | 已有 client；DAG 模板生成器从 `datus-scheduler-airflow` 搬过来 | **P0（迁移）** | M |
| **MWAA / MWAA-serverless** | 已有 `datus-mwaa-plugin` | 同上补 `jobs submit-*`（复用 airflow 插件的模板逻辑，但**不得跨插件 import**——见 §13.4） | `mwaa-sql-job-authoring` | 全 | 已有 | **P0（迁移）** | S |
| **Dagster** | `datus-dagster-plugin` | `assets`(ls/materialize/status/checks) `runs`(launch/logs/terminate) `jobs`(含 `submit-sql`) `schedules` `sensors` `partitions`/`backfill` `code-locations` | `dagster-asset-backfill-planning`、`dagster-run-failure-triage`、`dagster-sql-asset-authoring` | A B C E | GraphQL API（Dagster+/OSS 同接口） | **P0**（原本要走 adapter 的路线现在只剩 plugin） | M |
| **Prefect** | `datus-prefect-plugin` | `deployments` `flow-runs` `flows` `work-pools` `blocks` `automations` `submit-sql` | `prefect-run-failure-triage` | A B C | REST API（Cloud/Server 同构） | P1 | M |
| **Argo Workflows** | 优先做成 `datus-k8s-plugin` 的 skill（`argo-workflows`）；需要 `submit/retry/resubmit` 才升级为独立插件 | `submit` `list` `get` `logs` `retry` `resubmit` `terminate` `cron` | `argo-workflow-failure-triage` | B C | Argo Server REST 或纯 CRD（走 `datus k8s`） | P1 | S |
| **Temporal** | `datus-temporal-plugin` | `workflow`(start/describe/terminate/reset) `taskqueue` `schedule` `activity` | `temporal-stuck-workflow-triage` | B C | `temporalio` SDK | P2 | M |
| **DolphinScheduler** | `datus-dolphinscheduler-plugin` | `projects` `process-definition` `process-instance` `task-instance` `resources` `tenants` | `ds-workflow-failure-triage` | A B C E | Open API（国内栈价值高） | P2 | M |
| **Databricks Jobs / Step Functions / Cloud Composer** | 合并进各自平台插件（databricks / aws / gcp） | — | — | — | — | 随平台 | — |
| Flyte / Kestra / Mage / Luigi / Azkaban | — | — | — | — | — | P3 | — |

---

## 6. 数据集成 / CDC / 消息

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 接入 | 优先级 | 量 |
|---|---|---|---|---|---|:--:|:--:|
| **Kafka（+ Schema Registry + Connect）** | `datus-kafka-plugin` | `topics` `groups`(describe/lag/reset-offsets) `consume` `produce` `acls` `brokers` `sr`(subjects/schemas/compatibility) `connect`(connectors/status/restart/pause) | `kafka-lag-triage`、`kafka-schema-compat-check`、`debezium-cdc-triage` | A B C D E | `confluent-kafka` AdminClient + SR REST + Connect REST + Confluent Cloud API | **P0** | L |
| **Airbyte** | `datus-airbyte-plugin` | `sources` `destinations` `connections` `sync`(trigger/cancel) `jobs` `logs` `schema refresh` `workspaces` | `airbyte-sync-failure-triage`、`airbyte-schema-drift-review` | A B C E | Airbyte API（OSS/Cloud 同构） | P1 | M |
| **Fivetran** | `datus-fivetran-plugin` | `connectors`(sync/resync/pause) `schemas` `destinations` `logs` `usage` `transformations` | `fivetran-mar-cost-review` | A B C F | REST API v1 | P2 | S |
| **Pulsar** | `datus-pulsar-plugin` | `tenants` `namespaces` `topics` `subscriptions` `backlog` `functions` | `pulsar-backlog-triage` | A C D | Admin REST | P3 | M |
| **SeaTunnel / NiFi / dlt / Meltano / Estuary** | — | — | — | — | — | P3 | — |
| **Kinesis / Firehose / MSK** | 扩 `datus-aws-plugins` | `streams` `shards` `consumers` `deliverystreams` | `kinesis-iterator-age-triage` | B C D | boto3 + aws-common | P2 | S |

---

## 7. BI / 可视化 / 语义层

> **bi adapter 废弃后**，BI plugin 不再是"补运维面"，而是**唯一的 BI 能力来源**：dashboard/chart/dataset
> 的增删改查、`get_chart_data`、serving target 解析全部要落在命令面上，并且要能支撑
> `gen_dashboard_agentic_node` 那条"自动搭 dashboard"链路。逐条映射见 §13.1。

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 接入 | 优先级 | 量 |
|---|---|---|---|---|---|:--:|:--:|
| **Apache Superset** | `datus-superset-plugin`（**全量承接 adapter**） | `dashboards`(ls/get/create/update/delete) `charts`(ls/get/create/update/delete/add-to-dashboard/data) `datasets`(ls/get/create/delete/sync/refresh) `databases` `serving-target` `sql-lab`(execute/results) `export` `import` `cache-warm` `roles` | `superset-dashboard-authoring`（替代 `dashboard_assembler`）、`superset-dashboard-migration`、`superset-broken-chart-triage` | A B C E F | REST API v1；从 `datus-bi-superset` 搬业务逻辑 | **P0（迁移）** | L |
| **Grafana** | `datus-grafana-plugin`（**全量承接 adapter**） | 同上 BI 命令面 + `datasources` `alert-rules` `silences` `annotations` `query`(promql/loki) | `grafana-dashboard-authoring`、`grafana-alert-noise-review` | A B C E | HTTP API；从 `datus-bi-grafana` 搬（含 datasource UID 解析逻辑） | **P0（迁移）** | M |
| **Metabase** | `datus-metabase-plugin` | `cards` `dashboards` `collections` `databases`(sync/rescan) `query` `permissions` `serialization`(export/import) | `metabase-permission-audit` | A B C E F | REST API | P1 | M |
| **Tableau** | `datus-tableau-plugin` | `sites` `workbooks` `datasources` `extracts refresh` `jobs` `permissions` `lineage`(Metadata GraphQL) `publish` | `tableau-extract-failure-triage`、`tableau-downstream-impact` | A B C E F | REST API + Metadata API；PAT 认证 | P1 | L |
| **Power BI** | `datus-powerbi-plugin` | `workspaces` `datasets`(refresh/refreshes/params) `reports` `dataflows` `capacities` `lineage` | `powerbi-refresh-failure-triage` | A B C F | REST API（需 Entra ID / service principal） | P2 | M |
| **Looker** | `datus-looker-plugin` | `looks` `dashboards` `explores` `queries run` `lookml validate` `content-validator` `scheduled-plans` | `lookml-validation-gate` | A B C F | Looker SDK (API 4.0) | P2 | M |
| **Cube / Lightdash / Preset / Sigma / Mode / Hex / Redash** | — | — | — | — | — | P3 | — |
| **MetricFlow / dbt Semantic Layer** | semantic adapter 已有 → 只补 skill 到 dbt plugin | — | `metric-definition-review` | — | — | P1（skill） | S |
| **QuickSight** | 已有 | — | — | — | — | 已有 | — |

---

## 8. 数据质量 / 血缘 / 可观测性

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 接入 | 优先级 | 量 |
|---|---|---|---|---|---|:--:|:--:|
| **Soda Core** | `datus-soda-plugin` | `scan` `checks`(author/validate) `test-connection` `results` `cloud`(datasets/incidents) | `soda-check-authoring`（从表结构反推检查）、`quality-gate-in-pipeline` | C E F | `soda-core` CLI/Python + Soda Cloud API | **P0**（质量至少选一） | M |
| **Great Expectations** | `datus-gx-plugin` | `suite`(ls/new/edit) `checkpoint run` `datasource` `docs build` `validation-results` | `gx-suite-authoring` | C E F | `great_expectations` Python API（1.x 变化大，注意版本钉） | P1 | M |
| **OpenLineage / Marquez** | `datus-lineage-plugin` | `events emit` `jobs` `datasets` `runs` `graph` `column-lineage` | `lineage-impact-analysis`（与 datahub 共用一份方法论） | A F | Marquez/OL HTTP API | P2 | S |
| **Prometheus / VictoriaMetrics** | `datus-prometheus-plugin` | `query` `query-range` `series` `labels` `targets` `rules` `alerts` `tsdb-status` | `promql-cookbook-for-data-workloads`（Flink/Spark/K8s 指标） | C | HTTP API v1；与 k8s/flink 插件强联动，实现极便宜 | **P1（性价比最高）** | S |
| **Elementary** | 归入 dbt plugin 的 skill | — | `elementary-anomaly-review` | — | — | P2 | S |
| Monte Carlo / Datafold / Deequ | — | — | — | — | — | P3 | — |

---

## 9. 云平台组（对称补齐 AWS 之外）

AWS 已有 9 个插件 + `datus-aws-common`。GCP / Azure / 阿里云按同样的分组模式做，**先建 common 库再铺插件**。

### 9.1 GCP —— `datus-gcp-plugins/` + `datus-gcp-common`（ADC/服务账号/impersonation、错误映射、输出渲染）

| 产品 | 插件 | 命令组 | 轴 | 优先级 | 量 |
|---|---|---|---|:--:|:--:|
| BigQuery | `datus-bigquery-plugin`（见 §2） | — | — | **P0** | M |
| GCS | `datus-gcs-plugin` | `ls` `stat` `cat` `cp` `rsync` `rm` `signurl` `lifecycle` | A E | **P0** | S |
| Dataproc | `datus-dataproc-plugin` | `clusters` `jobs`(submit/wait/logs) `batches`(serverless) `workflow-templates` | B C D | P1 | M |
| Dataflow | `datus-dataflow-plugin` | `jobs`(run/list/cancel/drain) `templates` `metrics` `snapshots` | B C D | P1 | M |
| Cloud Composer | `datus-composer-plugin` | `environments` `airflow-cli` `dags upload`（与 mwaa 插件同构） | B E | P2 | S |
| Pub/Sub | `datus-pubsub-plugin` | `topics` `subscriptions` `pull` `publish` `backlog` `snapshots` | A B C | P2 | S |
| Cloud Logging / Monitoring | `datus-gcp-logging-plugin` | `read`(LQL) `metrics` `alerts` `sinks` | C | P2 | S |
| IAM | `datus-gcp-iam-plugin` | `policy get/analyze` `test-permissions` `sa`（对标已有 iam 插件的 AccessDenied 诊断） | F | P2 | S |

### 9.2 Azure —— `datus-azure-plugins/` + `datus-azure-common`（Entra ID / DefaultAzureCredential）

| 产品 | 插件 | 命令组 | 轴 | 优先级 | 量 |
|---|---|---|---|:--:|:--:|
| ADLS Gen2 / Blob | `datus-adls-plugin` | `ls` `cat` `cp` `sync` `rm` `sas` `acl` | A E | P1 | S |
| Azure Data Factory | `datus-adf-plugin` | `pipelines`(run/cancel) `runs` `triggers` `datasets` `linked-services` `ir` | A B C | P1 | M |
| Synapse | `datus-synapse-plugin` | `sql-pools`(pause/resume/scale) `spark-pools` `jobs` `pipelines` | B C D | P2 | M |
| Microsoft Fabric | `datus-fabric-plugin` | `workspaces` `lakehouses` `warehouses` `pipelines` `notebooks` `capacities` | A B D | P2 | M |
| Event Hubs / Azure Monitor | `datus-eventhubs-plugin` / `datus-azmonitor-plugin` | `namespaces` `consumer-groups` `lag` / `logs`(KQL) `metrics` `alerts` | C | P3 | S |

### 9.3 国内云（`datus-clickzetta` adapter 已存在，说明国内栈是真实需求）

| 产品 | 插件 | 命令组 | 轴 | 优先级 | 量 |
|---|---|---|---|:--:|:--:|
| 阿里云 MaxCompute (ODPS) | `datus-maxcompute-plugin` | `instances` `jobs`(logview/status/kill) `tables`(lifecycle/partition) `tunnel`(upload/download) `resources` `quota` `cost` | A B C E F | P1 | L |
| 阿里云 DataWorks | `datus-dataworks-plugin` | `projects` `nodes` `instances`(rerun/freeze) `di-jobs` `data-quality` `lineage` | A B C F | P1 | L |
| 阿里云 Hologres | `datus-hologres-plugin` | `instances` `slow-query`(HoloWeb 视图) `resource-group` `vacuum` `foreign-table` | C D | P2 | M |
| 阿里云 OSS | 直接用已有 `datus-s3-plugin` 指向 OSS S3 兼容端点（先验证签名兼容），不通再单做 | — | — | P2 | S |
| 阿里云实时计算 Flink (VVP) | `datus-vvp-plugin` 或 flink plugin 的 skill | `deployments` `jobs` `savepoints` `sql` | B C D | P2 | M |
| 腾讯云 / 华为云 数仓 | — | — | — | P3 | — |

---

## 10. 非 SQL 数据库 / 检索 / 向量

这类的 plugin 价值主要在 **A/C/D**（元数据 + 诊断 + 运维），因为它们不走 db adapter 的 SQL 通道。

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 优先级 | 量 |
|---|---|---|---|---|:--:|:--:|
| **MongoDB** | `datus-mongo-plugin` | `dbs` `collections` `find` `aggregate` `indexes` `explain` `stats` `profile` `currentOp` `dump/restore` | `mongo-aggregation-authoring`、`mongo-slow-op-triage` | A C D E | P1 | M |
| **Elasticsearch / OpenSearch** | `datus-es-plugin` | `indices` `search` `sql` `mappings` `aliases` `ilm` `snapshot` `cluster health` `tasks` `nodes` | `es-index-health-triage`、`es-sql-cookbook` | A C D E | P1 | M |
| **PostgreSQL 运维** | **`datus-pg-ops-plugin`（skill 形态，无 CLI）** | 不需要 CLI：`pg_stat_activity`/`pg_locks`/`pg_stat_replication`、`EXPLAIN ANALYZE`、`VACUUM` 全是 SQL | `pg-slow-query-triage`、`pg-lock-contention-triage`、`pg-bloat-and-vacuum`、`pg-replication-lag-triage` | C D E F | P1 | **S**（原估 M） |
| **MySQL 运维** | **`datus-mysql-ops-plugin`（skill 形态，无 CLI）** | 同上：`SHOW PROCESSLIST` / `ENGINE INNODB STATUS` / `SLAVE STATUS`、`performance_schema` 全是 SQL | `mysql-replication-lag-triage`、`mysql-lock-wait-triage` | C D F | P2 | **S**（原估 M） |
| **Redis** | `datus-redis-plugin` | `info` `scan` `get` `ttl` `memory` `slowlog` `clients` `cluster` | `redis-memory-pressure-triage` | A C D | P3 | S |
| **Cassandra / ScyllaDB** | `datus-cassandra-plugin` | `keyspaces` `tables` `nodetool`(status/compactionstats/tpstats) `repair` `cql` | `cassandra-compaction-triage` | A C D | P3 | M |
| **DynamoDB** | 扩 `datus-aws-plugins` | `tables` `query` `scan` `capacity` `gsi` `export-to-s3` | `dynamodb-hot-partition-triage` | A C E | P2 | S |
| **向量库**（pgvector / Milvus / Qdrant / Weaviate / Pinecone） | `datus-vector-plugin`（**先只做一个**，建议 Milvus 或 Qdrant） | `collections` `index` `search` `upsert` `stats` `compact` | `vector-index-tuning`、`rag-corpus-freshness-audit` | A C D E | P2 | M |
| Neo4j / InfluxDB / TimescaleDB / HBase / Druid / Pinot | — | — | — | — | P3 | — |

---

## 11. 产品分析 / 实验 / ML

| 产品 | 插件 | 命令组 | 轴 | 优先级 | 量 |
|---|---|---|---|:--:|:--:|
| Statsig | 已有 `datus-statsig-plugin` | — | — | 已有 | — |
| **Amplitude** | `datus-amplitude-plugin` | `events` `charts` `cohorts` `export`(raw) `taxonomy` `user-lookup` | A E F | P2 | M |
| **Mixpanel** | `datus-mixpanel-plugin` | `events` `funnels` `retention` `export` `schemas` `lexicon` | A E | P2 | S |
| **PostHog** | `datus-posthog-plugin` | `events` `insights` `cohorts` `feature-flags` `hogql query` | A E F | P2 | M |
| **GA4 / Segment / Snowplow** | `datus-ga4-plugin` / … | `runReport` `metadata` / `sources` `destinations` `tracking-plan` | A E | P3 | S |
| **MLflow** | `datus-mlflow-plugin` | `experiments` `runs`(compare/artifacts) `models` `registry`(stage transition) `deployments` | A B C F | P2 | M |
| **Feast** | `datus-feast-plugin` | `feature-views` `entities` `materialize` `get-online-features` `plan/apply` | A B E | P2 | S |
| Tecton / W&B / Kubeflow | — | — | — | P3 | — |
| SageMaker / Vertex AI | 归入 aws / gcp 组 | — | — | P3 | — |

---

## 12. 治理 / 权限 / 成本

| 产品 | 插件 | 命令组 | 专项 skill | 轴 | 优先级 | 量 |
|---|---|---|---|---|:--:|:--:|
| **AWS 成本** | 扩 `datus-aws-plugins`：`datus-cost-plugin` | `cost-explorer`(get-cost-and-usage) `budgets` `anomalies` `ri/sp-utilization` | `data-stack-cost-attribution`（把仓/湖/计算成本归因到 pipeline） | F | P1 | S |
| **Apache Ranger** | `datus-ranger-plugin` | `services` `policies`(ls/create/test) `roles` `audit` | `ranger-policy-audit` | F | P2 | M |
| **OPA** | 用 skill 挂到 k8s plugin | — | `opa-policy-authoring` | F | P3 | S |
| Immuta / Privacera / Collibra | — | — | — | — | P3 | — |

---

## 13. 迁移：把 bi / scheduler adapter 的能力搬进 plugin

**这一节不是候选，是已决定的事项**，但它有一个必须先拍板的架构决策（§13.4 决策 1）。

现状（`Datus-agent` 侧实测）：两类 adapter 不只是抽象层，它们被包成 **31 个 agent function tool**
直接给 LLM 调用。迁移的真实工作量在于**契约形态从 in-process Python 变成 CLI**，不是搬代码。

### 13.1 BI：`datus/tools/func_tool/bi_tools.py` 的 18 个 tool → plugin 命令

| 现 function tool | plugin 命令（以 superset 为例） | 权限 |
|---|---|:--:|
| `list_dashboards` | `datus superset dashboards ls --search --limit --offset` | allow |
| `get_dashboard` | `datus superset dashboards get <id>` | allow |
| `create_dashboard` | `datus superset dashboards create --title --description` | ask |
| `update_dashboard` | `datus superset dashboards update <id> --title --description` | ask |
| `delete_dashboard` | `datus superset dashboards delete <id>` | ask |
| `list_charts` | `datus superset charts ls --dashboard-id` | allow |
| `get_chart` | `datus superset charts get <id>` | allow |
| `get_chart_data` | `datus superset charts data <id> --limit` | allow |
| `create_chart` | `datus superset charts create --spec-file/-`（ChartSpec JSON） | ask |
| `update_chart` | `datus superset charts update <id> --spec-file/-` | ask |
| `add_chart_to_dashboard` | `datus superset charts add-to-dashboard <chart-id> <dashboard-id>` | ask |
| `delete_chart` | `datus superset charts delete <id>` | ask |
| `list_datasets` | `datus superset datasets ls --dashboard-id` | allow |
| `get_dataset` | `datus superset datasets get <id>` | allow |
| `create_dataset` | `datus superset datasets create --name --database-id --sql` | ask |
| `delete_dataset` | `datus superset datasets delete <id>` | ask |
| `list_bi_databases` | `datus superset databases ls` | allow |
| `get_bi_serving_target` | `datus superset serving-target` | allow |

### 13.2 Scheduler：`datus/tools/func_tool/scheduler_tools.py` 的 13 个 tool → plugin 命令

| 现 function tool | plugin 命令（airflow 插件） | 现状 | 权限 |
|---|---|---|:--:|
| `submit_sql_job` | `datus airflow jobs submit-sql --sql-file --schedule --conn-id --job-id` | **缺，要新建**（含 DAG 模板生成） | ask |
| `submit_sparksql_job` | `datus airflow jobs submit-sparksql --sql-file --schedule --spark-conf` | **缺，要新建** | ask |
| `update_job` | `datus airflow jobs update <job-id> --sql-file --schedule` | **缺** | ask |
| `delete_job` | `datus airflow jobs delete <job-id>`（含清理 DAG 文件） | 部分（`dags undeploy` 可复用） | ask |
| `trigger_scheduler_job` | `datus airflow dags trigger` | ✅ 已有 | ask |
| `get_scheduler_job` | `datus airflow dags details` | ✅ 已有 | allow |
| `list_scheduler_jobs` | `datus airflow dags list` | ✅ 已有 | allow |
| `pause_job` / `resume_job` | `datus airflow dags pause` / `unpause` | ✅ 已有 | ask |
| `list_job_runs` | `datus airflow dags list-runs` | ✅ 已有 | allow |
| `get_run_log` | `datus airflow tasks logs` | ✅ 已有 | allow |
| `list_scheduler_connections` | `datus airflow connections list` | ✅ 已有 | allow |

> 结论：**调度侧 airflow 插件已覆盖 8/13**，缺口集中在"从 SQL 生成作业"这一组（4 个）+ DAG 模板生成器。
> BI 侧则是 18 个几乎全新——`datus-superset-plugin` / `datus-grafana-plugin` 因此升到 P0。

### 13.3 core 侧要一起改的依赖点

| 文件 | 依赖 | 迁移动作 |
|---|---|---|
| `datus/tools/func_tool/bi_tools.py` | `datus_bi_core.adapter_registry`、`AuthParam`、`ChartSpec/DashboardSpec/DatasetSpec` | 改为调 plugin（见决策 1） |
| `datus/tools/func_tool/scheduler_tools.py` | `SchedulerAdapterRegistry`、`SchedulerJobPayload` | 同上 |
| `datus/tools/bi_tools/dashboard_assembler.py` | `BIAdapterBase` 强类型对象 | 改为 spec JSON + plugin 命令；或整体下沉成 plugin skill |
| `datus/agent/node/gen_dashboard_agentic_node.py` | in-process adapter 实例 | 决策 1 直接决定这里怎么写 |
| `datus/cli/bootstrap_bi_picker.py` | `datus_bi_core` 引导选择器 | 由 `<name>-setup` skill 承接 |
| `datus/cli/service_adapter_installer.py` | entry-point 组 `datus.bi_adapters` / `datus.schedulers` | 退役，统一走 `datus.plugins` |
| `datus/cli/service_client.py` | `_probe_bi_adapter` / `_probe_scheduler_adapter` | 改为探测 plugin 是否安装 |
| `datus/configuration/agent_config.py` | `agent.bi_platforms.*` / `agent.schedulers.*` | 配置迁移到 `agent.plugins.<name>.*`，需兼容期 |

### 13.4 必须先定的技术缺口

**决策 1（阻塞项）—— plugin 怎么把能力暴露给 agent？**
adapter 能给 agent 注册 function tool；**plugin 契约目前不能**——它只有 `cli: main(argv, profile) -> int`，
外加 `tool_transformers`（只能拦截已有 tool，不能新增）。两条路：

| 选项 | 做法 | 代价 |
|---|---|---|
| **A. agent 走 bash 调 CLI** | agent 用 Bash 执行 `datus superset dashboards ls -o json`，靠 manifest `commands` + `permissions` 描述能力 | 无需改契约；但 `gen_dashboard_agentic_node` 要重写成命令编排，且失去强类型与 in-process 性能 |
| **B. 扩展 plugin 契约，新增 `tools:` 键** | manifest 声明 `tools: datus_superset_plugin.tools:register`，plugin 直接注册 function tool | 要改 Datus core 的 plugin 加载器；但迁移几乎零能力损失，agent 体验不变 |

我的建议：**B**（或先 A 保证可用、同期做 B）。理由：`get_chart_data` 返回结果集、`create_chart` 吃
ChartSpec 结构体，走 CLI stdout 会遇到体积截断和 JSON 逃逸问题；且 `gen_dashboard_agentic_node`
是多步交互链路，subprocess 化会显著变慢。**这条决定后面所有 BI/调度插件怎么写，请先拍板。**

**其余缺口**

2. **强类型模型**：`ChartSpec` / `DashboardSpec` / `DatasetSpec` / `SchedulerJobPayload` 在 CLI 边界要变成
   JSON schema（建议 `--spec-file` 接文件或 `-` 读 stdin，避免超长命令行）。
3. **能力发现**：现在靠 mixin + `_supports_chart_data()` 运行时探测（Grafana 没有 chart data 就降级）。
   CLI 边界上应改为 manifest `commands` 声明式表达——各插件命令面允许不一致，agent 按 manifest 决定能做什么。
4. **代码复用与契约冲突**：DAG 模板生成器（SQL / PySpark / SparkSQL）airflow、mwaa、dagster 都要用，
   但契约明令**禁止跨插件 import**。要么抽 `datus-jobgen-common` 库（推荐，同 `datus-aws-common` 模式），
   要么各插件各存一份。
5. **权限语义变化**：adapter 走 `permission_category: scheduler_tools`（func tool 权限）；plugin 走
   manifest 的 bash-pattern 权限。`delete_dashboard`、`submit_sql_job` 这类必须在 `normal` 下落到 `ask`。
6. **配置兼容期**：`agent.bi_platforms` / `agent.schedulers` → `agent.plugins.<name>`，需要一个双读兼容窗口 +
   迁移提示，否则现有用户配置直接失效。

---

## 14. 建议先做的公共基础

在铺 20+ 个 REST 插件之前，这两件事能省掉大量重复代码——**但只在真的要做 ≥3 个同族插件时才建（符合仓库"多个插件需要才抽库"的约定）**。

| 项 | 说明 | 何时做 |
|---|---|---|
| `datus-rest-common` | REST 插件通用件：HTTP 客户端（重试/退避/分页/超时）、token 刷新、错误→退出码映射（1/2/3/8）、`-o json\|table\|plain\|yaml` 渲染、确认提示 `-y`。现在 airflow/statsig/k8s 各写了一份 `client.py`+`output.py`+`errors.py`。 | 决定做 ≥3 个 REST 插件时，先抽 |
| `datus-gcp-common` / `datus-azure-common` | 对标 `datus-aws-common`：凭证链（ADC / DefaultAzureCredential）、项目/订阅解析、错误映射、渲染 | 启动 GCP / Azure 组时 |
| `datus-jobgen-common` | 调度作业模板生成（SQL / PySpark / SparkSQL → DAG / asset / flow 源码），从 `datus-scheduler-airflow` 的 `dag_template` 抽出。airflow / mwaa / dagster / prefect 都要用，而契约禁止跨插件 import | **scheduler adapter 迁移时（P0）** |
| `datus-bi-spec-common` | BI spec 模型（ChartSpec / DashboardSpec / DatasetSpec）的 JSON schema + 校验，从 `datus-bi-core` 抽出 | **bi adapter 迁移时**，且仅当决策 1 选 A（CLI 边界需要 schema） |
| 现有插件补专项 skill | airflow 缺 `airflow-dag-failure-triage`；k8s 可加 `argo-workflows`；flink 缺 CLI + `flink-backpressure-triage` | 随时，成本最低 |

---

## 15. 推荐实施波次

### Wave 0 —— adapter 废弃迁移（**优先于一切新插件**，见 §13）

能力回退的风险高于新增能力的收益，所以这一波先做。

| # | 事项 | 说明 |
|---|---|---|
| 0 | **拍板决策 1**（plugin 如何向 agent 暴露能力：A 走 CLI / B 扩契约加 `tools:`） | 阻塞后续全部 BI/调度插件的写法 |
| 1 | `datus-superset-plugin`（全量承接 bi adapter，18 tool） | BI 侧几乎全新，L 量级 |
| 2 | `datus-grafana-plugin`（全量承接 bi adapter） | 含 datasource UID 解析等既有逻辑 |
| 3 | `datus-airflow-plugin` 补 4 个作业生成命令 + `datus-jobgen-common` | 调度侧已覆盖 8/13，缺口小 |
| 4 | `datus-mwaa-plugin` 同步补齐 | 复用 jobgen-common |
| 5 | core 侧依赖点改造 + 配置兼容期（§13.3） | 在 Datus-agent 仓库进行 |

### Wave 1 —— 覆盖"现代数据栈"主干（建议 6 个）

| # | 插件 | 为什么在第一波 |
|---|---|---|
| 1 | **dbt**（`datus-dbt-plugin`） | agent 写完 SQL 之后的收口工具；一插件覆盖建模/测试/血缘/文档五轴；无它则 agent 产出无法进 CI |
| 2 | **Snowflake**（skill 形态） | 装机量最大的云仓；成本与慢查询是最高频的人工痛点。**因全部能力可 SQL 化，量级从 L 降到 S**——性价比最高的一条，见 §0.1 |
| 3 | **Databricks** | 覆盖面最广（Spark + UC + Jobs + DLT + SQL），一次拿下四个方向 |
| 4 | **BigQuery + GCS** | 三大仓最后一块；`--dry-run` 预估成本是 agent 独有价值；同时开出 GCP 组 |
| 5 | **Iceberg** | 湖仓表维护是纯手工活，最适合托管给 agent；与已有 glue/s3/flink 插件天然串起来 |
| 6 | **Kafka**（含 SR + Connect） | 流式侧唯一必需项；lag/schema 兼容性诊断价值极高 |

### Wave 2 —— 补齐调度、质量、诊断

**Dagster**（BI 已在 Wave 0 处理）· Soda · Spark · Prometheus · Trino（含先补 db adapter）· flink CLI 补齐 · AWS 成本插件

### Wave 3 —— 企业与国内栈

Tableau · Power BI · Airbyte · DataHub · ClickHouse · StarRocks · MaxCompute · DataWorks · Redshift · ADF/ADLS · MongoDB · Elasticsearch · pg-ops

### Wave 4 —— 长尾（按客户需求拉动，不主动做）

Prefect · Temporal · DolphinScheduler · Metabase · Looker · Fivetran · MLflow · Feast · 向量库 · Ranger · 其余 P3

---

## 16. 勾选表（请在这里做决定）

**Wave 0 — adapter 迁移（已决定要做，需确认范围与决策 1）**

- [ ] 决策 1 选 **A**（agent 走 bash 调 CLI，不改 plugin 契约）
- [ ] 决策 1 选 **B**（扩展 plugin 契约，新增 `tools:` 注册 function tool）— 推荐
- [x] `datus-superset-plugin`（独立实现；覆盖 Superset v1 数据面、guarded API 与看板 SQL context 导出）
- [x] `datus-grafana-plugin`（独立实现；覆盖 Grafana 10–13、12+ `/apis` 优先、全查询语言 context 导出）
- [ ] `datus-airflow-plugin` 补 `jobs submit-sql/submit-sparksql/update/delete`
- [ ] `datus-mwaa-plugin` 同步补齐
- [ ] 抽 `datus-jobgen-common`
- [ ] 抽 `datus-bi-spec-common`（仅决策 1 选 A 时需要）
- [ ] core 侧改造 + 配置兼容期（在 Datus-agent 仓库）

**Wave 1 候选**

- [ ] `datus-dbt-plugin`
- [ ] `datus-snowflake-ops-plugin`（skill 形态，无 CLI —— 零配置重复）
- [ ] `datus-databricks-plugin`
- [ ] `datus-bigquery-plugin` + [ ] `datus-gcs-plugin`（+ `datus-gcp-common`）
- [ ] `datus-iceberg-plugin`
- [ ] `datus-kafka-plugin`

**Wave 2 候选**

- [ ] `datus-dagster-plugin`
- [ ] `datus-soda-plugin`
- [ ] `datus-spark-plugin`
- [ ] `datus-prometheus-plugin`
- [ ] `datus-trino-plugin`（+ 先补 `datus-trino` db adapter）
- [ ] `datus-flink-plugin` 补 CLI（jobs/savepoint/checkpoint/metrics）
- [ ] `datus-cost-plugin`（AWS 成本，进 aws-plugins 组）

**基础设施**

- [ ] 抽 `datus-rest-common`
- [ ] 建 `datus-gcp-common`
- [ ] 建 `datus-azure-common`
- [ ] 给 airflow / k8s / statsig 补专项 triage skill

**其他方向（按需勾）**：Wave 3 / Wave 4 见 §15，或直接在上面各章的表里标记。

---

## 17. 每个候选立项时要回答的问题

从 `/datus-plugin-development` 的 design draft 阶段照抄，勾选后逐个过：

1. **是否与 db adapter 重复？** 命令面里若超过一半是"跑 SQL/取元数据"，改做 db adapter。
   （BI / 调度类不适用——那两类 adapter 已废弃，plugin 就是全部。）
1b. **能不能不写 CLI？** 逐条过命令面：能用 SQL 表达的划掉。若划完剩不到 3 条，做 **skill 形态**
   （无 CLI、无 `config_schema`），并用 `tool_transformers` 给写操作加门禁——零配置重复。见 §0.1。
1c. **BI / 调度插件专项**：是否覆盖了 §13.1 / §13.2 映射表里的每一条？缺一条就是对现有用户的能力回退。
2. **能力轴够不够 3 个？** 不够就降级成 skill 或合并进邻近插件。
3. **认证怎么配？** 所有 secret 必须 `${ENV_VAR}`，写进 `config_schema` 且在 README 标注 secret 字段。
4. **权限姿态？** 读操作 `allow`；起跑作业/改配置/发代码/删除/碰 secret 在 `normal` 下必须 `ask`；`auto` 下只放开可逆读写。
5. **作用域限制？** 对标 k8s 的 `allowed_namespaces`、airflow 的 DAG scoping——高危插件要有 allowlist（如 Snowflake 的 `allowed_databases`、Databricks 的 `allowed_catalogs`）。
6. **专项 skill 是什么？** 至少一个"诊断剧本"型 skill，否则这个插件只是 CLI 包装，agent 拿不到方法论。
7. **测试怎么写？** `tests/test_plugin_contract.py` 必须有；外部 API 用 fixture 打桩，逐插件跑 pytest（根目录跑会有同名测试模块冲突）。
8. **端到端可测性属于哪一档？** 见 §18——本地可测（L/M/E）的候选优先，纯云档（R/C）的要提前接受"只能录制回放"。

---

## 18. 测试可行性：哪些能本地跑，哪些必须上云

> 配套方案见 [`plugin-testing-strategy.md`](plugin-testing-strategy.md)（分层与 CI）。本节只回答三件事：
> **谁能本地化**、**隔离怎么做**、**能串出哪些复杂场景**。

### 18.1 五个可测性档位

| 档 | 含义 | 在 CI 里的位置 |
|:--:|---|---|
| **L** | 官方 docker 镜像或纯本地进程，能完整本地化 | PR 必跑 |
| **M** | 需要 k8s（operator / CRD 类），本地 minikube / kind 足够 | PR 必跑（镜像缓存后） |
| **E** | 有模拟器，但**覆盖度不完整**，边界行为与真服务有差异 | PR 跑主干路径 + nightly 上云校准 |
| **R** | 无法本地化，只能**录制回放**（SaaS 专有 API） | PR 回放 + nightly 重录 |
| **C** | 必须真云账号（托管服务，连模拟器都没有） | 仅 nightly / 发版前 |

**E 档的风险要写进用例注释**：模拟器通过 ≠ 真服务通过。分页、错误码、一致性语义是三个高发差异点。

### 18.2 逐候选判定

**能完整本地化（L / M）—— 大多数 OSS，优先做这些的 E2E**

| 候选 | 档 | 本地起法 |
|---|:--:|---|
| **dbt** | **L** | `dbt-core` + `dbt-duckdb`/`dbt-postgres`，**零服务**，最容易 |
| ClickHouse | L | 官方 `clickhouse/clickhouse-server` |
| StarRocks / Doris | L | `starrocks/allin1-ubuntu` 单容器 |
| Trino | L | 官方 `trinodb/trino` |
| **Iceberg** | L | `apache/iceberg-rest-fixture`（或 `tabulario/iceberg-rest`）+ minio |
| Delta Lake | L | 纯本地文件系统 + `deltalake` |
| Unity Catalog | L | UC OSS docker（仅 OSS 子集，Databricks 托管特性不覆盖） |
| Hive Metastore | L | hive-metastore 镜像 + postgres |
| Nessie | L | 官方 docker |
| DataHub / OpenMetadata | L | 官方 quickstart compose（较重，镜像需缓存） |
| **Spark** | L + M | `apache/spark` 含 History Server；Spark-on-K8s 走 minikube |
| **Flink** | L + M | flink docker；Operator 走 minikube（**已有先例**：`flink-k8s-operator` skill） |
| Ray | L | `rayproject/ray` |
| **Airflow** | L | 官方 compose（postgres + scheduler + webserver，最重的一个） |
| Dagster | L | `dagster dev` 本地进程，比 airflow 轻得多 |
| Prefect | L | prefect server docker |
| Argo Workflows | M | minikube + argo install |
| Temporal | L | `temporal server start-dev` 单进程 |
| DolphinScheduler | L | 官方 compose |
| **Kafka + SR + Connect** | L | redpanda（更轻，Kafka 协议兼容）或 `cp-kafka` 三件套 |
| Airbyte | L | `abctl` / compose（很重） |
| Pulsar | L | `apachepulsar/pulsar` standalone |
| **Superset** | L | 官方 compose |
| **Grafana** | L | 官方 docker，**极轻** |
| Metabase | L | 官方 docker |
| Soda / GX | L | 纯 Python 库 + postgres/duckdb |
| OpenLineage / Marquez | L | 官方 compose |
| **Prometheus** | L | 官方 docker，**极轻** |
| MongoDB / ES-OpenSearch / Redis / Cassandra | L | 均有官方镜像 |
| **PostgreSQL / MySQL 运维** | L | 官方镜像；**skill 形态**，测的是 SQL + transformer，最容易 |
| 向量库（Milvus / Qdrant / Weaviate） | L | 均有官方镜像；Pinecone 是 **R** |
| PostHog | L | 自托管 compose（Amplitude / Mixpanel 是 **R**） |
| MLflow / Feast | L | mlflow server docker;feast + redis/postgres |
| Ranger / OPA | L | OPA 极轻；Ranger 较重 |
| **k8s**（已有插件） | M | minikube / kind |

**有模拟器但不完整（E）—— 主干路径可本地，边界必须上云校准**

| 候选 | 模拟器 | 不覆盖什么（需上云校准） |
|---|---|---|
| **S3** | **minio**（比 localstack 更轻更真） | 部分 ACL / 存储类 / 生命周期语义 |
| AWS 其余（glue / emr / ecs / iam / cloudwatch / kinesis / dynamodb） | localstack（社区版覆盖度不一，**需逐服务验证**）；DynamoDB 有官方 `dynamodb-local` | 社区版对 Glue / EMR 支持有限；IAM 策略仿真结果不可当真 |
| GCS | `fake-gcs-server`（第三方） | 签名 URL / IAM 条件 |
| BigQuery | `bigquery-emulator`（第三方，SQL 与 jobs API 部分支持） | **`INFORMATION_SCHEMA` 成本视图、`--dry-run` 字节预估、reservations 都不可信** ← 恰好是插件的核心价值，必须上云测 |
| Pub/Sub | **官方 gcloud emulator**（质量最好的一个） | 无重大缺口 |
| Azure ADLS / Blob | **Azurite**（官方） | 层级命名空间部分特性 |

**必须云环境（R / C）—— 立项时就要接受"E2E 只能录制回放"**

| 候选 | 档 | 说明 |
|---|:--:|---|
| **Snowflake** | R | 无本地版。LocalStack 有 Snowflake 模拟器但属商业功能，**需单独验证可行性与授权**。好消息：已定为 **skill 形态**，测试对象是 SQL 文本 + `tool_transformers` 门禁 → **transformer 可纯单元测试，无需任何环境**；只有 `ACCOUNT_USAGE` 查询结果需要真账号 |
| **Databricks** | R | 无本地版；仅 Unity Catalog OSS 部分可本地 |
| Redshift | R | 无本地版；postgres 近似但 `STL_*`/`SYS_*` 视图不存在，而那正是诊断能力所在 |
| Athena / Glue / EMR / EMR-Serverless / ECS / QuickSight / MWAA | C | localstack Pro 才可能覆盖；MWAA 底层是 Airflow REST → **命令逻辑可用本地 airflow 近似**，仅 token 铸造需回放 |
| Dataproc / Dataflow / Composer | C | 无模拟器 |
| ADF / Synapse / Fabric / Event Hubs | C | 无模拟器 |
| **阿里云全部**（MaxCompute / DataWorks / Hologres / VVP） | C | 无模拟器；OSS 可用 minio 近似 S3 兼容面 |
| Tableau / Power BI / Looker | R/C | SaaS 或需 license |
| Fivetran / Statsig / Amplitude / Mixpanel / GA4 | R | 纯 SaaS |

**一句话结论**：Wave 1 的六个里，**dbt / Iceberg / Kafka 完全可本地**（L），**BigQuery 部分可本地**（E，但成本预估必须上云），
**Snowflake / Databricks 只能回放**（R）——但 Snowflake 因为是 skill 形态，反而是最省测试环境的一个。

### 18.3 完全独立的测试 context：五个隔离维度

"独立 context"必须同时隔离这五项，缺一项就会串味或污染真实环境：

| 维度 | 做法 | 不做会怎样 |
|---|---|---|
| **配置** | 临时生成 `agent.e2e.yml`，用 `datus --config <path>` 指定 | 读到你真实的 profile，可能连上生产 |
| **datus home** | 在该配置里设 `agent.home: <tmpdir>`。**注意：`resolve_home()` 只认 `agent.home` 配置，不读环境变量**，所以必须走配置文件 | 会写 `~/.datus` 的 sessions / logs / data |
| **服务栈** | `docker compose -p e2e-${RUN_ID}` —— project name 隔离网络与卷 | 并行 job 抢同一组端口和卷 |
| **k8s** | 独立 namespace `e2e-${RUN_ID}`，且 profile 的 `allowed_namespaces` 只列它 | 污染共享集群 |
| **资源命名** | 所有远端资源带 `${RUN_ID}` 前缀 | 并行互踩，清理时误删他人资源 |

**必须有的环境守卫（血的教训预防）**：本机当前 `kubectl` context 指向的是**共享 EKS dev 集群**
（`datus-dev-eks-cluster`，里面跑着真 Airflow、Iceberg REST、Flink Operator）。它看起来"现成可用",
但 E2E 跑上去会影响他人。所以 runner 启动时**必须校验 context 在白名单内**（`minikube` / `kind-*`），
不匹配直接拒绝运行——而不是提示一下就继续。

### 18.4 测试流程（六步，全自动）

```
1. GUARD      校验 kube context / 云凭证指向沙箱；不合规立即退出（非 skip，非警告）
2. PROVISION  docker compose -p e2e-${RUN_ID} up --wait   (+ kubectl create ns e2e-${RUN_ID})
3. CONFIGURE  渲染 agent.e2e.yml：endpoint 指向本地服务，agent.home 指向 tmpdir
4. SEED       灌入确定性夹具（parquet / DAG 文件 / 建表 DDL）——固定内容，可断言精确行数
5. RUN        runner 逐 step 执行 `datus <plugin> ... -o json`：
                run    → 断言 exit code / JSONPath
                wait   → 轮询 until，配 timeout + fail_on 快速失败（禁止 sleep）
6. TEARDOWN   always-run：反序清理远端资源 → compose down -v → 删 ns → 删 tmpdir
```

第 1 步和第 6 步是这套流程的全部安全性所在；第 5 步是全部价值所在。

### 18.5 复杂场景库（多插件配合，按"接缝密度"排序）

跨插件 bug 集中在**一个插件的输出喂给下一个插件的输入**这个接缝上——单插件测试永远发现不了。
下表每个场景都标注了它验证的接缝，以及能否本地跑。

| # | 场景 | 串联的 plugin | 环境 | 验证的接缝（真正的价值） |
|:--:|---|---|:--:|---|
| 1 | **流式入湖闭环**：产消息 → Flink 作业写入 → Iceberg 快照 → Trino 查询验证行数 | kafka → flink → iceberg → trino | **全 L** | schema 兼容性、checkpoint 与 Iceberg snapshot 的对应关系、写入可见性延迟。**最高价值的复杂场景** |
| 2 | **批处理主干闭环**：上传 DAG → 部署 → 触发 → dbt build → 质量门禁 → BI 数据集刷新 | s3(minio) → airflow → dbt → soda → superset | **全 L** | DAG 发现延迟、dbt manifest → 仓库对象、质量失败能否阻断发布、BI dataset id 关联 |
| 3 | **表维护闭环**：制造小文件 → compact / expire-snapshots → 断言文件数与快照数变化 | iceberg (+ spark) | **全 L** | 维护操作的幂等性、快照过期后 time-travel 的边界行为 |
| 4 | **CDC 全链**：改 Postgres 行 → Connect 捕获 → Kafka → Flink → Iceberg 对账 | pg → kafka(connect) → flink → iceberg | **全 L** | 主键/删除语义（tombstone）、至少一次 vs 精确一次 |
| 5 | **K8s 作业诊断**：提交 Spark 作业 → pod 状态 → History Server 指标 → Prometheus 关联 | k8s → spark → prometheus | **M + L** | pod 名 ↔ Spark app id ↔ 指标 label 的三方对齐（最容易断的接缝） |
| 6 | **血缘影响面分析**：dbt run → manifest → 元数据摄取 → 查下游 → 改表前算影响 | dbt → datahub | **全 L** | manifest 的 unique_id 与元数据平台 URN 的映射 |
| 7 | **BI 迁移往返**：实例 A export → 实例 B import → 比对 spec 一致性 | superset ×2 实例 | **全 L** | **§13 迁移后 spec JSON 的往返保真度**——迁移验收必备 |
| 8 | **故障注入诊断（E2E-B，agent 驱动）**：故意让 DAG 失败，给 agent 自然语言任务"这张表昨天为什么没更新" | airflow + k8s（agent 自选） | **L + M** | agent 是否自己选对了插件与命令序列；`permissions` 是否真的拦住危险操作 |
| 9 | **跨云搬运**：minio → fake-gcs-server 对拷校验 | s3 → gcs | **E** | 两套凭证并存、分片上传、校验和一致 |
| 10 | **云仓成本回归（仅 nightly）**：dry-run 预估 vs 实际扫描字节数偏差 | bigquery（+ dbt） | **C** | 成本预估准确性——**模拟器不可信，只能上云** |

**建议起步顺序**：先做**场景 2 的前两步**（s3→airflow，验证 runner 本身），再做**场景 1**（全本地、接缝最密、最能暴露设计问题），
其余按 Wave 顺序跟随插件落地。场景 8（agent 级）等 Wave 1 插件齐了再做。

**镜像成本提示**：场景 1 需要 kafka+flink+iceberg+trino+minio 五个服务，首次拉镜像数分钟——
CI 必须做镜像层缓存，否则 PR 时间不可接受。DataHub / Airbyte / Superset 是另外三个"重镜像"。
