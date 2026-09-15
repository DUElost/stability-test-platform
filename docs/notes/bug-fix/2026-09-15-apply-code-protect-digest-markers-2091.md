# apply-code 保护部署态元数据（#2091）

Status: implemented
Class: bug-fix

## Decision

`apply-code` 的 rsync 带 `--delete --delete-excluded`，接收端未在 filter 中出现的
文件会被删除。`VERSION` / `ARTIFACT_DIGEST` / `ARTIFACT_DIGEST_RESOURCES` 既不在
暂存树、也不在保护清单 → **code-only 收敛会把三个记号删掉**；其中 resources 记号
只在资源层运行段内写入，故本轮资源层未运行时无人重写 → **文件与主机列分叉**
（列因「心跳空值不覆盖」保留旧值）。

修法（issue 方案 ①，与 #1248/#1950 protect 语义同族）：

1. **wrapper**：新增 `PROTECT_ONLY_METADATA = ["VERSION", "ARTIFACT_DIGEST",
   "ARTIFACT_DIGEST_RESOURCES"]`，以 `--filter=protect <name>` 注入（**不 exclude**
   ——`--delete-excluded` 下 exclude 等于「显式删除」）；同时把 filter 组装抽成纯函数
   `build_apply_code_filters()` 便于单测锁定保护面（`cmd_apply_code` 行为不变）；
2. **legacy 分支**（`host_updater._REMOTE_SCRIPT` 的 `sudo rsync -av --delete`）：
   补 `--exclude='VERSION' / 'ARTIFACT_DIGEST' / 'ARTIFACT_DIGEST_RESOURCES'`
   （该分支没有 `--delete-excluded`，exclude 即保护）。

未选 issue 方案 ②（无条件补写）：把「写 digest」与「层是否运行」解耦会在半程失败
等路径上引入「写入过期值」的风险，保护语义更窄更稳。

## Alternatives

- **加入 `FIXED_EXCLUDES`**：`--delete-excluded` 下会**删除**这些文件，方向相反 →
  否决；
- **方案 ② 无条件补写**：需要保证只写当前 desired 值且不覆盖更新值，复杂且与
  「写=收敛成功」的既有语义冲突 → 否决；
- **只保护 ARTIFACT_DIGEST_RESOURCES**：`VERSION`/`ARTIFACT_DIGEST` 同族同暴露面
  （code 层失败在写 VERSION 之前时同样丢），一并保护、口径统一。

## Verification

- 新增 `tests/test_agent_priv_apply_code_protection.py`（根 tests，PR 路径执行）→ **3 passed**：
  ① filter 面断言（三个 protect 存在且**不得**为 exclude）；② **真实 rsync 功能测试**
  （元数据存活 + 普通 `--delete` 语义不回归 + `resources/` protect 保持）；
  ③ legacy 分支远端脚本含三个 `--exclude=`；
- **反向验证**：用修复前 filter 集跑同一 rsync（`--delete --delete-excluded`）→
  `['VERSION', 'ARTIFACT_DIGEST', 'ARTIFACT_DIGEST_RESOURCES']` **三个全被删除**
  （#2091 可复现 ✓），修复后同场景全存活；
- `python scripts/run_gates.py check:quick` → 见 PR
- **pending（运维步骤，不在本单）**：wrapper 需 `update_agent.yml` 铺到 14 台纳管主机
  （legacy 分支修复随控制面重启生效）；铺开后再跑一次灰度复现（force 两层 → 紧随
  code-only 收敛 → 记号应存活）。

## Revisit

- 若后续再新增部署态元数据文件（如新的 digest 类型），必须同步进
  `PROTECT_ONLY_METADATA` 与 legacy exclude 列表——测试会因「filter 面不含新名字」
  不自动报错，故新增时按本条登记；
- 若 P2 后续把「文件为真源」纳入（ADR-0040 §7-3 低频校验），本保护是前提条件。
