# Ansible 轨道归位：排除集契约对齐 + 双 digest 簿记（#1997）

Status: implemented
Class: feature

## Decision

落地 ADR-0040 §5-3 P2 尾项——Ansible 轨道与协议身份对齐（#1975 分层流的
簿记闭环）：

1. **排除集契约对齐**（`agent_deploy/defaults/main.yml`）：
   - 测试模式三条窄模式（`test_agent*/aimonkey*/main*`）→ `test_*.py` 宽
     模式（与热更新打包/digest 输入集同规则，消除载荷分叉）；
   - 补 `venv/`、`logs/`（宿主侧目录，源树出现时也不同步）；
   - `agent_host_local_paths` 增补 `ARTIFACT_DIGEST` /
     `ARTIFACT_DIGEST_RESOURCES`（exclude+protect）——两者不入 git，rsync
     `--delete` 会清掉主机身份文件 → 心跳丢 current → 控制面误判 drift
     多做一次全量部署。
2. **双 digest 计算**：`tools/ansible/compute_deploy_digest.py`——stdlib
   only（importlib 直接加载 `backend/agent/artifact_digest.py` 镜像算法，
   **不触 backend.core 导入链**，无 DATABASE_URL/settings 依赖，控制机
   系统 python3 可跑）；从 playbook 的 checkout 现算 code + resources 两
   身份（schema 以 `stp_schemas/` arcname 计入，与控制面同基）。resources
   分区为空（大件不入 git）→ 打印空值，playbook 据此跳过写入——#1975
   空集守卫的对偶，绝不下发空载荷身份。
3. **远端写入**（`update_agent.yml`，health 验证通过后、rescue 之前）：
   become copy 写 `ARTIFACT_DIGEST` / `ARTIFACT_DIGEST_RESOURCES`（空值
   不写；rsync 未跑不写）。rescue/回滚路径天然不写——失败保持旧身份 →
   控制面按 drift 重新收敛，与热更新「探活通过才写」语义一致（#1943 类
   滞后就此闭合：Ansible 更新后主机立即上报双 current）。
4. docs/operations/agent-version-and-hot-update.md：通道簿记说明 + 排障
   表新增「Ansible 更新后仍 drift 一轮」行。

## Alternatives

- 控制机脚本复用 `backend/services/artifact_digest.py`——其 import 链经
  `backend.core`（DATABASE_URL/settings 硬依赖），playbook 环境不可满足；
  Agent 镜像模块 stdlib-only 且 parity 由既有测试锁定，是唯一干净载体。
- digest 由控制面 API 下发而非本地现算——Ansible 场景（灾难修复）可能
  控制面恰不可用，自足计算更稳。
- 不写 resources digest（只写 code）——Ansible 的 rsync 实际覆盖
  resources/**（除 mtbf），身份与覆盖面对齐才不产生假 drift。

## Verification

- `tests/test_ansible_digest_contract.py` 5 passed：compute 脚本输出与
  控制面 services digest 字节级等价（fixture 树 subprocess 对照，两 kind）、
  空 resources 打印空值、排除集契约断言（宽模式/venv,logs/双身份文件
  exclude+protect/旧窄模式禁回流）、playbook 任务序（health < compute <
  双写 < rescue）与守卫（空值不写、rsync 未跑不写）。
- `ansible-playbook --syntax-check update_agent.yml` 过（本机 ansible
  binary）。
- 相邻面 51 passed（digest 分层 parity / host_updater 分层脚本 / #1943
  心跳重读）。
- `check:quick` 10 gates 全绿。

## Revisit

- **灰度验收（ADR §6）需运维窗口**：1 台 → 5 台 → 全量 + fleet
  `duration_ms` 复核 + no-op 稳态出现率（连续两轮批量出现
  `converged(reason=digest-matched)`）+ 存量机 wrapper 升级确认
  （`write-digest --kind` / `apply-resources` 均依赖新 wrapper；旧 wrapper
  主机 resources 层回退 legacy，安全方向）。ADR-0040 验收至此仅剩此项。
- playbook 的 digest 写入路径无真机端到端（本片验证止于 syntax-check +
  契约测试）——首次 fleet 灰度时按排障表新行核对。
