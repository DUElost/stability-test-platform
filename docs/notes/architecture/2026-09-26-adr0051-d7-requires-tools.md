# ADR-0051 v1.7 D7：脚本包声明工具依赖（requires_tools）——机制落地（2026-09-26）

Status: implemented（机制；尚无真实工具登记与消费方新版本）
Class: architecture

## Decision

D7 原文只定方向（flashtool/aimonkey 改 `tool_manifest.json` 条目），未定**消费脚本怎样绑定到工具版本**。
owner 2026-09-26 裁决「脚本包声明依赖」，本单落机制（#3288 第 1 片，零生产效果——没有任何族树声明依赖）：

1. **声明**：族树 / 脚本包根 `capabilities.json` 顶层 `requires_tools: {<工具族>: {"version", "env"}}`；
   `env` 限 `*_DIR` 形态、禁引擎自有键（`STP_LOG_DIR` / `STP_AGENT_INSTALL_DIR`）、键不得重复。
   判据单源 `backend/agent/tool_requirements.py`（纯标准库）——Agent 运行时相对导入、仓库门禁按路径加载，
   杜绝「门禁放行、运行时拒绝」的两套口径。
2. **执行**：`pipeline_engine._run_script_action` 在引擎自有 env 键之后调
   `script_packages.inject_required_tools(包根, env)`：经 `tool_cache.resolve_packaged_tool_ref`
   （从 env 引用键版本抽出的 `(name, version)` 入口，展锐/dedup 行为不变）整包核验拉取，把**包根**写进本步 env。
   **fail-closed**：声明坏 / 缺包 / 坏 sha / 退役 → 步骤 exit 2（同「script package unavailable」先例），
   **不回退**主机 `.env` 同名键——回退会让「包面坏了」被旧主机路径掩盖。
3. **预热**：`verify_package` 在脚本包核验后同步解析依赖——precheck/presence 把 ~150MB 刷机工具提前拉进
   `tools_cache`，缺失在派发前暴露为该版本不在位。
4. **门禁**：`check_script_packages` 追加——族树声明形态合法且引用指向已登记、`kind=tool`、未退役条目。
5. **清单**：`STP_FLASH_TOOL_DIR` 首次进入 env 清单可见面（真实读点在 `backend/agent/scripts/**`，清单不扫；
   本单端到端测试的子进程夹具让它可见）——按 `STP_SCRIPT_SOURCE`/`PYTHONPATH` 先例声明为内部键
  （引擎写给子进程、运维不配置），`environment-variables.md` 该行终态改写为 v1.7 口径。

## Alternatives

- **主机级包引用键（展锐 Phase 4a 先例）**：`STP_*_PACKAGE_REF` 由控制面渲染到主机——不发脚本新版、最快；
  否决：新增工具私有 env 键（ADR-0033 §5.4 ③）、全 fleet 单一工具版本且不进脚本身份、env 变更还受
  #3356（渲染 env 不在收敛判据、删键不下发）所限。
- **声明放进 `tool_manifest.json` 脚本条目**：会动 C5 版本六元组（不可变字段集）；包内 `capabilities.json`
  本就随包内容寻址，零 schema 变更。
- **注入统一键 `STP_TOOL_<NAME>_DIR`**：需要改消费脚本读键；沿用既有键名让 flash/monkey 新版本只改声明。

## Verification

- 新增 `backend/agent/tests/test_required_tools_d7.py` **27 passed**：判据 14 类非法形态、坏文件 fail-closed、
  注入成功（包根 + `.stp-verified`）、三类失败（缺条目 / sha 不符 / 退役）**且主机同名诱饵目录不被采用**、
  verify 预热与缺失上报、引擎端到端（子进程读到包根覆盖主机旧值；缺工具时脚本子进程**不启动**、exit 2）
- **变异自证**：去掉引擎注入 → 2 红；去掉 verify 预热 → 2 红；失败改静默放行 → 5 红；门禁判据掏空 → self-test 6 红
- 回归：agent 包/工具缓存/引擎/展锐/verifier 相关 201 passed；`tests/` 边界守卫 + phase2a + 删键台账 31 passed；
  `check_script_packages` 真树 35 族绿；`check:quick` 17 gates OK；agent 全量套件结果见 PR

## Revisit

- #3288 后续片：② flashtool/aimonkey 以 `kind=tool` 登记并发布到站点包根；③ flash_firmware/flash_preflight/
  monkey 族新版本声明依赖 + 真机刷机窗口取证 + 计划重指；④ 删 `STP_FLASH_TOOL_DIR` hot-update 注入与
  host-resources 通道（删键下发依赖 #3356 或显式 `--force` + 残留说明）。
- 门禁只看族树（最新版本）：退役 tool 版本前须人工确认无 active 老脚本版本依赖（运行时 exit 2 兜底）。
  若出现此类事故，考虑 scan 时把包内依赖回填到 `script` 行（控制面可查全量依赖图）。
