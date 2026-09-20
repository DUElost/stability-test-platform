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

- **待激活对账（#2931）**：「合入 → 到部署树 → scan 注册 → `plan_step` 重指」四道里，
  第 3 道做没做此前无任何东西会喊（`check_unreferenced_script_versions` 的输入是
  DB 行，结构性看不见「磁盘有、库无行」）。收尾判据：新版本合入后跑

  ```bash
  STP_SCRIPT_ROOT=<部署树>/backend/agent/scripts \
    python -m backend.scripts.check_unreferenced_script_versions --pending-activation
  ```

  确认它**不再列出该版本**——视图空才是「磁盘 head 均已注册且激活」的证据。
  三态：`unregistered`=库无行（scan 未跑或跑在旧树）；`inactive`=有行未激活
  （反激活遗留）；不列出=已生效。按族 **head 版本**报告（族内旧版零引用是
  #735 退役面，不进此账——存量 backlog 会把真滞后淹掉，判据落目录行/数据行，
  不做散字符串相邻匹配）。工具只读；退出码 0=对账完成（账本非门禁），
  2=`STP_SCRIPT_ROOT` 未设=无从判定，不得读成「没有落后项」。

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

## 新版本上线收尾（模板钉钉 + 控制面生效）

「版本目录已合入」≠「下一窗会跑到新行为」。执行链按精确版本解析、无 latest 兜底
（#2865）：合入后若未完成下列收尾，修复在真机上等于不存在。

仓库侧（可进 PR，由守卫锁住）：

1. 新增 `backend/agent/scripts/<name>/v<ver>/`（全量副本，见上）；
2. 若该脚本出现在 `backend/schemas/pipeline_templates/*.json`，把对应
   `action: script:<name>` 的 `version` **钉到磁盘最新版**——模板是编辑器种子，
   钉旧版会让新建 Plan 继续带泄漏/旧语义；守卫
   `tests/test_pipeline_template_script_pins_2865.py` 对
   `check_device` / `monkey_setup` 做「模板 pin == 磁盘最新」对拍（名单可随复发面扩）。

控制面侧（运维授权写操作，不进 PR）：

3. `POST /scripts/scan`，确认 `created` 命中且 `conflicts=0`；
4. 把仍引用旧版的 `plan_step` 重指到新版（生产周期链等存量 Plan **不会**随模板自动迁）；
5. 单机验证后再放量。

同族最新 active 版本受退役判据「承接面豁免」（见下），**注册与重指不必绑成一批**；
但重指未做前，修复对线上无效——不要把「代码已合」记成「问题已闭」。

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
  `default_params` / 多 Plan / 多脚本版本去分化**机型**差异。反例形态 = 调用方按机型
  传参（如 `step.params.device_model`）让脚本内分支；**项目/用例维度的参数不算**，
  见下「判定边界」；
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

**判定边界：「adb 指纹读不出」的差异不判落空**（#507 A 节判定口径，并入本清单）。
差异维度若属**项目 / 用例 / 编排选择**，脚本即使读指纹也推不出来，按 ADR-0029 归
登记簿 / 编排层职责——`install_apk.apk_path`（同设备跑不同项目的用例包）、
`push_resources.files`、`monkey_setup.steps`、`connect_wifi.ssid`、
`clean_env.uninstall_packages` 等均属此类，**判可接受**；不要按「参数驱动」误判为
约束落空（#507 A 节已逐项判定，避免评审重复争论）。

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
- **scan 只报告，退役是显式动作**（#2386 起）：`scan_script_root` 对 `is_active` 的单向
  管理是**有条件的**——仅当被扫子树 == 部署目标（`origin/main`）时才反激活；否则跳过，并把
  跳过的版本显式列进响应 `deactivation_skipped_versions` 且写审计。目录仍在的已停用行永不
  复活；需要无条件反激活时走显式出口 `POST /api/v1/scripts/scan?allow_deactivate=true`
  （传了即跳过 git 判定）。「盘上缺失」是否等于「已退役」的取向由 **ADR-0046 D2** 裁决，
  当前方向 = scan 只报告、退役走显式运维动作。
- 反过来的坑是**种子迁移**：已应用的 seed 迁移在 `upgrade()` 分支里显式 `is_active = true`，
  因此空库重建/灾备会复活退役状态。退役是生产数据事实，不经迁移链表达（迁移丢操作者身份与
  `audit_logs`）；漂移收口归 #2055 与 #735 长效机制。
- **核验退役结果不能用 `GET /api/v1/scripts?name=<脚本>`**：该端点没有 `name` 过滤参数
  （传入被静默忽略），按版本字符串筛选会命中其他脚本族的同名版本而误判「未生效」。
  正确姿势：取 `GET /api/v1/scripts?is_active=true` 后按 `(name, version)` 二元组对拍。

### 判据与巡检（唯一事实源）

工具口径「active 且 `refs == 0`」只是**候选面**，不等于可退役。判据固化在
`backend/services/script_retirement.py`，诊断工具与退役执行器都必须经它——不允许各自再写
一份豁免规则（两份口径不合一，下一批就会漂）。按优先级先命中先决定：

| 优先级 | 条件 | 结论 |
|---|---|---|
| 1 | `is_active = false` | 已退役，跳过不重复处理 |
| 2 | `refs > 0` | 不可退役（与 API 409 `SCRIPT_STILL_REFERENCED` 同构） |
| 3 | 同族**最新的 active 版本** | 承接面豁免。**退出判据**：同族一旦出现更新的 active 版本，旧最新版立即失去豁免并转入 4/5 判定——豁免不是永久身份 |
| 4 | 留存窗口内有执行事实 | 末次执行距今 < 60 天 → 保留（追溯期）；≥ 60 天 → 可退役 |
| 5 | 其余（零引用且窗口内无执行事实） | 可退役 |

- 冷却期常量是 `script_retirement.STALE_COOLDOWN_DAYS = 60`，与本文的「60 天」由
  `backend/tests/test_script_retirement_guard.py` 互锁，改一侧必致另一侧红。
- `script.created_at` **不参与判据**：它是入库注册时间，扫描会把早已发布的历史目录补登记
  （2026-09-13/14 一轮就补了 `monkey_launch@5.0.1`、`gpu_check@1.0.7` 等），按它冷却会把
  最该退役的行留在场上。
- 后置不变量：任何脚本族都不得因一批退役失去全部 active 版本；判据自身 fail-loud 抛
  `InconsistentRetirementPlan`，而不是等运维把某个脚本打成「无版本可选」。

巡检（「超期零引用仍活跃」即违规）：

```bash
python -m backend.scripts.check_unreferenced_script_versions --guard [--json]
```

退出码：`0` 无到期项 / `1` 存在应退役而未退役的版本 / `2` 执行事实维度不可得（**不降级**
为「零使用」——那会让巡检偏向过度退役）/ `3` **工具自身异常**（不是判定结果）。`3` 与 `1`
必须可区分：`1` 是退役授权依据，若「工具坏了」与它同码，就会被读成「有版本该退役」，
后果是反向的过度退役。`-m` 与 `python backend/scripts/check_unreferenced_script_versions.py`
两种调用形态等价（工具侧有 `REPO_ROOT` bootstrap 与子进程回归用例兜住）。默认模式仍恒 `0`
（诊断工具，非门禁）。CI 只锁判据函数与上述退出码（CI 不得连生产库）。

**执行者 = 控制面 systemd timer**（2026-09-17 补上；此前本文档写「运维或定时任务跑」而该任务
并不存在，被 24h 审计当场指出——判据、退出码、工具三层齐备却零执行，等于没有守卫）：
`deploy/control-plane/systemd/stp-script-guard.{service,timer}` 每天 09:30 跑
`tools/dev/script_guard_probe.py`，它只 subprocess 调 `--guard --json`，把
`due / unknown / broken / last_run` 四个值落成 node-exporter textfile 指标。

- **巡检永不写库**：`due=N` 只表示「有 N 条待人工授权」；实际退役仍须 `plan` 出 manifest、
  复核后跑 `retire_script_versions.py execute --yes`。
- 任务退出码：`due`（1）与 `unknown`（2）算任务**成功**，只有 `broken`（3）让 timer failed 进
  告警面——否则「有待退役项」会天天报失败，真故障反而被淹死在告警疲劳里。
- `last_run` 指标专治「守卫静默停摆」：`due=0`（干净）与「从没跑过」必须可区分；指标写不出去
  probe 当场失败，不做「跑成功了但没人知道」。
- 告警面（`deploy/prometheus/alerts-stability-platform.yml`）两条规则：
  `StabilityScriptGuardRetirementDue`（`due > 0` 持续 7 天＝有版本压着没人授权）与
  `StabilityScriptGuardUntrusted`（`broken==1 or unknown==1 or time()-last_run > 48h or
  absent(last_run)`——四种不可信形态合成一条，以免告警风暴）。这两个 textfile 指标的名字由
  `tests/metrics_registry.py` 的 `textfile_metric_index()` 从生产者源码静态提取后并入注册表
  索引，**不是豁免口子**：生产者改名或删掉某个指标，引用它的告警立刻按「未知指标」红。
- **不进 CI**：CI 不得连生产库，夜间 `backend-test` job 也不是这条巡检的执行者。

批量执行是两段式——先只读出 manifest，人工复核后再写：

```bash
python tools/dev/retire_script_versions.py plan --out /tmp/retire.json          # 只读
python tools/dev/retire_script_versions.py execute --manifest /tmp/retire.json --yes
python tools/dev/retire_script_versions.py reactivate --manifest /tmp/retire.json --yes  # 误退役处置
```

`execute` 只经控制面 API（服务端引用守卫 + `audit_logs` + 脚本目录版本缓存失效），不直连
数据库写、不碰版本目录文件；缺 `--yes` 只 dry-run；写前逐条核对 `(name, version)` 未漂移、
写后读回复核；默认拒绝非本机回环以外的 API 地址（需 `--allow-remote`）。
