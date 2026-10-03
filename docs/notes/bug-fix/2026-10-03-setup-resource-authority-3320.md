# 四个专项 setup 的 APK 资源 authority 与脚本包位置解耦（B4/G1a，#3320）

Status: implemented
Class: bug-fix

## Decision

- 修复 #3320 的**可调用消费链**（#3601 §1.4 A01/A04/A07/A10）：`mtbf_setup`、
  `gpu_setup`、`powercycle_setup`、`sleep_setup` 四族 `_lib.py` 的 APK 资源根解析，
  不再用脚本包位置推导 host-local 资源（旧表达式
  `Path(__file__).resolve().parents[3] / "resources" / <family>`：开发态落到
  `backend/resources/...`；包态 `<cache>/<family>/<version>/_lib.py` 落到 tools_cache
  祖先——自定义 `STP_TOOLS_CACHE_ROOT` 时随 cache 移动）。
- 最终 authority contract（C1）：**非空 param > 非空 family env >
  `config.AGENT_DIR/resources/<family>`**。默认分支惰性 `from config import AGENT_DIR`
  （引擎把 Agent 代码根注入脚本 PYTHONPATH——`pipeline_engine.py` 的既有契约），
  取不到时显式 `RuntimeError`，不另猜路径；override（param/env）分支不调用默认锚、
  不依赖该导入；空字符串视为未提供；显式 override（含中心存储）按原 Path 语义消费，
  目录不存在也不切换 authority。
- 涉及文件：四族 `_lib.py`（`_default_resources_root` / `resources_dir`）与入口
  docstring 版本注记；`tool_manifest.json` 追加登记 `mtbf_setup@1.4.2`、
  `gpu_setup@1.2.4`、`powercycle_setup@1.2.9`、`sleep_setup@1.0.6`
  （append-only；`check_script_packages.py` 35 个族树等价保持绿）。
- 模板生命周期（script-versioning 正式过渡条款）：三个模板暂留旧 active pin
  （gpu_setup 1.2.3 / powercycle_setup 1.2.8 / sleep_setup 1.0.5），G1a 只登记三条
  临时 `EXCEPTIONS`（`tests/test_pipeline_template_script_pins_2865.py`）；阶段A
  scan active / 包身份 / `--pending-activation` 清账齐全后，由 G1b（#3603）在追 pin
  的同一 PR 中删除。**不改三个 template JSON，不提前引用未激活版本。**
- `backend/agent/DEPLOY.md` 仅同步直接相关 layout 片段：脚本执行单元是 manifest 登记的
  不可变包（Phase 3 后无 `v<version>/` 目录），并列出 `agent/resources/` 下四个专项
  APK 根（带外供应，不随发布/热更新分发）。
- 生效方式：**代码合入 ≠ 生效**；后续仍需 publish → 部署 A → scan/active →
  pending-activation 清账 → G1b 追 pin → 部署 B。本单元不执行也不伪造这些生产动作。

## Alternatives

- 保留 `parents[3]` 并改为按包/开发两态猜层级：包布局已固定为
  `<cache>/<family>/<version>/`，任何深度推导都会把 cache 位置当 authority
  （自定义 cache 下随祖先移动）——正是本缺陷形态，否决。
- 用 `config.RESOURCE_DIR`（`BASE_DIR/resources`）：与 `AGENT_DIR/resources` 是不同
  authority（A22），四专项 APK 实际在 `agent/resources/`（生产 MTBF 主机只存在
  `install/agent/resources/mtbf`），用它会改变 authority 语义，否决。
- 新增 env / 把 APK 打成工具包 / 复制进脚本包：超出 C1 与 ADR-0051 发布模型
  （#3601 §6 明确不做），且会把 host-local 带外供应改成新的分发通道，否决。
- 在四族各写一份按 `STP_AGENT_INSTALL_DIR` 拼接的 helper：安装目录 env 只由引擎注入、
  standalone 可缺，且与「Agent 代码根」在语义上不同，仍属猜测路径，否决。

## Verification

- 新增 `backend/agent/tests/test_setup_resource_authority_3320.py`（35 项）：
  开发态 / 真实 builder 不可变包态（引擎 cwd=包根 + PYTHONPATH=Agent 目录契约）解析；
  安装根外自定义 `STP_TOOLS_CACHE_ROOT` 不移动 authority；param/env/默认三路与空值、
  显式缺目录、中心存储 override；缺 `config` 时 override 仍可解析、无 override 显式失败；
  MTBF 真实 `_run`（fake adb）消费 project + 三个精确 APK + suite/results；
  旧 marker 格式可读且不触碰旧 check state；GPU 包内 companion 以本包根定位；
  PowerCycle/Sleep 精确 `AutoTestTool.apk`；GPU 旧配置形态不变；包身份/成员 == manifest。
- 负向变异自证：测试内每族在隔离副本恢复旧 `parents[3]` 表达式，开发/包两态断言分别
  变红（4 族 × 2 态用例），恢复真实树后转绿；实现期另做仓库级整体变异（四族同时恢复
  旧表达式）→ 新增测试 26 failed / 9 passed（共 35），恢复后 35 passed 且
  `check_script_packages.py` 等价恢复绿。
- 真实旧包交叉验证（站点包源只读，sha 与 manifest 逐一核对一致）：旧
  `mtbf_check@1.2.0` 在 new setup 1.4.2 写出的 marker 共存下 `_run` 成功、per-device
  state 形态不变；旧 `mtbf_finish@1.4.0` 从同一 `{NFS}/mtbf/<project>/runtask.xml`
  取 suite sha、写同一 `results/<run_dir>.json`；旧 setup 1.4.1 ↔ 新 1.4.2 marker
  双向可读；GPU 1.0.10 / 1.2.1 / 1.2.4 对 `/mnt/stp-aee/gpu` override 解析一致。
- 门禁与 CI：`check_script_packages.py`、`check_tool_manifest.py --base origin/main`、
  pin 守卫、`check:quick`、`tests/` 与 `backend/agent/tests`（含新增文件）；六个
  required checks 以当前 PR head 结果为准（见 PR 正文）。

## Revisit

- G1b（#3603）追平三个模板 pin 时必须同 PR 删除三条 EXCEPTIONS；本 note 的临时
  pin/例外事实随之失效。
- G2（#3321）离线 checker 若把 `agent/resources` 写成全局固定答案（而非按 authority
  类型分类），会漏掉 tools_cache 工具根/包内 companion/中心存储 override——以本契约
  为输入重议。
- #3498 的 Agent/Device SDK 分层若改变「Agent 代码根」定义或脚本执行基底，需同步
  重议 C1 的默认锚实现（当前实现依附 `config.AGENT_DIR` 与引擎 PYTHONPATH 注入契约）。
