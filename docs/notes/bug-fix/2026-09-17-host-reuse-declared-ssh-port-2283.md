# 复用 Host 路径不得丢弃声明端口（#2283 残余）

Status: implemented
Class: bug-fix

## Decision

#2283 的修复把 `ansible_port`（inventory）接到了 **Host 创建**路径（`_create_payload`），
但**复用**路径没跟上：`_reuse_host` 只校验 `retired_at` 与 `name`，而真实安装用的是
**Host 行**的端口（`backend/services/agent_installer.py` `port = host.ssh_port or 22`）。
于是「改大 `ansible_port` 后重跑」「先在 hosts 页建行再跑安装」这些常规路径仍会
静默丢弃声明端口，安装打到错误端口——症状与本单原始问题同形。

修法取 issue 验收给出的第二条出口：**显式判否**，不替操作员改行。

- 新增 `_host_ssh_port()`：读 Host 行的 `ssh_port`（API 可能回 `int` 或数字字符串，
  缺失/非法按 22——与 `agent_installer` 的 `host.ssh_port or 22` 同语义）；
- `_reuse_host()` 增加 `ssh_port` 形参并与行值比对，不一致返回
  `_fail("install.s5.host", "host_ssh_port_mismatch")`（remediation 说明两条对齐路径）；
- 两个复用调用点（正常命中 + 409 竞态后重查命中）都传入声明端口；
- `checks.MESSAGES` 增 `host_ssh_port_mismatch`（该表是 code → remediation 的封闭
  词表，新增码必须同时登记，否则 `failure()` 会回落成 `invalid_value`）。

## Alternatives

- **复用命中时 `PATCH /hosts/{id}` 把端口改成声明值**：否决。`ApiClient` 协议里没有
  更新方法，加它要同时扩 `HttpApiClient` 与站点 API 的写入面（本单是 fail-closed 的
  残余收口，不该顺手扩写权限）；且端口可能与该 Host 的其他用途/其他站点约定绑定，
  「改行」是需要人确认的动作。登记为 Revisit。
- **只在 `name` 不符时失败，端口不一致降级为 WARN**：否决。站点工具没有 WARN 档
  （`Check.status ∈ {PASS, FAIL, BLOCKED}`），且这正是本单要消除的「静默通过」形态。
- **复用时不比对、安装前由 `agent_installer` 兜底**：否决。安装侧读的就是 Host 行值，
  它无从知道声明——声明只在站点工具这一侧可见，不在这里比对就永远丢了。

## Verification

- 新增两条用例（`tests/test_site_agents.py`，用合成站点 + FakeApi，不发网络/不碰真机）：
  - `test_existing_host_with_different_ssh_port_fails_closed`：声明 2222 / 行 22 →
    `install.s5.host` **FAIL** 且 code 为 `host_ssh_port_mismatch`（复用不创建）；
  - `test_existing_host_with_matching_ssh_port_is_reused`：声明 2222 / 行 2222 →
    照常 **PASS**（判据不得误报）。
  既有 `test_existing_host_is_reused_without_creating`（行无 `ssh_port`）在默认声明
  22 下仍 PASS——缺失按 22 的语义与安装侧一致。
- **反例构造（先证伪再采信）**：把比对条件改成恒假（`if False:`）→
  `test_existing_host_with_different_ssh_port_fails_closed` **FAILED**
  （状态是 PASS）；恢复实现后 3 条用例全绿。
- 实测命令与结果：
  - `TESTING=1 python -m pytest tests/test_site_agents.py -q` → **85 passed**；
  - `TESTING=1 python -m pytest tests/ -q` → 1347 passed, 1 failed：失败项仍是
    `test_script_seed_governance.py::test_new_seed_migrations_deactivating_versions_check_references`
    （**主线既有红灯**，已在干净 `origin/main` 复现，与本单无关）；
  - `python -m ruff check`（改动文件）→ All checks passed；
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**。

## Revisit

- **`ssh_port` 的收敛方向**：当前出口是「报告不一致，人工对齐」。若站点工具日后拿到
  Host 写入权限（`ApiClient` 增 `update_host`），可改为「声明驱动」——复用命中且端口
  不符时 `PATCH` 行值并在报告里注明改动。届时需同时决定：Host 行的端口被谁视为权威
  （本单的前提是「安装读行值」，若改成写行值，前提变化需同步 agent_installer 的注释）。
- **同类字段仍有别的漂移面**：`_reuse_host` 现在只比对 `name` 与 `ssh_port`；
  `ssh_user` / 凭据类型（key vs password）与声明不一致时同样不被发现（安装走
  ansible 侧凭据，影响较小）。若出现现场，按本单同一形态扩比对，不要各自为政。
- **`checks.MESSAGES` 的封闭词表**：新增失败码必须同时登记，否则静默回落
  `invalid_value`（本单实测过：漏登记时报告只写 "Configuration rejected."）。可考虑
  加一条「`_fail` 的 code 必须存在于 MESSAGES」的静态守卫。
