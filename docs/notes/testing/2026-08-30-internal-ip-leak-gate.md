# 内网主机地址扫描固化为阻塞门禁（#538 收尾）

Status: implemented
Class: testing

## Decision

把「public 仓库不得出现真实内网主机地址」从**人工清理**升级为**阻塞门禁**。

新增 `tools/dev/check-internal-ip-leak.py`，挂载三处（缺一即被治理门禁拦下）：

| 挂载点 | 形式 |
|---|---|
| `.github/workflows/ci.yml` §lint | step `内网主机地址检查(public 仓库)`，跑 `--check` |
| `scripts/run_gates.py` | gate `ip-leak`，进 `check:pr`（推送前默认） |
| `tools/dev/check_governance_surface.py` | `GATE_TO_CI_ANCHOR["ip-leak"] = ("ci.yml", "内网主机地址检查")` |

第三项是**硬性**的：`check_governance_surface.py` 的 S5x 会双向断言 GATES 与
CI 步骤的配对，本地 gate 若未登记 CI 锚点，`check:quick` 当场红灯。

### 规则边界（关键取舍）

只拦**四段齐全的具体主机地址**（点分 `172.21.15.66` 与 HOST_ID 横杠
`172-21-15-80` 两种写法都覆盖——历史上横杠格式正是被漏掉的那一种）。
以下放行：

- **CIDR 网段常量**（`172.16.0.0/12`）：公开标准网段，不是资产。
  `backend/core/limiter.py` 的注释、`docker-compose.yml` 的 trusted proxies
  都属此类。
- **标准基础设施地址**：`0.0.0.0`、`127.0.0.1`、`255.255.255.255`、
  `172.17.0.1`（Docker 默认 bridge 网关）。
- **白名单路径**（前缀匹配，理由见下）：
  - `backend/agent/scripts/**` —— ADR-0020 规定已发布脚本版本目录**不可变**
    （改写会让 DB sha 与磁盘永久不一致，2026-07-31 生产事故即由此起）；
  - `backend/alembic/versions/**` —— 已锁定迁移不可改；
  - `backend/tests/**`、`tests/**`、`*.test.*` / `*.spec.*` —— 测试夹具，
    构造 IP 是被测逻辑的一部分（如 `test_rate_limiter.py` 用 `172.20.0.4`
    验证 `resolve_client_ip`）。

### 顺带修正的 3 处示例地址

首轮扫描命中 6 处，全部是**示例/占位**而非真实资产，已改用 **RFC 5737
文档保留段**（`192.0.2.0/24`，IETF 专为文档示例保留，不在 RFC1918 内故天然
放行），而非塞进白名单：

- `.claude/skills/agent-host-onboard/SKILL.md` HOST_ID 转换示例；
- `backend/agent/DEPLOY.md` 的 `batch_deploy.sh` 示例主机列表；
- `frontend/src/pages/hosts/components/AddHostModal.tsx` 的输入框 placeholder。

## Alternatives

- **禁止一切 RFC1918 地址**：实现最省事，但会误伤上述三类合法写法，门禁
  第一天就满屏红，进而逼着维护者加一堆白名单或干脆豁免——反而更差。放弃。
- **把 3 处示例地址加白名单**：能最快转绿，但白名单会持续膨胀，且读者无法
  区分「这个 IP 是示例」还是「这是真资产」。改用 RFC 5737 保留段在语义上
  更正确（文档示例本就该用文档地址）。放弃。
- **只做增量扫描（只查 diff 新增行）**：无法拦住「编辑历史文件时塞进新地址」
  这一路径，而那正是历史累积的主要方式。放弃。
- **同时下沉到 `.githooks/pre-commit`**：本地拦截更早，但需开发者手动
  `git config core.hooksPath .githooks` 才生效（空行污染门禁的教训：钩子
  覆盖率不可假设），且本门禁已在 `check:pr` 中，本地推送前即可跑。
  暂不做，见 Revisit。

## Verification

- `python tools/dev/check-internal-ip-leak.py --self-test` → **15/15 通过**
  （拦截 4 例：点分/横杠/10 段/192.168 段；放行 11 例：已脱敏 `x.x` 写法、
  CIDR、Docker 网关、回环与通配、三类白名单路径、版本号 `1.2.3.4.5`
  与 `10.0.31558` 等非地址串）。
- 全仓扫描 → **1579 个文件，0 命中**（基线已绿）。
- **对照实验**（证明门禁真能拦回归）：向 `docs/DOC-MAP.md` 注入
  `172.21.8.202` 与 `172-21-15-80` → exit=1、命中 2 处（两种写法均被拦）；
  `git checkout` 恢复后 exit=0。
- `python tools/dev/check_governance_surface.py --check` → S1–S5、S5x、S7
  全绿（含新增 gate 的 CI 锚点配对断言）。
- `ruff check backend/ tools/ scripts/` → All checks passed。

## Revisit

- 组织若更换内网网段，需同步更新脚本的 DOT / DASH 正则（当前按 RFC1918
  三段全扫，换段通常无需改；若改用非私有段才需动）。
- 白名单应定期审计：ADR-0020 脚本目录与已锁定迁移属「永久不可改」，但测试
  夹具若被发现有真实资产 IP，应就地泛化而非继续白名单。
- 若希望拦截更早，可把该 step 下沉到 `.githooks/pre-commit`——但需同时解决
  钩子的启用覆盖率问题（参考空行污染门禁的三重防线设计）。
- 设备序列号（`docs/acceptance/2026-08-suite-binding-mtbf-signoff.md` 中的
  `AYCGNX6730000054` ×2）**尚未纳入**本门禁——它属设备个体标识而非网络
  地址，脱敏粒度待定，确定后再扩规则。
