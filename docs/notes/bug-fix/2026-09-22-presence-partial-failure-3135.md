# script-presence：部分核验失败不得塌成整片 unknown

Status: implemented
Class: bug-fix

## Decision

`classify_host_presence` 把 `verify_ok=False` 一律按「RPC 不可用」处理——丢弃 agent 已经回报的
**逐条结果**，把所有可达条目写成 `unknown` + 一个笼统错误码。但 `verify_ok=False` 覆盖两种
完全不同的情形：

① **RPC 整体不可用**（`agent_offline` / `rpc_failed` / `verify_exception`，`results` 为空）
   → 没有逐条数据，整片 unknown 是对的（未知不是绿）；
② **RPC 成功、核验发现有失败**（`results` 非空）→ 逐条数据在手，却同样被塌成 unknown。

2026-09-22 在生产上撞到 ② 的后果（触发路径：把 plan 57 的 `clear_recents` 重指到 v1.0.4，
该版本只在部分主机上）：

- 对一台未下发新载荷的主机做单机账本刷新 → `rows=50`，`counts={unknown:28, n_a:22, present:0}`；
  目标版本记 `state=unknown, detail=sha_mismatch`；
- 同刻用只读探针把该机 `scripts/**` 全量 sha256 与库中 `content_sha256` + `support_files_manifest`
  对拍：**mismatch=0**（在位文件逐一相符），真实情况是 `clear_recents/v1.0.4/` 的两个文件**不存在**。

即：**1 个文件缺失 → 28 个可达条目全 unknown**（27 个无辜），而真正缺失的那条既没被判
`missing`，detail 还写着 `sha_mismatch`。缺口面（`missing`/`mismatch` → `hosts_with_gap` 与
告警口径）在最需要它的情形下**永不产生**。

改动只在账本侧：`verify_ok=False` 且 `verify_entries` 非空时按逐条判（该条 ok=present、
缺文件=missing、其余=mismatch）；**只有该 key 没有条目**时才 unknown + `not_reported`；
`results` 为空的真不可达路径**行为不变**。

**刻意不改 `verify_one_host` 的错误码**：那句 `"sha_mismatch"` 是**载荷性分类 token**——
`admission_pump._verify_scripts_phase` 用 `err != "sha_mismatch"` 区分「可自愈（推脚本）」与
其它失败。它同时充当「缺失/不符」的笼统码（实测该主机缺文件也返回它），改名或细分会改派发
自愈语义，属另一单的射程（见 Revisit）。

## Alternatives

- **改 `verify_one_host` 让错误码分清 missing/mismatch**：本轮否决。它被派发期当分类 token 用，
  改名即改自愈行为；`file_missing_or_unreadable` 这类**逐条**错误码本来就在 `results` 里，
  账本侧修好即可拿到，无需动派发侧。已在 `verify_one_host` 的语义上留 Revisit。
- **按条目缺失就判 missing（把 `not_reported` 也当缺）**：否决。老 agent / 部分上报时那是猜，
  会制造假缺口——现有 `not_reported → unknown` 的口径是对的，本次只改「有数据却不用」。
- **只在文档里提醒操作者「unknown 可能就是缺」**：否决。那是把可机器判定的事实推给人肉对拍
  （本次现场就是靠手工全量 sha 对拍才定位的），且 `hosts_with_gap` 会继续漏报。
- **顺带把 28 个条目全记 mismatch**：否决。方向反了——`present` 是事实，不该为了让告警好看
  而牺牲准确度。

## Verification

- 新增 4 条单测（`backend/tests/services/test_script_presence.py`）：
  ① 部分失败时逐条判（无辜条目 `present`、缺文件 `missing`）且 `hosts_with_gap` 计 1；
  ② 有逐条结果但某条未回报 → 该条 `unknown/not_reported`，其余照判；
  ③ 维护窗内逐条判出的缺口改记 `maintenance`、`present` 仍是 `present`；
  ④ 端到端 `run_sweep`：`gather_verify` 回 `(False, entries, "sha_mismatch")` 时落库为
     `present` + `missing`（`unknown=0`、`missing=1`、`hosts_with_gap=1`）——正是生产现场那条路径。
- 反向自证：既有的 `test_classify_rpc_failure_is_unknown_not_green`（`verify_ok=False` +
  `entries=[]`）保持绿 ⇒ 真不可达路径未被放宽。
- `python -m pytest backend/tests/services/test_script_presence.py -q` → 14 passed
  （含 #3111 的 3 条）。
- 现场真值（只读 ansible 探针，非本 PR 改动）：该主机 `scripts/**` 375 个文件逐一 sha256
  与库中期望对拍 mismatch=0，缺失 6 项（含 `clear_recents/v1.0.4/` 两个文件与 4 个
  `gpu_setup/*/_gpu_stress_loop.sh`——后者属未下发版本，另见 #3111/#3112 的「下发才算生效」）。
- `python scripts/run_gates.py check:quick`（见 PR 说明）。

## Revisit

- **`verify_one_host` 的错误码语义**：`"sha_mismatch"` 现在同时表示「内容不符」与「文件缺失」，
  而派发自愈按它分流。若将来要细分（例如只对内容不符走推脚本、缺文件走别路），必须与
  `admission_pump` 的自愈分支同批改并补齐派发侧测试。
- **`unknown` 的可操作性**：本次让「有数据」的 unknown 归位后，剩下的 unknown 只剩真不可达与
  未回报两类——若后续观察到大批量 unknown 仍难以归因，可考虑在 sweep 日志里带上
  `verify_error` 的分布（本轮未做，避免日志放大）。
- **新版本下发与重指的耦合**：本次现场是「重指先于下发」暴露的（fail-closed 而非静默）。契约已写
  下发在重指之前（`docs/development/script-versioning.md` 控制面侧第 4/5 步），但**没有任何机器判据**
  阻止「重指到一个尚未全量下发的版本」——若要加，落点应在 `PUT /plans/{id}` 的校验里（需要
  主机侧到位数据，成本不低）。
