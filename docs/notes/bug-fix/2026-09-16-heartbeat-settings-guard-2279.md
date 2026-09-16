# #2279 心跳不再因「坏配置旋钮」静默中断（含 reload 路径）

Status: implemented
Class: bug-fix

## Decision

三处改动：

1. **`heartbeat_thread._read_heartbeat_settings_safe()`**（新增）：读 `HeartbeatSettings`
   失败返回 `None` + `logger.exception`（ERROR 含栈），**不抛**；
2. **`_tick`**：把条件表达式里的裸 `get_heartbeat_settings().adb_auto_repair_enabled`
   改为**本拍一次性**读取的 `heartbeat_settings`（`None` → 该可选项不启用）；
3. **`reload_from_settings`**（heartbeat 与 coordinator 两侧）：读取失败不抛，
   **沿用既有值**并记 ERROR。

## 缺陷确认：**方向属实，但 issue 点名的旋钮不对**

issue 说「`STB_ADB_AUTO_REPAIR` 这类旋钮是非数值 → 抛 `ValidationError`」。
回源码发现**该字段不会抛**：

```python
stp_adb_auto_repair: str = "0"          # settings.py:211 —— **字符串域**
@property
def adb_auto_repair_enabled(self): return self.stp_adb_auto_repair == "1"
```

**实测**（`STP_ADB_AUTO_REPAIR=abc`）→ 构造成功、`adb_auto_repair_enabled=False`，**不抛**。

**但失败面本身真实存在，只是来自严格组的数值旋钮**。实测三个都会抛：

| env | 结果 |
|---|---|
| `STP_HEARTBEAT_INTERVAL_MIN=abc` | **抛 ValidationError** |
| `STP_ADB_REPAIR_COOLDOWN_SECONDS=abc` | **抛 ValidationError** |
| `COORDINATOR_HEARTBEAT_INTERVAL=abc` | **抛 ValidationError** |
| `STP_ADB_AUTO_REPAIR=abc` | 不抛（字符串域） |

**触发链**（与 issue 描述一致，只是旋钮换名）：`adb_server_conflict == True`（短路求值
才走到读 Settings）→ 某数值旋钮非数值 → `ValidationError` → `_safe_tick` 吞掉 →
**该 tick 执行不到 `:394 send_heartbeat`** → 静默掉线。

**为何值得修**：心跳是 Agent 的**生命线**，其可用性不应依赖某个**可选**修复旋钮
（ADB 自动修复）的取值是否合法。

## 实施中的一次自我纠正（留痕）

### 我扩大了 scope：coordinator 同源暴露

issue 只点了 `heartbeat_thread.py`。但我顺着 `main.py:1045-1046` 发现：

```python
heartbeat_thread.reload_from_settings()
coordinator.reload_from_settings()      # ← 紧随其后，同一个无外层兜底的处理器
```

`coordinator.reload_from_settings()` 同样裸调 `get_heartbeat_settings()`（实测**无 try**），
且它抛会让 `reload_config` **整体失败**——后续 `ScanRunner.is_configured()` /
`UploadManager` 重配 / done 日志全部不执行。

issue 的「建议修法 1」本就点名了 `reload_from_settings`、「验收③」要求
「`#2014` 的同类兜底在 heartbeat 路径同样存在」，故**判为同单范围内**。
据此我 `finish --abandon` 后**重新 declare** 把 `coordinator.py` 纳入 scope。

## Alternatives

- **把坏值钳成默认值（宽容化）** → 否决：`_clamp_positive_seconds` 的严格性是有意保留的
  （ADR-0042 等价性原则：非数值应与迁移前 `float(os.getenv(...))` 同样失败）。
  本单要修的是「**失败不应杀死心跳**」，不是「让失败不发生」。
- **只在 `_tick` 外层加 try** → 否决：`_safe_tick` **已有** try/except，问题恰恰是
  它把异常吞成一行日志；需要的是**局部兜底 + 可观测**，而非再套一层。
- **改成 `__init__` 一次性读（回到迁移前）** → 否决：#2086 刻意改为每 tick 读，
  为的是 `reload_config` 后新值即时生效；回退会**重新引入 #2086 的缺陷**。
- **让 `get_heartbeat_settings()` 自身宽容** → 否决：会影响所有消费方（含
  `main.py` 构造路径），把「配置错误」静默化——破坏 ADR-0042 的等价性承诺。
  兜底应发生在**消费者**（生命周期关键路径）而非配置层。
- **只改 heartbeat 不改 coordinator** → 否决：同一 reload 处理器内的相邻调用，
  只修一半会让 `reload_config` 仍因 coordinator 抛而整体失败（见上「自我纠正」）。

## Verification

- `pytest backend/agent/tests/test_agent_settings_heartbeat.py -q` → **48 passed**
  （既有 44 + 新增 4）；
- **红绿双向（实测）**：还原为裸调用后 →
  `test_tick_survives_invalid_pacing_knob_when_adb_conflict` 与
  `test_heartbeat_reload_survives_invalid_knob` **红灯**（`ValidationError`），
  修复后 **48 passed**；
- **新增用例覆盖 issue 的三条验收**：
  ① 坏旋钮 + ADB 冲突 + tick → **心跳仍发出**；
  ② 失败留 **ERROR 级**日志（`heartbeat_pacing_reload_skipped` /
  `heartbeat_settings_unavailable`），不再是 debug；
  ③ `reload_from_settings` 不抛、沿用既有值（heartbeat 与 coordinator 两侧）；
- **负向对照** `test_tick_still_sends_heartbeat_without_adb_conflict`：
  无 ADB 冲突时（短路未读 Settings）坏旋钮不影响心跳；
- **全套 agent 回归**：`pytest backend/agent/tests/ -q` → 23 failed / **2067 passed**；
  与干净 `origin/main` 基线（23 failed / 2059 passed）**失败集合逐行 diff 完全一致**
  （23 例为既有失败，本改动净增 4 例通过、**零新增失败**）；
- `ruff check` 三个文件 → All checks passed；
- `check_governance_surface.py --check` → S1–S14、S5x 全绿。

## Revisit

- **`__init__` 路径仍严格**（`heartbeat_thread.py:76`）：构造时若旋钮非法会抛，
  由 `main.py` 的启动路径处理。本单**未**改（启动期失败是「起不来」而非「静默掉线」，
  语义不同）。若现场出现「启动即崩且日志难读」，应另行评估启动期的可诊断性。
- **`STP_ADB_AUTO_REPAIR` 的类型**：它是 `str`，故 `"true"`/`"yes"` 等**不会**启用
  （仅精确 `"1"`），这是 #2086 刻意保留的迁移前语义。本单未动；若运维常误填，
  应在文档或校验层显式提示，而非放宽比较。
- **坏配置的持续性**：本单使心跳在坏配置下**继续**，但 ADB 自动修复会**静默失效**
  （降级为不启用）。ERROR 日志是唯一信号——若要更强可见性（如心跳体里带
  `settings_degraded` 标志），需控制面 schema 配合，超出本单范围。
