# Plugin 自动化测试策略

> 面向"要再加 20+ 个 plugin"的规模。重点是 **L0 契约层去重复** 和 **L3 跨插件工作流**。
>
> 状态：待评审 · 最后更新 2026-08-10

---

## 1. 现状盘点（实测，非推测）

| 项 | 现状 |
|---|---|
| 契约测试 | 每插件一份 `tests/test_plugin_contract.py`，**靠复制粘贴**（CONTRIBUTING 原话："Copy it into new plugins"） |
| 严格程度不一 | k8s 有 8 个契约断言（含"skill 不得回退到 kubectl"）；aws 系明显更薄 |
| 单元打桩 | 手写 `FakeSession`/`FakeResponse`；airflow 与 statsig **各写了一份** |
| 测试规模 | 13 插件 / 40 文件 / ~8600 行 |
| 真环境集成测试 | **无**。CONTRIBUTING 明确 "no real LLM or live-service calls" |
| 跨插件测试 | **无** |
| agent 级测试 | **无**——`permissions` 那几十行 YAML 从未被真正验证过 |
| CI | **无**（`.github/workflows/` 与 `ci/` 都不存在） |

**实测的一个可立刻修掉的障碍**

```
$ pytest <全部 13 个插件> -q
Interrupted: 22 errors during collection      # import file mismatch
$ pytest <全部 13 个插件> -q --import-mode=importlib
Interrupted: 7 errors during collection       # 只剩 7 个
```

根因两层：① 多插件有同名 `test_commands.py` / `test_plugin_contract.py`，prepend 导入模式下 basename 冲突；
② 改用 `importlib` 后，剩下 7 个文件因 `from conftest import ...` / `from skill_blocks import ...`
失败——它们依赖 prepend 模式把 `tests/` 塞进 `sys.path`。

**修法**：把这 7 处共享件从 `conftest` 移到具名模块（或收进 testkit），配 `--import-mode=importlib`。
改动量 = 7 个 import 语句 + 2 个 conftest 拆分。**收益：全仓库一次跑完**（现在两插件并跑是 135 passed / 2.61s，
全量预计 <15s），CI 不必再维护 13 路矩阵。

---

## 2. 五层金字塔

| 层 | 测什么 | 外部依赖 | 耗时 | 触发时机 |
|:--:|---|---|---|---|
| **L0** 契约 | manifest ↔ parser ↔ permissions ↔ 打包 一致性 | 无 | 秒 | 每次 push |
| **L0.5** 输出契约 | 每个命令的 stdout JSON 形状（golden schema） | 无 | 秒 | 每次 push |
| **L1** 单元 | 客户端/参数/错误映射/退出码（打桩） | 无 | 秒 | 每次 push |
| **L2** 单插件真环境 | 真服务上的单插件行为 | docker compose | 分钟 | PR |
| **L3** 跨插件工作流 | **多插件串成端到端数据流** | compose / 录制回放 / 云沙箱 | 分钟–十分钟 | PR（local）+ nightly（cloud） |
| **L4** agent 级 | agent 是否走对命令序列、权限门禁是否真的拦住 | LLM + 上述环境 | 十分钟 | nightly |

L0–L1 已有基础但重复严重；L2–L4 全缺。

---

## 3. L0：把契约测试做成 testkit（去掉复制粘贴）

**问题**：13 份复制的契约测试已经开始漂移，再加 20 个插件必然失控。

**方案**：`datus-plugin-testkit` —— 一个**仅 dev 依赖**的 pytest 插件。
这不违反"插件不得互相 import"：那条约束针对运行时 `dependencies`，dev-deps 不在其列。
（testkit 自身同样不得 import `datus`。）

新插件接入 = 一个文件三行：

```python
# tests/test_plugin_contract.py
from datus_plugin_testkit import contract_suite

test_contract = contract_suite(
    package="datus_snowflake_plugin",
    plugin_name="snowflake",
    writes={"warehouse resize", "task resume", "copy"},   # 必须为 ask 的命令
)
```

testkit 自动生成的断言（把 k8s 那份**最严格**的版本作为全仓库基线）：

- `manifest_version == 1`；`cli` code ref 可解析；`skills`/`system_prompt` 路径存在
- `pyproject` 恰好一个 `datus.plugins` entry point，且**不依赖 `datus`**
- **`commands` 目录 ≡ argparse 子命令树**（防 manifest 与实现漂移）
- **`permissions` 覆盖每个叶子命令**，且 `writes` 里的在 `normal` 与 `auto` 下都是 `ask`
- `main(["--help"], {})` 返回 0 且不需要配置
- system_prompt 模板 `profiles={}` 与非空双分支都能渲染（StrictUndefined）
- `<name>-setup` skill 声明 `requires_mutable_config: true`
- secret 字段标了 `x-secret: true`；prompt 引用的非 secret 字段都在 schema 里
- **子进程验证 `import <pkg>` 后 `sys.modules` 不含 `datus`**
- wheel 里确实打进了 `datus-plugin.yml` / skills / prompt（`unzip -l`）

再附带把 `FakeSession` / `FakeResponse` / `paged` 收进 testkit，顺手解决 §1 的 import 冲突。

**顺带做 L0.5 输出契约**：跨插件编排靠 stdout JSON，**JSON 形状就是 API**。给每个命令存一份
golden schema，字段改名/消失时测试失败：

```python
test_output_contract = output_schema_suite(package="datus_s3_plugin", golden="tests/golden/")
```

这一层极便宜，却是 L3 稳定的前提——下游插件靠字段名取值，改名就是破坏性变更。

---

## 4. L2：单插件真环境

- 统一 marker：`-m integration`，默认 `addopts = -m "not integration"`
- **凭证/服务不可用时 skip 而不是 fail**（`pytest.importorskip` 同理，用 `--datus-env` 决定）
- 本地服务用一套 compose（见 §5.3），云服务用录制回放或沙箱账号
- k8s 插件的 `tests/fixtures/minikube-workload.yaml` 已经是这个方向的雏形，把它规范化

---

## 5. L3：跨插件工作流测试（本文重点）

### 5.1 必须在 CLI 边界做，不在 Python 层

三条理由，都是硬约束：

1. **契约禁止跨插件 import** —— 工作流测试若 import 两个插件的 Python，本身就违约。
2. **插件间真实的协同界面就是 CLI + stdout JSON**。agent 就是这么用的，测试也该这么测。
3. 只需一个 subprocess runner + JSON 解析，**不依赖任何插件代码**，新增插件零改动。

由此得出结构：跨插件测试不属于任何一个 distribution，放**仓库级** `tests/e2e/`（不发布）。

### 5.2 场景即数据：YAML 声明，不写 Python

新增一条工作流 = 加一个 YAML 文件，零代码。runner 通用。

```yaml
# tests/e2e/scenarios/s3-glue-athena.yml
name: 落地 parquet → 爬表 → 查询验证行数
requires: [s3, glue, athena]          # 缺任一插件或凭证 → skip 而非 fail
env: [local, cloud]                    # 支持哪些环境档
vars:
  bucket: ${E2E_BUCKET}
  prefix: e2e/${RUN_ID}                # RUN_ID 由 runner 生成，保证隔离

steps:
  - name: 上传测试数据
    run: s3 cp ./fixtures/events.parquet s3://${bucket}/${prefix}/events.parquet
    expect: {exit: 0}

  - name: 触发 crawler
    run: glue crawlers start --name ${RUN_ID}-crawler
    expect: {exit: 0}

  - name: 等 crawler 就绪
    wait: glue crawlers get --name ${RUN_ID}-crawler
    until: "$.State == 'READY'"        # JSONPath 断言，轮询而非 sleep
    timeout: 300s
    interval: 10s
    fail_on: "$.LastCrawl.Status == 'FAILED'"

  - name: 查询验证
    run: athena queries start --sql "SELECT count(*) FROM ${RUN_ID}.events" --wait
    expect: "$.rows[0][0] == 1000"

cleanup:                                # always-run，即使中途失败
  - s3 rm s3://${bucket}/${prefix}/ --recursive
  - glue crawlers delete --name ${RUN_ID}-crawler
```

设计要点，每条都对应一个真实的踩坑点：

| 要点 | 为什么 |
|---|---|
| **JSONPath 断言** | 直接复用 `datus-k8s-plugin/datus_k8s_plugin/jsonpath.py` 的实现思路，不引新依赖 |
| **`wait`/`until` 轮询，禁止 sleep** | 异步作业（crawler/DAG/Spark job）耗时不定；sleep 要么慢要么 flaky。k8s 插件的 `wait --for` 和 airflow 的 `dag_discovery_timeout` 已是同一思路 |
| **`fail_on` 快速失败** | 别在明确失败态上白等 5 分钟（对齐 commit `5267995` "make a failure visible instead of waiting it out"） |
| **`RUN_ID` 前缀隔离** | 多人/多分支/并行 job 共享同一个云账号时不互踩；也让清理可靠 |
| **`cleanup` always-run** | 云资源泄漏 = 真金白银。runner 用 try/finally，且清理步骤本身幂等 |
| **`requires` 声明** | 插件未安装或无凭证时自动 skip，不污染 CI 红灯 |
| **`env` 档位** | 同一场景可在 local compose 与云沙箱两档跑 |

### 5.3 三档环境，成本递增

| 档 | 内容 | 何时跑 |
|---|---|---|
| **local** | 一套 compose：postgres · minio(S3 兼容) · airflow · superset · kafka+SR · trino · clickhouse · spark；k8s 用 kind/minikube | **每个 PR** |
| **replay** | 云 API 的**录制回放**（VCR 风格）：真跑一次录 HTTP 流量，之后回放 | **每个 PR**，覆盖 Snowflake/Databricks/BigQuery 这类无法本地化的 |
| **cloud** | 真沙箱账号（独立 AWS account / Snowflake trial / DBX workspace），带预算告警 | **nightly** + 发版前 |

`replay` 档是"高效"的关键：它让云工作流在 PR 里以毫秒级、零成本跑，且能捕获 SDK 调用序列的回归。
代价是录制需要定期刷新（建议 nightly 的 cloud 档跑完自动重录）。

### 5.4 值得先写的工作流场景

按"接缝密度"排序 —— 跨插件 bug 集中在**一个插件的输出喂给另一个插件的输入**这个接缝上，单插件测试永远发现不了。

| 场景 | 串联 | 验证的接缝 |
|---|---|---|
| 落地 → 建表 → 查询 | s3 → glue → athena | S3 URI 格式、Glue 表名推断 |
| **dbt 全链** | dbt → snowflake(skill) → superset | manifest 血缘 → 仓库对象 → BI dataset id |
| 部署 → 触发 → 排障 | airflow(deploy) → airflow(trigger) → airflow(logs) | DAG 发现延迟、日志定位 |
| 流式入湖 | kafka → flink → iceberg | schema 兼容、checkpoint 与快照 |
| Spark on K8s | k8s(apply) → spark(history) → prometheus | pod 状态 → app id 关联 |
| 跨云搬运 | s3 → gcs | 两套凭证并存、大文件分片 |
| BI 迁移 | superset(export) → superset(import) | §13 迁移后的 spec JSON 往返一致性 |

### 5.5 高效手段（三个）

1. **变更影响分析**：`git diff` 出改动的插件 → 只跑 `requires` 命中它们的场景。全量留给 nightly。
2. **分层触发**：L0/L0.5/L1 每次 push（秒级）· L2/L3-local+replay 每个 PR（分钟级）· L3-cloud/L4 nightly。
3. **并行 + 隔离**：`RUN_ID` 让场景天然可并行；`pytest -n auto` 直接开。

---

## 6. L4：agent 级测试（别跳过）

plugin 的真实使用者是 **agent**，不是人。以下三件事只有这一层能测：

1. **权限门禁真的生效吗？** `normal` 档下让 agent 去做 `dags delete` / `warehouse resize`，
   断言它**触发了确认**而非直接执行。现在 `permissions` 的几十行 YAML 从未被验证过——
   L0 只验证了"YAML 里写的是 ask"，没验证"运行时真的拦住了"。
2. **skill 与 prompt 是否把 agent 引到正确命令？** 给自然语言任务（"这张表昨天为什么没更新"），
   断言 tool-call 序列**包含期望子序列**（如 `dags list-runs` → `tasks logs`），而不是断言逐字相同。
3. **跨插件协同 agent 能不能自己串起来？** 这是 §5.4 场景的 agent 版本。

实现手段：跑 datus agent → 从 trace 提取 tool-call 序列做断言。本地已有 `analyze-datus-trace` skill
与 Langfuse 接入，`Datus-agent` 侧有 `multi_round_benchmark.py` 可作为运行器骨架。
成本控制：小模型 + 少量场景 + 只在 nightly。

---

## 7. CI 编排

```yaml
# .github/workflows/plugins.yml（骨架）
jobs:
  fast:            # L0 + L0.5 + L1，全仓库一次跑
    run: uv run pytest --import-mode=importlib -q       # 前提：修掉 §1 的 7 处 import
  integration:     # L2 + L3(local/replay)
    services: [postgres, minio, airflow, superset, kafka]
    run: uv run pytest -m integration --datus-env=local
  nightly:         # L3(cloud) + L4
    schedule: cron
    run: uv run pytest -m "integration or agent" --datus-env=cloud
```

要点：
- `fast` job 必须能**一次跑全仓库**——所以 §1 的 import 修复是 CI 的前置条件，不是可选优化。
- nightly 的 cloud job 跑完**自动重录 replay 夹具**并提 PR，保证录制不腐坏。
- 每个 job 独立 `RUN_ID`，且 cloud job 串行执行（避免沙箱账号配额打满）。

---

## 8. 立即可做的三件事（按 ROI 排序）

| # | 事项 | 成本 | 收益 |
|:--:|---|---|---|
| 1 | 修 7 处 `from conftest import` / `from skill_blocks import` + 加 `--import-mode=importlib` | ~1h | 全仓库一次跑通，CI 从 13 路矩阵变 1 个 job |
| 2 | 抽 `datus-plugin-testkit`（契约 suite + FakeSession + 输出 golden） | 1–2 天 | 新插件契约测试从 200 行复制变 3 行；严格度统一到 k8s 基线 |
| 3 | 建 `tests/e2e/` runner + compose + 前 2 个场景 | 3–5 天 | 跨插件接缝首次有覆盖；后续每加一个场景只是一个 YAML |

L4 建议等 Wave 1 插件落地后再做——它依赖足够多的插件才有意义。
