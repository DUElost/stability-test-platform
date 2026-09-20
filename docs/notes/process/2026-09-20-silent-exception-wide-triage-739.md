# 静默吞咽：宽异常（`except Exception`）65 处逐条分诊（#739 面② 第三步）

Status: implemented
Class: process

## Decision

在 2026-09-17 的口径/基线与分诊（`silent-exception-audit-baseline-739.md` /
`silent-exception-triage-and-e722-739.md`）之上，把 triage Revisit ②「宽异常 66 处未逐条读」
收口：**本轮逐条读完当前全部 65 处 `except Exception`**（基线复测 260 处，pass 114 /
continue 58 / return_none 88；裸 `except:` 0 处——E722 在场）。

结论：**58 处为有意降级（无需改动），7 处补可观测性（本轮修复，行为不变），0 处需要改语义。**
既有的「不按总量上门禁」裁决**维持**（理由见 triage；本轮逐条复读未发现支持翻案的新证据）。

### 一、本轮修复（7 处，均为「静默会掩盖状态/账目/进程问题」的位点）

| 位点 | 处理 | 理由 |
|---|---|---|
| `backend/agent/event_uploader.py` `_durable_get_attempts` | 未配置 `STP_AGENT_STATE_DB` 改**显式早退**；真读失败补 `logger.warning` | 原先「未配置」（预期）与「真读失败」（锁/表缺失/IO/坏值）都走 `except: return None`——重试账目为何漂移在现场无信号 |
| `backend/agent/api_client.py` `local_db.ack_terminal` | 补 `logger.warning` | ack 失败 ⇒ terminal 留在 outbox 被 drainer 重发（服务端幂等）；需能解释「为什么又发了一次」 |
| `backend/agent/outbox_drainer.py` 错误响应体解析 ×2 | 补 `logger.debug` | 区分「服务端错误契约漂移」与「本来就没有该字段」 |
| `backend/agent/pipeline_engine.py` taskkill 兜底 `proc.kill` 失败 | 补 `logger.warning` | 兜底也失败 ⇒ 进程可能仍存活；原先静默，后续步骤的异常会指向别处 |
| `backend/services/script_catalog.py` / `backend/api/routes/scripts.py` 缓存失效 | 补**原因注释**（不补日志） | 版本缓存带 TTL（默认 30s，`STP_SCRIPT_CATALOG_VERSION_CACHE_TTL`）→ 失效失败自愈；注释把判据固定下来，避免后续评审当缺陷反复审 |

### 二、58 处有意降级：按形态分诊（代表位点，逐条已读）

| 形态 | 量级 | 判据 |
|---|---|---|
| 进程 teardown 级联（terminate→wait→kill→communicate best-effort） | `aee/bugreport.py` ×4、`aee/reconciler.py` ×4、`pipeline_engine` 读者收尾 ×2 | 每一步都有下一步兜底，失败无补救动作；抛错反而掩盖主结果 |
| 单例关停 / 测试复位（`_instance.stop(...)`） | `artifact_uploader` / `local_disk_monitor` / `log_archiver` / `watcher.{emitter,manager}` / `run_console` / `socketio_client` 共 ~11 | 关停尽力而为；测试复位失败不应影响后续用例 |
| cleanup / 临时文件删除（unlink、rmtree、SFTP remove） | `aee/processor` / `watcher/puller` ×2 / `dedup_scan` / `host_updater` / `precheck/sync` 收尾 | 删除失败无补救；配额/堆积统计另有可观测量 |
| 指标面防御（Prometheus 原语不可用即 no-op） | `core/thread_pool` ×2、`log_archiver` 指标合并、`agent_version_info` | 既定判例：指标不得阻断业务路径（triage 表） |
| 解析容错（JSON / 时间戳 / 记录构建 / 响应体） | `realtime/console_registry` ×2、`api/routes/logs` 时间戳、`aee/db_history`、`check_schema_sync`、`core/csrf`、`dedup_extract` | 单条记录坏不拖垮整批；缺省值语义即「无该记录」 |
| 可选依赖降级（socketio/realtime 导入） | `services/plan_run_events`、`precheck/notify`、`precheck/reachability` | 控制面某些进程没有 realtime；既定降级 |
| 轮询等待到 deadline | `tools/site_config/stages` ×2 | 循环内吞掉每次失败、由超时返回 False 收口（triage 表已定性） |
| 读面跳过（不可读文件/无法解析的行） | `api/routes/logs` 读文件失败 → `continue` | 读面跳过；不产生假数据 |
| 兜底/诊断路径（fallback 探测、关停收尾、环境导出） | `check_backend_redis`、`tools/dev/fake_agent`、`skill_usage_report`、`migration/preflight`、`core/leader_election` ×2、`plan_run_aggregation` rollback、`job_session` 清理、`watcher/sources` 探测、`run_console` flush、`pipeline_engine` `BASE_DIR` 导出 | 均有下游可见信号（返回 False / 上层 logger.exception / 后续失败），或为诊断工具宽容降级 |

### 三、口径问题（triage Revisit ③）的处置建议

`return 0` / `return []` / `return ""` 是否算「静默」：**建议维持现口径（不计入）**。
理由：`return None` 是 Python 里「无值」的通用形态，逐处可判；而字面量缺省值的语义必须逐处读
（`return 0` 在计数场景是真实值、在长度场景是失败兜底），扩展口径会把噪声直接计进基线。
若将来要治理这一类，按 triage 的建议走「先子系统抽读、再决定是否扩口径」。

## Alternatives

- **按总量上棘轮/封顶门禁**：否决（triage 已论证，本轮逐条复读支持该结论）——58/65 是有意降级，
  封顶只会逼出「加一行无意义 debug」的应付式改动。
- **对 58 处逐条补「原因注释」**：否决。注释的价值在于「不看代码就知道为什么」，而这 58 处的
  理由高度同形（9 类形态）；逐条加注释等于把同一句话抄 58 遍，diff 噪声远大于收益。改为
  **按形态记入本文**（评审与后续治理的判据），只在**易被误判为缺陷**的两处（缓存失效）留行内注释。
- **把 `except Exception` 收窄成具体异常类型**：多数位点可行但风险不对称——收窄后未预期的异常
  会直接抛出（在 teardown/cleanup 路径上等于用新故障换旧静默），需要逐处确认异常面；本轮
  不改语义，留作后续专项（触发条件见 Revisit）。
- **扩展审计工具口径到 `return <字面量>`**：暂缓（见 Decision 三）。

## Verification

- `python tools/dev/audit_silent_exceptions.py --self-test` → 通过；基线 260 处 → 修复后
  **255 处**（pass 110 / continue 58 / return_none 87；本轮 5 处转有日志，2 处仅补注释仍计入）；
- 逐条读：65/65 宽异常位点均带上下文读完（agent 33 / services 14 / core 6 / api 3 /
  realtime 2 / scripts 2 / tools 5）；
- 定向测试：agent 侧 outbox/uploader 六文件 `env -i` → **95 passed**；
  `script_catalog`（缓存/激活）+ `api/test_scripts.py` → **44 passed**；
- `ruff check`（6 个改动文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → **[OK] 12 gates**；`check:pr` → **[OK] 21 gates**
  （含 agent-tests / agent-tests-collect / repo-level tests / pr-migrate）。

## Revisit

- **触发条件（命中任一即启动专项）**：① 出现「静默吞咽掩盖真实故障」的事故（同类于 #739 的
  落盘/日志静默）；② 某个子系统在月度审计中反复出现「同一处静默被反复怀疑」；
  ③ 审计工具口径扩展（`return <字面量>`）被裁决采纳。
- **`except Exception` 收窄专项**：若启动，按子系统分批（agent 执行链 / watcher / control-plane
  services / tools），每批附「收窄后异常面」的实证（注入非预期异常必须能红）；
- **工具口径**：维持 `pass / continue / return_none` 三类；扩展前先出「按子系统抽读」结论。
