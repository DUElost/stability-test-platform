# agentctl health 严格消费 /health readiness（#1254 / R14-F08）

Status: implemented
Class: bug-fix

## Decision

本质问题：`agentctl health` 的服务器连接检查是 `curl -fsS /health || curl -fsS /`
（`backend/agent/agentctl.sh:18-19`）。`/health` 是控制面 readiness（DB/Redis/SAQ
依赖），`/` 是前端首页——依赖不可用时 `/health` 503 而 `/` 仍 200，旧逻辑据此
判定"正常"；经 Nginx 时首页请求甚至可能只打到前端，升级健康检查误判。

修复 = **只消费 `/health` 的退出码**：删除首页兜底，`check_server_connection`
单次 curl + `-fsS`，失败即非零；`health_check` 其余逻辑与输出文案不动
（"服务器连接: 无法连接" 语义仍成立——连接层面确实不可用）。

## Alternatives

- **保留首页兜底、仅降级为 WARN**——放弃：Ansible 升级链消费的是 `agentctl health`
  返回码（`docs/linux-agent-ansible-runbook.md:270`），WARN 拦不住升级；
- **首页与 /health 都成功才通过**——放弃：首页不是 readiness 信号，经 Nginx 时
  前端可用会引入假阳性，且比现状更严格无收益；
- **解析 /health 响应体区分失败项（jq）**——放弃：超本单范围；需要更细语义时
  另行设计（见 Revisit）；
- **同步改输出文案为"readiness 失败"**——放弃：现有契约测试断言原文案，
  语义无误导性，改动只扩散不增效。

## Verification

实际运行（worktree `/tmp/stp-1254`，基于 `origin/main@e3e30f38`）：

- `pytest tests/test_agentctl_contract.py -v` → **6 passed**（新增 3 例：
  `/health` 503 + 首页 200 时 `check_server_connection` 必须非零；`/health` 200
  时必须零；函数体单 curl 的防回归静态断言）；
- **反向验证**：临时恢复首页兜底 → 新增 2 例失败（运行时 + 静态断言），确认
  测试可捕获该回归；已恢复为修复版本；
- `bash -n backend/agent/agentctl.sh` → 语法通过；
- `check:quick` → **7 gates 全绿**（ruff / eslint / tsc / knip / compileall /
  gov-surface / ai-work）。

**补充验收（2026-09-13，隔离容器 + 真实 Nginx 反代）**——原 pending 第 1 条：

拓扑（docker 自定义网络隔离，127.0.0.1 高位端口，全程不触生产）：
`nginx 反代 → stub-home（恒 200）+ stub-health（503）`，另起一组全 200 作对照。
nginx 用本地 `stability-frontend` 镜像内的真实 nginx（1.31.5），conf 要点：

```nginx
location = /health { proxy_pass http://stub-health:8002; }  # 依赖故障 → 503
location /        { proxy_pass http://stub-home:8001; }     # 首页仍 200
```

调用真实函数（与契约测试同款 source 方式，剥离入口 `main`）：

```bash
bash -c '. "$1"; check_server_connection "$2"; echo "rc=$?"' -- agentctl.sh "$URL"
```

结果矩阵（经真实 Nginx 反代）：

| 场景 | 新版（修复） | 旧版（pre-fix，`4f05c9c7^`） |
|---|---|---|
| `/health`=503 + `/`=200 | `check_server_connection` **rc=22（失败）**；`health_check` 输出「服务器连接: 无法连接」 | **rc=0**；`health_check` 输出「服务器连接: 正常」（被首页 200 骗过，复现原缺陷） |
| 两者皆 200 | rc=0；「正常」 | rc=0；「正常」 |

结论：修复在真实反代拓扑下端到端成立——旧版在依赖故障时误判为正常、新版正确
失败；Ansible 升级链消费的 `agentctl health` 返回码在依赖故障时非零，拦得住。
容器与网络已全部拆除（无残留）。

未完成（pending）：

- 无（原「真实 Nginx 反代端到端复现」已由上述隔离容器实测补齐）；
- 隔离 VM 安装链验证不涉及本单（F08 验收无该要求）。

## Revisit

- 若出现「控制面 readiness 失败但 Agent 必须继续运行」的诉求，正确落点是 Agent
  侧重试/退避策略，而不是放宽 health 检查；
- 若 `agentctl health` 需要区分 DB/Redis/SAQ 具体哪一项失败，再引入响应体解析
  并同步升级 Ansible 消费语义。
