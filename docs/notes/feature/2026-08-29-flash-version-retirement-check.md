# 脚本版本退役检查工具（评审 P5 落地）

Status: implemented
Class: feature

## 决定了什么

新增只读诊断工具 `backend/scripts/check_unreferenced_script_versions.py`：
按 `plan_step.script_name + script_version` 冗余引用统计每个 script 版本
的引用数，标出「is_active 且零引用」的退役候选——解决「flash_firmware
11 个版本堆积，不知道哪些已无 plan 引用、可退役」的盲区。`script` 表
每版本一行（无独立 version 表），引用是非 FK 冗余字符串，无法靠约束
发现悬空版本，只能主动查。

### 常驻化方式：只进 AGENTS.md，不做 CI job

工具落地时**在 `.github/`、`scripts/run_gates.py`、`AGENTS.md`、`CLAUDE.md`
里零引用** —— 只在人想起来手动跑时才跑，等于没解决膨胀问题。收口时选了
最轻的接法：在 `AGENTS.md` 新增「脚本版本退役」一节记录用法与口径。

没做 CI job 的理由同上（空库无判别力），外加：退役决策是低频人工动作
（一年可能一次），为它挂一个常驻 job 的收益不匹配；而完全不记录的代价是
一年后没人记得有这个工具 —— 那正是它要防的「无人过问的堆积」。

### 退役 ≠ 删除

写文档时查证出的，此前没落在任何文档里：

- **删除版本目录会被 CI 拦下**：`check-script-version-immutability.py` 把
  git status 的 `D` 与 `M`/`R`/`T` 同等视为变更。
- **删了还会破坏 sha 一致性**：历史 `plan_step` 的 `script.sha` 会与磁盘
  永久对不上 —— 2026-07-31 那次全平台派发中断就是这个形态（当时是
  `ruff --fix` 原地改写，效果等同）。
- **退役的正确动作**只有 `PUT /api/v1/scripts/{id}` 带 `is_active=false`：
  目录留着，不再进 `?is_active=1` 目录；且扫描**不会**把它重新激活
  （`script_catalog.py:281`，那个状态只来自 admin 停用端点或 seed 迁移）。

### 退役接口已有硬守卫，误操作退不掉

`scripts.py:484` 在 `payload.is_active is False` 时先跑
`_ensure_script_can_be_deactivated()`，其连接条件**与本工具完全相同**
（`PlanStep.script_name + script_version`，`scripts.py:250`）——
只要还有 Plan 引用就 **409 `SCRIPT_STILL_REFERENCED`**，并回传 `plan_ids`。

于是两层的职责是：**本工具提前圈候选，`PUT` 最终裁定**，判据一致，不会
出现「工具说可以、接口却拒」或反之。这让「只报告不执行」的定位是安全的。

## 放弃的备选

- **做 CI 门禁**：引用关系依赖生产数据（CI 空库全零引用 = 全报候选，
  无判别力）；定位为人工运维诊断工具，退出码恒 0。
- **自动置 is_active=false**：退役涉及派发影响面判断（存量 PlanRun、
  灰度窗口），保留人工决策；工具只报告候选。

## 如何验证

- 单测 `backend/tests/test_script_reference_check.py`：有引用 / 零引用 /
  已退役三态覆盖，验证「只有 active 且零引用才进候选」。
- 生产库只读实跑：`python -m backend.scripts.check_unreferenced_script_versions
  --name flash_firmware`（读生产 `stp` 库，无写操作）。

## 判据的边界：refs == 0 只覆盖配置态

本工具的 `refs` 来自 `plan_step` 的**当前配置**引用。它回答不了「这个版本
后续还会不会再用」—— 比如一个版本刚被改走配置、但历史 PlanRun 仍需追溯
（退役不删目录正是为此），或它属于某个季度才跑一次的 Plan。

运行态那一维要等 issue **#506**（脚本详情页「最近 30 天被哪些项目使用 /
各自成功率」，接口 `GET /api/v1/scripts/{name}/usage?days=30`）。#506 目前
open 且未实现，它自己还挂着一个前置依赖：生产 `plan_run.project_id` 归属
分布曾为 LEGACY 96 / NULL 35 / 真实项目 1，归属推断（#540 已合）之前该页
近乎全空。

**两个维度都为零** —— 配置零引用 且 近期零执行 —— 退役才够稳妥。只看
任一边都会误判：只看 refs 会退役掉低频但仍在用的版本；只看运行频率会
退役掉刚配好还没跑的版本。

## 何时重议

- 若退役批量落地后，需要「引用检查自动审计」成为常态门禁（如 scan 时
  提示可退役版本），再议 CI 形态与自动关闭窗口。
- **#506 落地后**：本工具的 `refs` 与 #506 的 `run_count` 可以合成单一
  退役判据，届时应把两段口径合并进一个命令或页面，别让运维在两处对比。
  合并前先确认两边的 `script_version` 口径一致（#506 的 `versions_used`
  是运行时实际用的版本，与本工具的 `plan_step.script_version` 未必相同）。
