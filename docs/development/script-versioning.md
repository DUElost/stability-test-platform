# Agent 脚本版本与退役

本文是版本化脚本目录、参数分层、扫描和退役的开发契约。架构决策见
[`ADR-0020`](../adr/ADR-0020-plan-step-one-shot-migration.md)。

外部工具（原厂 / 专项工具）的**源码入仓边界与接入形态**另见
[`ADR-0033`](../adr/ADR-0033-tool-kit-ecosystem-integration.md)：新工具族必须走
Tool Contract + 包存储，既有工具族的新版本目录允许继续 legacy 形态（D0 分级准入）
——本文的版本目录约定对已入仓脚本族持续有效。

## 目录与扫描

```text
<STP_SCRIPT_ROOT>/<name>/v<version>/<entry>.{py,sh}
```

- 一级目录是脚本名，二级目录以 `v` 开头；扫描器只识别 `.py`（python）与
  `.sh`（shell）两种后缀（`script_catalog._SUPPORTED_SUFFIXES`）——`.bat/.cmd`
  等 Windows 批处理**不受支持**（历史文档曾宣称支持，2026-09 按实现收口，
  #1029）；
- 入口是首个非 `_` 前缀的可识别脚本；
- `_` 辅助模块在入口扫描时跳过，但仍受版本目录不可变门禁保护；
- 扫描结果：`created`、`skipped`、`conflicts`、`deactivated`；
- `STP_SCRIPT_ROOT` 必须显式配置；扫描机与运行机不同时另设
  `STP_SCRIPT_RUNTIME_ROOT`。

## 已发布版本不可变

`script.content_sha256` 是扫描时冻结的期望值。原地修改已发布版本只会产生 conflict，
不会更新数据库基线；引用该版本的 Plan 会在 precheck 阶段
`script_verify_failed`，self-heal 也无法修复磁盘内容与数据库期望值的失配。

正常修改必须创建新版本。CI 和本地门禁：

```bash
python tools/dev/check-script-version-immutability.py --base origin/main
```

`POST /scripts/scan?force_rebaseline=true` 只用于契约已经被外部破坏后的恢复：仅 admin
可调用，有 RUNNING、QUEUED 或 PRECHECK PlanRun 时返回 409。不能作为日常改版路径。

## 种子迁移治理（#942 裁决 A）

数据迁移里的种子逻辑（INSERT/UPDATE `script` 表、停用旧版本）**不受服务层
保护**（alembic 内裸 SQL），因此负有与 API 同构的引用检查义务：

- 对**已存在**的 `(script_name, script_version)` 做任何写操作（UPDATE
  `default_params`/`param_schema`、停用 `is_active`）前，必须先查
  `plan_step` 引用；
- 引用数 > 0 → 迁移**失败**（RuntimeError 带重指指引）——与 API 层
  `_ensure_script_can_be_deactivated` 的 409 同构；操作者重指 plan_step 或
  以新建版本表达参数变化后重跑；
- **禁止**裸 `UPDATE script SET default_params ...` 与无引用检查的
  `UPDATE script SET is_active = false ...`；
- 全新版本行的 INSERT 不受此约束。

模板权威源：[`backend/services/script_seed_governance.py`](../../backend/services/script_seed_governance.py)
（`raise_if_version_referenced` / `raise_if_any_version_referenced`）。迁移
**自包含**原则下不 import 服务层——把该文件当前实现**内嵌**进迁移文件，并
在迁移 docstring 注明复制来源与复制日期。带数据行为测试见
`tests/test_script_seed_governance.py`。

裁决与论证：[`docs/design/2026-09-08-seed-migration-governance.md`](../design/2026-09-08-seed-migration-governance.md)。

## 参数分层

已存在版本的 `default_params` 不可原地修改；API 返回 422。需要修改默认参数时使用：

```text
POST /api/v1/scripts/{name}/versions
```

派发参数来源：

1. 脚本版本的 `default_params`；
2. PlanStep `step.params` 对用户声明键的覆盖；
3. WiFi 资源池向 `connect_wifi` 或 `monkey_setup.params.wifi` 补齐未声明字段；
4. 管理套件按冻结的 `dispatch_suite` 向 `script:mtbf_*` 注入测试点数量与项目。

Pipeline action 唯一格式是 `script:<name>`。执行链：

```text
文件 → scripts scan → DB script → PlanStep
  → default_params ⊕ step.params/资源注入 → pipeline_def
  → Agent ScriptRegistry → subprocess → stdout JSON
  → step_trace → JobStatus → aggregator
```

## 新增脚本评审 checklist（#507 B 节）

ADR-0029 v2 把**项目模型**收敛为登记簿（客户 / 关系 / 形态 / JIRA 映射——adb 指纹读不出的部分），
**执行差异归脚本**。项目变量、项目 × 脚本绑定、派发门禁这些形态不再长回来，全部挂在
「执行差异由脚本路由吸收」这一条上；新脚本评审按下表逐条核对。

适用前提：**行为随设备而变的脚本**。与机型无关的脚本（`noop` / `clean_env` /
`push_resources` 等）不适用——逐条标注「不适用 + 理由」，不要直接跳过。

- [ ] **自行读指纹路由，不吃调用方按机型传参**：不得要求调用方用 `step.params` /
  `default_params` / 多 Plan / 多脚本版本去分化机型差异。反例见 #507 A 节点名的
  `install_apk`（纯 `apk_path` 参数驱动）；
- [ ] **路由未匹配一律 fail-fast**：路由表是闭集白名单，不得静默落「默认分支」；
  错误信息必须带**实测指纹值 + 已知集合**，现场一次定位补键。样板：
  `backend/agent/scripts/flash_firmware/v1.3.14/flash_firmware.py:1222-1228`
  （`no firmware family route for model {model}; known models: ...`）；
- [ ] **路由决策进 step_trace**：至少落「依据字段 / 实测值 / 选中分支 / 选中版本」，
  事后可复盘「这台设备为什么走了这个分支」。样板：同文件 `metrics.route`
  （`decided_by=params|fingerprint` + `model` / `family` / `version` / `manifest`，
  docstring :1091，`params` 分支 :1128-1132，`fingerprint` 分支 :1284-1292）；
- [ ] **指纹来源差异鲁棒**：同一指纹可能有多个来源且拼写不一——`getprop
  ro.product.model` 返回连字符（`MLD-LX3`），`adb devices` 的 model 字段是下划线，
  两者不同源（同文件 :67、:170-173、:585-594）。路由键要收两套拼写或先归一化再查表，
  否则「路由吸收差异」会变成「路由自己制造差异」。

评审时怎么验：

1. 该脚本行为是否随机型 / 平台 / 能力而变？不确定就看近 3 个月是否因机型改过参数、
   开过专用 Plan；
2. 变的话：读没读指纹？匹配不上会怎样？决策留痕在哪？
3. 对照样板核四要素，缺一即要求补测（路由命中 / 未命中各一例）。

**触发点（#507 C 节）**：出现下列信号时优先怀疑本约束被绕过——

- 有人提出给项目加变量 / 加脚本绑定 / 加派发门禁；
- 同一专项出现「XX 项目专用」的脚本或 Plan；
- 新机型接入后某步骤静默跑错分支而非报错。

**非目标**：不在平台层加 `script.applicable` 之类的项目门禁；也不要求所有脚本都读指纹。
来源与论证见 #507（本清单固化其 B 节）。

## 退役与删除

退役前的只读诊断：

```bash
python -m backend.scripts.check_unreferenced_script_versions [--json] [--name flash_firmware]
```

它按 `PlanStep.script_name + script_version` 统计配置引用。候选版本通过
`DELETE /api/v1/scripts/{id}`（专用软退役，审计 `action=deactivate`）或
`PUT /api/v1/scripts/{id}` 设 `is_active=false`（审计 `action=update`）下线；两者
共用 `_ensure_script_can_be_deactivated` 同一守卫，仍被 Plan 引用时返回
409 `SCRIPT_STILL_REFERENCED`。重新激活只有 `PUT {"is_active": true}` 一条路，且
无守卫（不做引用校验）。

退役保留版本目录，只让版本退出活动目录。不要删除历史版本目录：删除会触发不可变
门禁，也会破坏历史 PlanRun 的重放与追溯。

`refs == 0` 只代表没有当前 Plan 配置引用，不代表没有历史运行。退役前还应查看
`GET /api/v1/scripts/{id}/usage` 的 `run_count`、`success_rate` 和 `versions_used`；
配置与近期运行两个维度都为零时更稳妥。

三条配套事实（#735 A 批 50 条退役实测，论证见
[2026-09-16-script-retire-channel](../notes/process/2026-09-16-script-retire-channel-735.md)）：

- **`usage` 的执行事实窗口被 `PLAN_RUN_RETENTION_DAYS` 截断**：库内 run 只覆盖保留期，
  `versions_used` 为空 = 「留存窗口内零执行」，不等于「从未执行」；更早的使用无库内证据。
- **退役不被扫描复活**：`scan_script_root` 对 `is_active` 只做单向管理（目录缺失即停用），
  目录仍在的已停用行永不复活——再激活是显式运维动作。反过来的坑是**种子迁移**：
  已应用的 seed 迁移在 `upgrade()` 分支里显式 `is_active = true`，因此空库重建/灾备会
  复活退役状态。退役是生产数据事实，不经迁移链表达（迁移丢操作者身份与 `audit_logs`）；
  漂移收口归 #2055 与 #735 长效机制。
- **核验退役结果不能用 `GET /api/v1/scripts?name=<脚本>`**：该端点没有 `name` 过滤参数
  （传入被静默忽略），按版本字符串筛选会命中其他脚本族的同名版本而误判「未生效」。
  正确姿势：取 `GET /api/v1/scripts?is_active=true` 后按 `(name, version)` 二元组对拍。
