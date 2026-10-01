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

Q1 检查根契约的两处标题，Q2 在 root 检查 Agent 标题（预期不存在），在 Agent 检查
Agent 标题，在 AEE 检查 Agent 与 AEE 两个标题；Q3 已删除，不再声称检测重复加载。
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

- `cursor` CLI：命令不含 `--trust`，宿主无预置工作区信任时非交互直接 `exit=1`
  → 该格恒 UNVERIFIED。**这是接受的终态**，不为此加信任绕过参数；Cursor CLI 的
  真实证据需在有预置工作区信任的宿主上复跑。
- `codex` CLI：本机 stderr 恒有两行（`Reading additional input from stdin...` 与
  `failed to refresh available models: request timed out`）→ 整列 UNVERIFIED。答复
  本身有效也不改判；宿主网络恢复后复跑，或由 Owner 另行裁决 stderr 噪声豁免口径。
报表保留完整 HEAD、版本、模式、cwd、判定、耗时与 stderr 文件路径，不保存原始 CLI
stdout/stderr 正文或私有配置。

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

`--only` 必带：省略它会把 6 个 CLI 形态一并选中并真的起外部 LLM 会话。

另测 autoload 时用 `--mode autoload` 生成新模板与新会话；不能把两个实验的数据混用。
未补响应时每行 `actual=null`、UNVERIFIED。无需为取证清理已有会话、修改个人权限或复制凭据。
