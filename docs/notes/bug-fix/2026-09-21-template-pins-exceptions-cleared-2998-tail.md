# 模板 pin 例外清单清空（#2998 尾账：三条滞后项随 scan 收口）

Status: implemented
Class: bug-fix

## Decision

**这是一次纯清账，不引入新判据。** #2998 主批（PR #3008）把 16 族 pin 追到磁盘
head，但因当时 `script` 表尚无对应行，留了三条例外（`ensure_root 1.0.1` /
`powercycle_setup 1.2.1` / `gpu_setup 1.2.1`），每条都写了删除条件 =「部署跑过
scan、版本注册激活」。本批核验删除条件已达成并执行删除：

- 事实来源用 #2931 的既有只读视图，不手搓 SQL（避免 #2632 猜 schema 形态）：
  `--pending-activation` 返回**无待激活项**（全部族 head 已在库且 active）；
  再按族 `--name <族> --json` 逐条确认目标版本 `is_active=True`——
  `ensure_root 1.0.2` / `gpu_setup 1.2.2` / `powercycle_setup 1.2.2` 三行都在，
  `plan_step` 引用数为 0（新版本尚无历史引用，符合预期：引用面是第 4 道，归 #3030）。
- 8 处 pin 一次追平（7 个模板文件），EXCEPTIONS 清空为 `{}`。
- **清空不等于拆机制**：例外清单是滞后项唯一的显式出口，拆掉就等于回到
  「要么全族一刀切、要么永久钉死」的两难。判据三断言抽成纯函数
  `_exception_lag_reason()`；理由是新清单为空后原用例变成对空 dict 的空转——
  **跑在空集合上的守卫不算有守卫**，所以补
  `test_exception_shape_predicate_has_teeth` 变异自证判据本身仍会咬人（无理由 /
  空白理由 / 已追平 / 超过 head 四种失效各命中一次，外加"真滞后必须放行"的反向
  用例，防止出口被误焊死）。

## Alternatives

- **等 #3030（plan_step 重指）一并处理**：弃。两批对象不同——本批只管**新建 Plan
  的种子**（模板 pin），#3030 管**既有 `plan_step` 行**；且 #3030 的两轴（追 head
  vs 故意冻结、活跃 vs 历史 Plan）尚未裁决，等它会把这些已无争议的 pin 继续压着，
  而每压一天，新建的 GPU/开关机 Plan 就拿不到 #2802 的撞峰修复。
- **保留例外条目并加注「已生效」**：弃。这正是 AGENTS.md 点名的形态——写清删除
  条件的临时例外在条件达成后不删，就会沉淀成「看起来仍然合法的旧 pin」，下次读
  的人无从判断它是活账还是死账。
- **顺手把 seed 迁移里的旧 pin 也改了**：弃。alembic 版本文件不可变（有守卫），
  且它属于历史 Plan 的形成路径，改它既不影响既有 `plan_step` 也不该影响。

## Verification

- `python3 -m pytest tests/test_pipeline_template_script_pins_2865.py`：5 passed
  （原 4 + 新增牙测试）；
- 变异自证三处，均按预期变红后恢复：
  1. `gpu.json` 的 `gpu_setup` pin 退回 `1.2.1` →
     `test_pinned_template_scripts_track_latest_on_disk` 红（`pin='1.2.1' latest='1.2.2'`）；
  2. 把已追平的 `gpu_setup 1.2.2` 重新登记为例外 →
     `test_exceptions_are_real_lags_with_reasons` 红（守卫拒绝保留）；
  3. `_exception_lag_reason` 首条判据改为恒放行 →
     `test_exception_shape_predicate_has_teeth` 红；
- 库侧事实取自 `backend/scripts/check_unreferenced_script_versions.py`
  （只读 SELECT，生产库；本次会话未执行任何非 SELECT 语句，输出不含凭据）；
- `python3 scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- 第 4 道（既有 `plan_step` 重指）仍无账：模板全绿不代表 44 个既有 Plan 拿到修复，
  且重指会衰减（#3030 里 plan 54 实证）——那是独立一题，不在本批；
- 若后续再有族「磁盘 head 未注册」，照本批反向操作：进 EXCEPTIONS 写 (版本,
  理由+删除条件)，`--pending-activation` 清空后删条目并追 pin；
- 牙测试挂在 `check_device` 的 head 上（只为取一个真实版本串做参照）。若哪天
  `check_device` 目录结构变了导致 `_latest_on_disk` 取不到版本，会先在该用例
  报错——那属于夹具失效、不是判据失效，届时换成 tmp_path 构造的假 head 即可。
