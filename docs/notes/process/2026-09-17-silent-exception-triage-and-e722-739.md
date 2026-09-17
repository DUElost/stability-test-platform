# 静默吞咽：口径修正 + 分诊 + E722 收口（#739 §2 第二步）

Status: implemented
Class: process

## Decision

在 #2544（审计工具与基线）之上做三件事——都是「先固口径、再裁决」的继续，**仍不引入
按总量封顶的门禁**：

1. **口径修正：排除 vendored 第三方**。初版扫描面把 `backend/agent/resources/`
   （AIMonkey / flashtool，第三方随包工具）算进了治理面——实测 16 处 `except: pass`
   里 **15 处**落在这里。判据同 `ruff.toml` 的 `extend-exclude` 与 CI 污染检查的
   `-not -path '*/resources/*'`。修正后基线：**253 处**（pass 115 / continue 54 /
   return_none 84），全量对照 923 处。
2. **用标准规则收口最危险的一类**：启用 ruff **E722（禁裸 `except:`）**。
   它**不是**格式规则而是真实缺陷判据——裸 except 连 `KeyboardInterrupt` /
   `SystemExit` 一起吞掉，且「出错了」与「本来就没有」在日志里不可区分。
   全仓（除已排除面）实测只有 **1 处**：`backend/agent/system_monitor.py`
   读 `/proc/net/tcp` 的那段——**内层裸 except 本就是多余的**（外层已有
   `logger.warning + 返回 0` 的收口），删除内层即可，顺带把「静默报 0」变成有告警。
3. **分诊结论（决定为什么不按总量上门禁）**：逐类读下来的构成是——

| 形态 | 量级（示例） | 判定 |
|---|---|---|
| 指标面防御转换（`int()/float()` 失败即跳过） | `backend/core/metrics.py` 全部 16 处 | **有意**：指标不得打断调用方 |
| 可选依赖降级（命名异常） | `console_registry` 7 × `ConsoleRegistryUnavailable` | **有意**：Redis 不可用时的既定降级 |
| 轮询等待（到 deadline 前吞掉重试） | `site_config/stages.py` 2 处 | **有意** |
| 进程/清理竞态（`ProcessLookupError`、close/kill best-effort） | `pipeline_engine` 多数 | **有意** |
| 解析容错（PROGRESS 行、注册表载荷） | `pipeline_engine:153`、`console_registry` 2 处 | **有意** |
| **落盘/日志静默** | `pipeline_engine.py:2609`（makedirs）、`:2627`（日志写入） | **待裁决**（见 Revisit） |

即：253 处里**绝大多数是有意降级**，按总量封顶只会逼出「加一行无意义 debug」的
应付式改动（与 #736 里被否掉的「私有函数数 ≤5」同病）。

## Alternatives

- **按总量上棘轮门禁（不得净增 / 绝对封顶）**：否决。构成里有意降级占绝对多数，
  阈值的任何取值都会把「有意的」和「可疑的」一起管住；而且它会惩罚那些**正确**
  地吞掉探测失败的改动。
- **自建「禁止静默吞咽」自定义门禁**：否决。E722 这类**标准规则**更便宜、无争议、
  ruff 版本升级即自带维护；自建规则还得自己定义形态边界（本工具的 `classify_handler`
  就是那套边界，但它适合审计、不适合阻断）。
- **保留裸 except 那 15 处 vendored 例外在统计里**：否决。第三方代码我们改不了，
  计入只会让「治理面」的规模与形态都被带偏（正是本次修正的动因）。
- **逐条修 253 处**：否决。没有判据的批量改动 = 把日志噪声换成另一种噪声；
  先有分类（本 Note）再有批次，是 #739 §2 的既定路线。

## Verification

- **反例构造（先证伪再采信）**：
  - 关掉 vendored 排除（`_VENDORED_PREFIXES` 分支删掉）→
    `test_frozen_surfaces_are_excluded` **FAILED**；
  - 往 `backend/agent/heartbeat.py` 加一段裸 `except:` → `ruff check` **Found 1 error**
    （E722 有效）。两处恢复后全绿。
- 实测命令与结果：
  - `python tools/dev/audit_silent_exceptions.py` → 扫 403 个生产文件、**253 处**
    （裸 except **0**）；`--self-test` 通过；
  - `python -m ruff check backend/ tools/ scripts/ tests/` → All checks passed（含 E722）；
  - `env -i … pytest backend/agent/tests/test_system_monitor.py -q` → **22 passed**；
  - `TESTING=1 python -m pytest tests/test_silent_exception_audit.py -q` → **6 passed**。

## Revisit

- **`pipeline_engine.py` 的两处待裁决**：`:2609` `os.makedirs` 失败静默（后续写入会
  以别的方式失败，根因丢失）、`:2627` 步骤日志写入失败静默（**事故时**日志正好消失，
  且与「这一步本来没输出」不可区分）。建议：至少降到 `logger.debug`（或加一个
  「日志写入失败计数」），但要先确认不会在坏盘场景刷屏——这两处是 253 里我认为
  真正值得单独处理的。
- **宽异常（`except Exception` / 裸）66 处未逐条读**：本次只抽读了六个主要文件
  （约 60 处）。若决定治理，建议按**子系统**分批（agent 执行链 / watcher /
  site_config / tools）而不是按文件计数——同子系统的吞咽往往同因，可一次定性。
- **`classify_handler` 的形态边界**：`return <缺省值>` 目前只认 `return None`；
  `return 0` / `return []` / `return ""` 是否算「静默」需要裁决（本工具暂不计入，
  `--json` 里有原始 `return_none` 明细可供扩展）。
