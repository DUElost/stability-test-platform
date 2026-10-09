# Harness 行为探针

本页是 [#3516 G2](https://github.com/DUElost/stability-test-platform/issues/3516) 的操作入口。
使用当前 worktree 项目解释器运行 `tools/dev/harness_probe.py`；真实会话是手动/低频探针，
不进入 quick、pr 或 full。`check:gov` 的既有 harness-ingest 调用默认完整 contract 矩阵。

## 两种实验

- `--mode contract`（默认）：允许按**已加载契约**的祖先指针，只读获取 AGENTS/CLAUDE；
  验证完成启动协议后 root/scoped 可见。不能把指针存在或 CLI 成功当作 IDE 继承证据。
- `--mode autoload`：禁止文件读取/工具调用，只诊断初始上下文。
  CodeBuddy IDE/Zcode 深 cwd 的历史 root=false 仅是诊断基线，不是 contract 验收标准；
  其他尚无历史证据的单元是显式目标预期，不能当作已观测值。版本变化导致偏离时需人工解释。

Q1 检查根契约的两处标题。**Q2 问什么由模式决定——两种实验测的不是一件事**：

| 模式 | root | agent | aee |
|---|---|---|---|
| `autoload`（初始上下文诊断） | Agent 层标题（预期不存在，阴性对照） | Agent 层标题 | **AEE 层标题** |
| `contract`（祖先继承验收） | 同上 | Agent 层标题 | **Agent + AEE 两层都要** |

两个标记分属不同文件：`Agent 侧 scan / upload` 在 `backend/agent/AGENTS.md`、
`AEE crash detection chain` 在 `backend/agent/aee/AGENTS.md`。

**autoload 的 aee 格只问本层**：workspace-only IDE 只注入 workspace 那份契约，同时问
两层会让该格对这类形态不可满足，FAIL 便无法区分形态漂移与题目缺陷（#3563 实测
autoload Q2=否 即属此类）。

**contract 的 aee 格必须问完整链**：`backend/agent/aee/AGENTS.md` 自身要求按
root → Agent → AEE 逐层加载，**漏掉中间 Agent 层就是断链**。若此处也只问 AEE 单个
标题，则「root + AEE 可见、Agent 层缺失」的会话会答 `Q1=是 Q2=是` 并被判 PASS——
探针反而看不见它要抓的缺陷（#3585 复核反例）。`agent` 格的独立结论**不能**替代 aee 格
对自身完整继承链的证明。

Q3 已删除，不再声称检测重复加载。
每个最终答复必须完整匹配两行 `Q1=是/否`、`Q2=是/否` 的实际选择值；不能带选项斜线、
题目、角色前缀、解释或日志。Claude/Codex 只从支持的最终事件取答复，其他 CLI 的 stdout
必须整体匹配；未知输出协议为 UNVERIFIED，不猜测。标题自报告只是最小黑盒可见性证据，
不证明所有约束被遵守，更不证明 hook 触发。IDE 取证还须人工确认读取路径与无工具错误。

## 运行与状态

```bash
.venv/bin/python tools/dev/harness_probe.py --self-test
.venv/bin/python tools/dev/harness_probe.py --only codex --cwd root,agent,aee --mode contract --timeout 90 --json /tmp/codex-contract.json
.venv/bin/python tools/dev/harness_probe.py --only claude-subdir-plain --cwd agent --mode autoload --timeout 90 --json /tmp/claude-autoload.json
```

- PASS：有效最终答复符合当前矩阵预期。
- FAIL：有效答复偏离预期；autoload 的 FAIL 表示加载漂移，不能直接解释为继承违规。
- UNVERIFIED：未跑/缺证据、非零退出、超时、工具错误、stderr 诊断、协议异常、乱码或题目回显。
  诊断需要本地检查，不从 stderr 或工具输出抽答案。未取得版本也不能算验收通过。
- 退出码：完整所选矩阵 PASS 为 0；有 FAIL 为 2；否则有 UNVERIFIED 为 1。
  FAIL 与 UNVERIFIED 混合时返回 2，逐行状态仍保留；没有“不可验证但 exit 0”的默认路径。

### UNVERIFIED 原因码

探针拒绝一个答复时，报告里的 `error` 在固定前缀 `no valid final answer or protocol/tool error`
之后附一个方括号原因码。JSON 协议形态（Claude / Codex）进程非零退出时先写 `exit=N`；**仅当 stdout
还能读出流级拒绝原因**才在其后附原因码——stdout 是协议有效的最终答复、没有流级原因时仍只写 `exit=N`，
纯文本形态恒为 `exit=N`。非零退出本身就足以解释 UNVERIFIED，`exit=N` 后没有方括号码不是诊断缺失。
原因码**只解释为什么不可判，不改变任何判定**，也不含原始输出（探针本身不保存 stdout）。判定与原因出自
同一段读取代码，不会漂移。其它 UNVERIFIED 来源（`timeout`、`stderr diagnostics; inspect locally`、
`version unavailable`、`not-runnable: …`、人工证据类）的 `error` 文本不变，也不带原因码。

| 原因码 | 含义 |
|---|---|
| `transport-retry(api_retry xN)` | 流里出现了 N 个 `system/api_retry` 事件，**仅此而已**：不证明答案有效，不证明故障是瞬时的，也不证明重试已恢复或已耗尽。该事件自带非空 `error` 字段，探针据此把有它的流判为出错，因此其它缺陷（缺最终 `result`、`result` 非 success、答案格式不对、多个 `result`）不再单独列出，401 这类持续性错误也记同一个码；与 `result-error` 同现只说明最终 `result` 同时带错误。要下结论须手动重放同一命令查看事件流（探针不保存 stdout） |
| `result-error` | 最终 `result` 事件自身带错误 |
| `tool-or-protocol-error` | 流里其它节点带错误：工具结果出错、命令非零退出、`error` / `turn.failed` 事件等 |
| `unparseable-stream` | 输出为空、含非 JSON 行、非对象事件或嵌套过深 |
| `no-single-final-result` | Claude：不是恰好一个 `result`，或它不是最后一个事件 |
| `result-not-success` / `result-not-text` | Claude：`result` 不是 success / 不是文本 |
| `turn-not-completed` / `no-agent-message` | Codex：回合未完成 / 没有 agent 消息 |
| `answer-format` | 取到了答复，但不是完整的两行 `Q1=…` / `Q2=…` |
| `unsupported-protocol` | 形态登记了未知输出协议 |

多个原因用 `+` 连接，顺序固定：`transport-retry`、`result-error`、`tool-or-protocol-error`。

### 重试纪律

上游不稳、限流、模型渠道临时不可用等宿主 / 服务的瞬时故障会让格子 UNVERIFIED。允许重试，但：

1. 只重试因**宿主 / 服务瞬时原因**而 UNVERIFIED 的格——这由操作者依据证据判断，不能只凭原因码；
   已得出 PASS 或 FAIL 的格**一律不重试**，否则就是在挑结果；
2. 命令行、提示词与判卷不变；不得靠改参数、加信任绕过或指定模型来换结果；
3. **每次尝试都留档**（revision、版本、状态、原因码、耗时）；报告同时写首轮结果和最终结果，不把
   重试后的结果写成首轮；设次数上限，上限内仍不可判就如实报告 UNVERIFIED；
4. 原因码是辅助线索：`transport-retry` 只说明流里有重试事件，**不说明**是上游瞬时问题（401 这类持续性
   错误也记同一个码），更**不构成豁免**；判定仍以探针为准。

### 宿主环境提示

从 Claude Code 会话里运行探针，子进程会继承该会话的 `CLAUDE_CODE_EFFORT_LEVEL`。曾实测：`max` 下一次
极简的 `claude -p` 在 90 秒内都没有返回，`low` 下 6 秒返回。可在**探针进程环境**里设为 `low`（推理强度
不影响上下文装载）并把它记进证据；探针命令行本身不因此改变。

### stderr 证据

stderr 一律判 UNVERIFIED，但判定必须可复核，因此原文落盘到**操作者本地**的
`.probe-evidence/<form>_<cwd>_<mode>.stderr`（该目录已 gitignore）。报表每行只记
`stderr_file` 路径，**不含 stderr 正文，也不含 stdout**。`--stderr-dir ''` 关闭落盘，
`--stderr-dir DIR` 改位置。落盘只是取证，不改变判定：判 UNVERIFIED 的格不会因为
「文件已保存」而变 PASS，良性噪声的豁免是人工裁决并记在 issue 上，工具不提供
豁免开关（避免自查自免）。仅在 `.stderr` 里出现、且各宿主稳定复现的启动噪声
（例如 `codex exec` 恒写的 `Reading additional input from stdin...`）属于宿主基线，
记录后仍按 UNVERIFIED 处理。

`--cwd root,agent,aee` 为三个独立启动目录；每个调用是新 CLI 进程。Claude wrapper 是
后备供给形态，不能代替裸 Claude CLI。Cursor/CodeBuddy CLI 与对应 IDE 不共享结果。
不添加信任绕过参数，不默认激活宿主 hooks；正常工作区信任与服务可用性由操作者确认。

### 已知不可跑形态（Owner 2026-10-01 裁决）

- `cursor` CLI：**结果随宿主条件变化，不再是恒 UNVERIFIED**。命令不含 `--trust`，探针不为此加信任
  绕过参数；宿主无预置工作区信任或服务拒绝时非交互直接 `exit=1` → 该格 UNVERIFIED（Owner
  2026-10-01 裁决接受这类结果，不改判）。2026-10-03 旧账号团队额度拒绝，contract / autoload 六格全部
  UNVERIFIED（[证据](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5965465667)）；
  同日换账号后同命令六格 PASS（`2026.09.18-9a7762b`，
  [证据](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5965565067)）。
  PASS 只代表该宿主 / 日期 / CLI 版本；额度或信任条件再变时按本节重新取证，如实标 UNVERIFIED。
- `codex` CLI：本机 stderr 恒有两行（`Reading additional input from stdin...` 与
  `failed to refresh available models: request timed out`）→ 整列 UNVERIFIED。答复
  本身有效也不改判；宿主网络恢复后复跑，或由 Owner 另行裁决 stderr 噪声豁免口径。
报表保留完整 HEAD、版本、模式、cwd、判定、耗时与 stderr 文件路径，不保存原始 CLI
stdout/stderr 正文或私有配置。

### 已退役形态（退出验收矩阵）

- `opencode` CLI（Owner 2026-10-03 裁决）：探针不再登记该形态，`--only opencode` 与任何未知形态
  一样被拒绝（返回 1，不启动会话）。原因：宿主默认模型每次调用不同，并落在不可用模型上
  （无可用渠道 / 需订阅），12 次探针调用无一取得有效答复——它**没有走向 PASS 的路径**，
  也不在 v1.3 对 Codex / Cursor CLI 的接受范围内，故按退役处理，而不是再加一条 UNVERIFIED
  豁免。`harness-adapters.md` 的 `AGENTS.md` 适配行与 `opencode.json` 本地态约定不变；历史证据
  保留（[#3563 报告](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5966758919)），不再复测。

## IDE 人工入口

人工形态只剩 **Zcode**（Owner 2026-10-01 裁决：`cursor-ide` / `codebuddy-ide`
退役，不参与后续实施者的验收矩阵；两者的历史实测记录保留在 ADR-0034 附录 A，
不删除、不再复测）。

```bash
.venv/bin/python tools/dev/harness_probe.py --manual-template /tmp/ide-evidence.json
```

该命令只生成 Zcode × 3 cwd 的空白模板并返回 1（UNVERIFIED），没有执行会话。
用 Zcode 打开当前 worktree 的 root / Agent / AEE 目录：

1. 确认实际 workspace/cwd、当前 HEAD 与产品版本；开**独立新会话**，不用旧上下文或 CLI 答案。
2. 取当前 cell 的提示词（只打印提示词，不供给契约正文）：
   `.venv/bin/python -c 'from tools.dev.harness_probe import make_prompt; print(make_prompt("aee", "contract"))'`。
3. 粘贴到 AI 面板。保存最终两行响应及必要的工具活动证据；人工核实仅读取所需契约、
   未发生工具错误。autoload 须确认未调用工具。不要保存敏感配置或无关文件正文。
4. 填写对应 `response`、`version`、`fresh_session=true`、`tool_error=false`、
   `evidence_source`（可复核的本地脱敏记录或 Issue 评论链接）；失败/未跑不能填 expected 冒充 actual。
5. 保持 `root_version` 为实际取证的完整 HEAD；HEAD 不同、缺字段或重复 cell 均不能通过。
   结果是人工提供的证据，脚本不能验证操作者声明真实性，独立复核需打开来源核对。

```bash
.venv/bin/python tools/dev/harness_probe.py --only zcode --manual-input /tmp/ide-evidence.json --json /tmp/ide-contract.json
```

`--only` 必带：省略它会把 5 个 CLI 形态一并选中并真的起外部 LLM 会话。

另测 autoload 时用 `--mode autoload` 生成新模板与新会话；不能把两个实验的数据混用。
未补响应时每行 `actual=null`、UNVERIFIED。无需为取证清理已有会话、修改个人权限或复制凭据。
