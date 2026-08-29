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

## 何时重议

若退役批量落地后，需要「引用检查自动审计」成为常态门禁（如 scan 时
提示可退役版本），再议 CI 形态与自动关闭窗口。
