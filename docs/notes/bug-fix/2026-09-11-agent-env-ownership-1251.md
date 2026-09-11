# 独立安装 .env 归属 agent 用户（#1251 / R14-F05）

Status: implemented
Class: bug-fix

## Decision

本质问题：`install_agent.sh` 以 root 运行，`.env` 在安装目录整体 `chown -R`
（`backend/agent/install_agent.sh:191`）**之后**才创建/更新（第 7 步）；出口处
只做了 `chmod 640`（原 `:380`）。独立安装路径下 `.env` 保持 `root:root` + 640
→ systemd（root）可读，但 Agent 进程以 android 运行时 `load_dotenv()` 不可读，
启动加载配置失败。Ansible 路径后续有属主修复可规避，独立安装路径没有。

修复 = `.env` 在模式修正后**显式归属 agent 用户/组**（幂等；对「新建」「已存在
则 sed 更新」两条分支统一生效）：

```bash
chmod 640 "$INSTALL_DIR/.env"
chown "$USER:$GROUP" "$INSTALL_DIR/.env"
```

## Alternatives

- **把 `chown -R` 整体挪到 .env 创建之后**——放弃：安装目录其余 root 写入产物的
  时序更难推理；单点补 chown 最小且幂等；
- **改用 `install -o "$USER" -g "$GROUP" -m 640` 创建 .env**——放弃：第 7 步含
  「已存在则 sed 更新」分支，统一在出口修正覆盖两种路径；
- **依赖 Ansible / 热更新修复属主**——放弃：独立安装（文档主路径之一）没有该
  步骤，正是本 issue 的缺口。

## Verification

实际运行（worktree `/tmp/stp-1251`，基于 `origin/main`）：

- `pytest tests/test_install_agent_artifacts.py -v` → **11 passed**（新增 1 例：
  断言 `.env` 的 chown 出现在 chmod 640 之后）；
- **反向验证**：临时移除 chown 行 → 新测试失败（1 failed / 10 passed），确认
  测试可捕获回归；已恢复并复跑 11 passed；
- `bash -n backend/agent/install_agent.sh` → 通过；
- `check:quick` → 7 gates 全绿（ruff / eslint / tsc / knip / compileall / gov-surface / ai-work）。

未完成（pending）：

- 验收真机侧：隔离 VM 干净安装后 `sudo -u android test -r .env` + 服务启动加载
  验证——本机为生产控制面宿主，不做破坏性安装；需隔离环境执行。

## Revisit

- 若 Agent 未来改为 root 读 .env（仅 EnvironmentFile）运行，本 chown 无害（属主
  修正幂等）；
- `install_agent.sh` 若新增其他 root 创建的用户级文件，按同一模式在出口修正属主。
