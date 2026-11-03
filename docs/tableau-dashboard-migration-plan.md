# Tableau 资产同步与 DOSI 驱动的 SaaS Dashboard 重建计划

更新日期：2026-11-03。状态：首版实现完成，离线验证通过；待真实环境验收。

本计划完整替代聊天中最初以 Tableau 发布、刷新运维为主的方案。用户已授权开始实现；ETL 范围为同步、解析和生成加工产物。

## 1. 目标与范围

主流程：选择部分或所有 Tableau dashboard → 同步本地资产与依赖 → 解析 SQL、图表、dataset 和 ETL → 映射现有 DOSI 指标 → 生成并验证 datus-saas dashboard。

已确认的产品决策：

- 来源为 Tableau Server；版本尚未明确，首版以 2023.3 及以上作为设计基线，实际版本通过检测确认。
- 同时支持 PAT 和用户名密码认证；Metadata API 已启用，作为元数据与血缘的主要来源。
- 同步 dashboard/chart 元数据、dataset 配置和加工依赖；原生定义文件解析纳入核心范围。
- 重建以 Datus 自身 DOSI 指标体系为权威，目标为 datus-saas dashboard。
- ETL 负责**同步、解析和生成加工产物**；本次不部署、调度或运行 ETL，不创建或修改仓库物理表。
- 后续来源包括 Looker、Power BI、FineBI、观远 BI；本次实现 Tableau，保留可供其他来源使用的资产契约。

成功标准：每个选定 dashboard 都有可审计的本地资产包和复刻结果；可完成的图表绑定真实 DOSI 指标，在 SaaS 中支持相应查询与筛选；未完成项明确说明缺失依赖或不支持语义，不能以静态截图、复制原 SQL 或近似计算冒充完成。

## 2. 职责与接口

| 层 | 职责 | 实现位置与约束 |
|---|---|---|
| Tableau 插件 | 发现、选择、下载、解析与同步来源资产 | 本仓库新增独立 `datus-tableau-plugin`，不 import Datus 或其他插件 |
| 通用迁移契约 | 描述资产、依赖、表达式、SQL 来源和解析覆盖情况 | 首版以版本化 JSON Schema 定义，保留平台扩展字段，不引入新的 BI adapter 注册机制 |
| Datus 工作流 | 解释图表意图、匹配 DOSI、生成加工产物和目标 dashboard | 扩展现有 dashboard-bootstrap 与 visual dashboard 流程，不能由插件复制一套指标引擎 |
| Backend / SaaS | 查询执行、预览、版本发布与渲染 | 复用已有 dashboard artifact、metric query 和发布链路 |

插件命名：目录/发行包 `datus-tableau-plugin`，导入包 `datus_tableau_plugin`，唯一入口 `tableau = "datus_tableau_plugin"`，CLI 为 `main(argv, profile) -> int`，初始版本 `0.1.0`，Python ≥3.12。

配置位于 `agent.plugins.tableau.<profile>`：`server_url` 必填，`site_content_url` 默认空字符串，`auth_type` 默认 `pat`；PAT 模式必填 `token_name/token_secret`，密码模式必填 `username/password`；另有 `api_version=auto`、`verify_ssl=true`、可选 `ca_bundle`、`timeout=60` 秒。秘密使用环境变量引用并标记 `x-secret: true`。SDK 使用官方 `tableauserverclient`；XML 使用禁用实体解析的解析器，ZIP 仅读取所需定义，不整体解压。

公开 CLI：

| 命令（省略 `datus tableau`） | 行为 |
|---|---|
| `status` | 检测 Server/API 版本、认证和 Metadata 能力 |
| `projects list` | 列出可访问项目 |
| `workbooks list/get` | 工作簿发现与详情 |
| `dashboards list/get` | 识别工作簿内真正的 dashboard，关联 worksheet/chart，而非把所有 view 当 dashboard |
| `datasources list/get` | 数据源发现与详情 |
| `flows list/get` | Prep Flow 发现、连接与输入输出详情 |
| `sync run --dashboard-id <id> ...` | 同步选定 dashboard 及其递归依赖 |
| `sync run --project-id <id> ...` | 同步选定项目内 dashboard 及其依赖 |
| `sync run --all` | 同步当前站点所有可访问 dashboard 及其依赖 |
| `sync inspect <bundle-path>` | 离线检查资产、SQL、依赖缺口与解析覆盖情况 |

三种选择方式互斥且必须显式选择。默认输出根目录 `bi_sources/tableau/`；`--output-root` 可修改，`--overwrite` 显式更新既有来源快照。结构化输出默认 JSON，可选 table。读取命令在 normal/auto 下 allow；写本地文件的 `sync run` 在两者下 ask。manifest commands、parser 和权限逐叶对齐，不使用整个 namespace 的宽泛 allow。

Tableau 首版不提供远端发布/刷新/删除命令。SaaS 重建通过 agent 工作流衔接已有工具，不新增冒充 Datus 全局命令的插件子命令。

## 3. 本地资产、复杂 Dataset 与 ETL

本地来源包使用 `schema_version: 1`。实体分为 dashboard、chart、dataset、transform、query 和 external_source，记录来源标识与引用边；Tableau REST LUID、Metadata ID 和工作簿内部标识分别保存，不混用，不按显示名称推断连接关系。

包内包含 `manifest.json`、`assets.json`、`dependencies.json`、脱敏后的原生定义、`sql/` 和 `coverage.json`。同一来源内共享 dataset/flow 只保存一次。manifest 保存选择范围、源对象标识、版本信息、抓取时间、文件校验和、各对象状态和错误；覆盖情况与错误分开，HTTP 成功不代表语义完整。

必须采集和解析：

- Dashboard：布局、worksheet 引用、全局与局部筛选、参数、动作和说明。
- Chart/worksheet：图表与字段绑定、聚合、排序、时间粒度、计算字段、LOD 和表计算依赖；保留计算阶段及过滤顺序。
- Dataset：嵌入/发布数据源、连接身份、字段类型与别名、逻辑关系、物理 join/union、自定义 SQL、extract/刷新配置和依赖数据源。
- ETL：Prep Flow 定义、输入输出、清洗、派生字段、join、union、pivot、聚合、参数和依赖链；外部加工可通过用户提供的本地源码/定义包补充，保留源文件与节点映射。

SQL 明确标记 `source`（真实取得）、`derived`（根据模型推导）或 `target`（按 DOSI 与目标方言生成），附对应实体、参数、来源和支持状态。真实自定义 SQL 使用 Metadata `CustomSQLTable.query` 与原生定义交叉补全。Tableau 计算表达式不伪装成 SQL；无法恢复的运行时生成 SQL 标为不可用，不填入猜测内容。

同步以 Metadata + REST 建立资产和依赖，再下载可访问的 `.twb/.twbx/.tds/.tdsx/.tfl/.tflx` 定义补全。下载默认排除 extract；extract 文件和业务明细不进入来源包。原始归档仅临时处理，不作为可分享的已脱敏产物直接保留。

外部表不能被自动解释为已同步其生成任务。仓库表、视图及外部 ETL 的定义通过现有 datasource 元数据工具、用户提供的 SQL/源码或本地定义包补充；首版不新增任意外部调度系统的在线采集器。取不到定义或无法解析的节点必须保留原始证据和缺口，并阻断依赖该节点的完整重建声明。

同步处理 REST 与 GraphQL 分页，检查 GraphQL errors 及部分响应；共享依赖去重、检测循环，并逐对象记录失败。重复同步使用源标识和校验和比较，支持重试；未选中或暂时无权访问不能视为远端删除。文件采用暂存后提交，保留上一次可用快照，禁止失败过程留下伪成功结果。

解析覆盖与生成覆盖分别报告。无法转换的运算、连接或版本差异必须输出 `unsupported`；不能静默丢弃运算、过滤或权限语义。

### ETL 加工产物

采用 SQL-first 输出：`transforms/` 保存带依赖引用的运算定义，`models/*.sql` 保存可转换节点的 SELECT 模型，`pipeline.json` 保存拓扑顺序、输入输出、参数、目标方言及刷新/增量语义，`validation.json` 保存已执行检查和剩余条件。

为目标重建所需的加工生成产物，复用可满足需求的现有 DOSI dataset 和上游表；不无条件照搬整套历史 ETL。生成物保留来源节点与目标节点映射。不能翻译的脚本或自定义运算保留原定义，标为待处理，不生成假的可执行 SQL。

SQL 先做语法、方言、字段和依赖校验；有可用连接且运行环境允许时执行只读编译/EXPLAIN 检查。未做真实数据检查的产物标为未验证，不将语法通过视为数据正确。只读指标查询和 dashboard 预览属于验证，不代表执行 ETL。

## 4. DOSI 映射与 SaaS 重建

每张图提取分析意图、指标、维度、时间粒度、过滤作用域、排序、窗口及交互，再与现有 DOSI 指标、dataset 和关系逐项匹配。指标以完整 subject identity 关联，数据源以真实连接身份匹配；多源 dashboard 分区处理，不通过一个 profile 默认连接掩盖歧义。

映射报告包含原字段/计算、目标指标引用、口径差异、所需加工、可复刻状态和原因。优先复用现有指标；缺少指标/维度时生成新增或修订建议，不自动覆盖现有 DOSI 口径。源 SQL 作为分析证据，不作为未匹配图表的静默执行后备。

目标生成使用现有 `dashboards/<slug>/` artifact、图表组件和参数机制，优先保存 `.metric.json`、参数定义、结果元数据和语义模型快照。编译后的 SQL可保存为目标查询的可追溯证据，但运行时按现有 metric query 链路使用绑定模型编译。

当前 metric query 文件为单指标结构。多指标图使用多个指标查询，仅在公共维度、粒度和键唯一性验证通过后组合；不同粒度或不兼容连接应阻断，不隐式做笛卡尔积或重复聚合。不能由现有组件表达的图表/动作在差异报告列出，不能静默改成交互不同的图表并宣称等价。

保留可表达的布局、图表意图、过滤和下钻。DOSI 更新口径导致数值不同是可解释差异；验收同时检查业务意图、DOSI 绑定、计算粒度和交互，不以所有数值必须与旧系统一致作为唯一判据。

预览、查询验证、模型快照和发布复用现有 backend/SaaS 链路。发布版本固定所使用的模型，更新指标后重新生成/验证和发布，不让已发布看板静默漂移。插件不 import Datus；跨层交接使用来源包、文件和已有 agent 工具。

实现时必须锁定兼容的 agent/backend/SaaS 版本：本地独立 Datus-agent checkout 与 backend 的子模块存在能力差异；仅发现源码支持不等于已部署环境支持。

## 5. 实施顺序与验证

1. 固定来源包契约与复杂 fixtures，覆盖 Tableau 工作簿、嵌入/发布数据源、Prep Flow、外部 SQL 加工及共享依赖；增加 schema/支持矩阵。
2. 实现 Tableau 插件的认证、发现、同步和原生定义解析，以及 manifest、提示模板和技能。技能为 `tableau`、`tableau-setup`、`tableau-sync`、`tableau-rebuild`，setup 声明可变配置要求，其余技能只编排对应职责。
3. 扩展 agent 的来源包消费、DOSI 映射、加工产物生成与 visual dashboard 重建；保持既有 Superset 导出流程兼容，为后续来源准备相同消费入口。
4. 验证并补齐 backend/SaaS 对目标 artifact 的消费、模型快照、筛选和发布兼容性；已有功能直接复用，不新增第二套 dashboard 服务。
5. 更新插件文档、安装说明与 lockfile，进行逐包测试和 wheel 资源检查；完成真实目标环境验收后才声明对应版本生产可用。

自动化测试以离线 fixtures 和独立断言为主，不依赖真实 LLM：

- **契约与配置**：唯一入口、不依赖/import Datus、配置分支、秘密脱敏、空配置提示、命令/权限一致及 wheel 资源齐全。
- **同步**：部分/项目/全量选择、分页、同名不同 ID、共享依赖、循环、重复同步、权限拒绝、GraphQL 部分错误、失败重试及快照保护。
- **解析**：真实/推导/目标 SQL 区分，关系/join/union、LOD/表计算及过滤顺序、清洗/pivot/聚合、多级 Prep Flow、外部定义缺失和未知节点。
- **DOSI**：现有指标复用、来源与指标口径冲突、缺指标或维度、时间粒度/去重/分母/窗口差异、多数据源身份歧义；确保不通过原 SQL 后备掩盖失败。
- **加工产物**：依赖顺序、字段和类型、目标方言、参数及增量语义；生成物校验和稳定，生成与校验全过程不执行 ETL 写操作。
- **SaaS**：metric recipe 与模型快照、多个指标查询组合、全局/局部筛选、时间范围、空结果、口径差异、预览与发布版本一致。

端到端固定场景：选择两个 dashboard，共享一个已发布 dataset，其中一个含 Prep join/pivot/聚合及上游 SQL，另一个含 LOD/表计算；同步后检查资产去重和完整依赖图；复用已有 DOSI 指标生成 SaaS 图表。另设置一个已知不可表达运算和一个缺失指标，要求明确阻断对应图表，其他图表可独立验证。由独立 fixture 校验资产数、依赖边、SQL 来源、指标 identity、参数和目标查询结果，不以 LLM 描述作为通过依据。

### 参考依据

- [Tableau TSC API](https://tableau.github.io/server-client-python/docs/api-ref)、[Metadata API](https://help.tableau.com/current/api/metadata_api/en-us/docs/meta_api_start.html)、[CustomSQLTable](https://help.tableau.com/current/api/metadata_api/en-us/reference/customsqltable.doc.html)、[Flow REST API](https://help.tableau.com/current/api/rest_api/en-us/REST/rest_api_ref_flow.htm)。
- 仓库现有 Superset exporter 和 dashboard-bootstrap 用作来源上下文流程参考；SaaS 现有 visual dashboard artifact 用作目标交付接口。
- 本地已核对 backend 的 `MetricQueryFile`、`execute_metric_artifact_query` 和已发布查询加载代码，以及 SaaS 的 dashboard provider 与 artifact 类型；实施前需将这些能力核对到实际采用的版本。

### 首版实现与验证记录

- 新增独立 `datus-tableau-plugin`，包括 TSC 连接、发现、部分/项目/全站同步、原生解析、版本化来源包和离线校验；范围内 Metadata 自定义 SQL 使用变量和分页采集。
- 支持显式加工图的 SQL-first 产物，保留不支持或缺少语义的原生节点。Join 必须具有明确左右输入顺序，方向不可靠时阻断生成。
- DOSI 重建通过离线绑定评估器及 `tableau-rebuild` 技能交给已有真实指标/看板工具；静态评估不伪造 `.metric.json`、模型快照或已发布状态。现有 backend/SaaS 链路复用，本次没有修改这些仓库。
- 128 项插件测试通过，包含真实 TSC 0.42 HTTP 序列化、SQLite 加工结果、来源覆盖/依赖传播、DOSI 绑定和插件契约；审查回归另覆盖连接字符串凭据脱敏、不支持 SQL 转换阻断、Metadata 自定义 SQL 和外部表缺口传播、快照并发锁与崩溃恢复、写锁内失败同步保护已有快照及恢复备份、部分选择范围、XML 命名空间凭据与样本子树脱敏、严格 clean 语义和深图资源限额。POSIX 锁要求与超限 blocked 行为详见插件 README。Ruff 检查与格式校验通过，wheel 构建并检查 manifest、schema、模板和四个技能齐全。
- 真实 Tableau Server、目标 DOSI 数据源和 SaaS 预览/发布尚未验收。原生 Prep 容器、未知脚本及无等价映射的 LOD/表计算保留定义并明确阻断，不宣称已经通用转换。
