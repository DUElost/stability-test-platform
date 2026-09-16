# `/hosts` 的 desired digest：读失败降级 unknown，且 `""` 不再被当成「没传」（#2320）

Status: implemented
Class: bug-fix

## Decision

### 1. 展示面判据不得把整页打成 500

`list_hosts` 每请求现算一次 desired digest，而 `compute_desired_artifact_digest` 对
输入集全量 `os.stat` + 逐文件读、全程无 try。控制面自身工作树在部署/checkout/清理
期间会变动（stat 与 open 之间还有竞态窗口），一次 IO 失败就让主机运维页整页不可用——
而**降级通路本来就有**：desired 为空 → `resolve_agent_code_sync_status` 判 `unknown`，
前端已按 `unknown` 渲染。

- `hosts.py` 新增 `_list_desired_artifact_digest()`：只吃 `OSError`，失败 → 空串 +
  **一条** warning，并进入 60s 冷却。冷却不是可选项：成功路径的进程缓存在抛异常时不会
  写入，没有冷却的话 UI 轮询的每个请求都会重跑一遍注定失败的输入集遍历（耗时与日志
  同时被放大）。
- 不吞 `OSError` 以外的异常：`ValueError` 之类是真缺陷，继续冒泡（#739 正在治理
  「静默异常吞咽」，这里不能再添一处）。

### 2. 但只改调用侧会得到**假判据**，不是降级（本单的实质发现）

按 issue 的「建议修法 1」把 `desired = ""` 传下去，实测**不成立**：
`build_host_version_view` 里写的是

```python
desired = (desired_artifact_digest or "").strip() or (自算 or "")
```

`""` 与 `None` 被 `or` 混为一谈 → 列表页递下来的「算不出」被下游当成「没传」而**回头
自算**；自算若成功，判据就显示成 `matched`/`drift`。也就是说只包一层 try 会把
「500」换成「一个看起来可信的假判据」——比 500 更难发现。新增用例
`test_explicit_empty_desired_is_unavailable_not_recomputed` 对旧实现**红**，正是钉这条。

于是把 desired 改成**三态分明**：

| 入参 | 语义 | 结果 |
|---|---|---|
| `None`（未传，详情路径） | 本函数自算 | 自算失败 → 空串 → `unknown`（新增 `_self_computed_desired_digest` 兜底 + warning） |
| `""`（调用方**显式**说不可用） | 不重算 | `unknown` |
| 非空 | 真实比对 | `matched` / `drift` |

自算兜底放在 `agent_version_info` 里，顺带把**详情/单主机**那几个 `_host_to_out` 路径
（都走自算分支）一起纳入同一降级语义，而不是只修列表一条路。

## Alternatives

- **在 `artifact_digest.compute_desired_artifact_digest` 内部返回 `None`**（issue 的修法 2）：
  否决。该函数还有部署前检查等**要求响亮失败**的消费者，把它的契约改成「失败返回空」
  会让那些场景失去信号；降级属于调用面的语义，就该在调用面表达。
- **给列表路径加异常兜底但不改 `build_host_version_view`**：见上，会产出假判据。
- **缓存 desired 以彻底消除竞态**：#2155 之后已有进程缓存（键=输入集 stat 指纹），
  指纹本身就依赖 stat，抖动期间照样失败——缓存解决的是重复成本，不解决异常面。
- **冷却做成可配置项**：60s 是展示面判据的经验值（心跳周期同量级）；真要调再说，
  不预先加一个 env 旋钮（#2026 那类「清单跟不上」的成本比省下的这一次大）。

## Verification

新增 7 条用例，对**旧实现 4 条红**、3 条为防过头的边界（两版皆绿，明说不计入自证）：

- 单元（`test_agent_version_info.py`）：`""` 显式不可用**不得回头自算**（红）、
  自算 OSError → unknown + 恰一条 warning（红）、非 IO 异常仍冒泡（边界，两版绿）
- API（`test_hosts.py::TestHostsListDesiredDigestDegradation`）：注入
  `OSError("Stale file handle")` → **200 + 全部 unknown + 恰一条 warning**（红）、
  连打三次 → 只自算一次（红）、正常路径 digest 判据仍 `matched`（边界，两版绿）
- 恢复实现后：`test_agent_version_info.py` 14 passed、两个文件合计 18 passed
- `check:quick` 与更大范围回归见 PR

## Revisit

- 冷却是**进程内**状态（多副本各记各的），且只保护列表路径；若 `/hosts` 的抖动期
  500 仍然出现，先查是不是别的调用面（`host_updater` 等）在算 digest，而不是先调大冷却。
- `agent_code_sync_status` 的 `unknown` 现在有三种成因（Agent 未上报、首次心跳前、
  desired 算不出），运维动作不同。文案层面要不要区分（tooltip 或单独字段），
  等 #2205/#2155 那条展示面收口线再判——本单只保证「不再 500、也不给假判据」。
- `desired` 三态语义应同步进 `docs/operations/agent-version-and-hot-update.md` 的判据表；
  本轮未改该文档（它正被在窗 Execution 触碰），留作下一次落笔。
