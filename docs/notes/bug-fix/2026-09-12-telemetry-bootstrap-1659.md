# queue_head_telemetry 非包入口 bootstrap（#1659）

Status: implemented
Class: bug-fix

## Decision

#1659：`queue_head_telemetry.py`（#1246 交付的队首阻塞遥测）以
`python tools/dev/queue_head_telemetry.py` 直接运行时，实际采集路径报
`ModuleNotFoundError: No module named 'tools'`——`collect()` 内的 lazy import
`from tools.dev.ai_work import derive_integration` 需要仓库根在 `sys.path`，
而直接运行的 `sys.path[0]` 是 `tools/dev`。`--help` 不触发该 import，所以
冒烟时漏网；首次实战（诊断 #1586 排队位置）才暴露。

修复：与仓内先例 `tools/dev/backfill-test-project.py` 同款 bootstrap——
`REPO_ROOT = Path(__file__).resolve().parents[2]` 后 `sys.path.insert`
（用 `__file__` 推导，**与 cwd 无关**）。纯入口修复，不改任何采集语义。

## Alternatives

- 把 lazy import 改为 `importlib.util.spec_from_file_location` 按文件加载：
  可行但绕，且 `ai_work` 自身还有包内相对依赖；bootstrap 是仓内既有模式；
- 把 import 移到模块顶层但不加 bootstrap：直接运行会以同样方式炸在 import 行
  （错误位置前移，不解决）；bootstrap + 保留 lazy import（首屏更快）即可；
- 文档改为「必须 `python -m tools.dev.queue_head_telemetry`」：把工具入口限制
  成包调用，改变了使用习惯且不解决根因——先例工具都是直接可跑的。

## Verification

- `pytest tests/test_queue_head_telemetry_bootstrap.py`：2 passed——
  ①仓库外 cwd 用 importlib 加载脚本模块后再 import `tools.dev.ai_work`，
  断言 exit 0（**反证：去掉 bootstrap 后该用例失败**，mutation 实跑确认）；
  ②bootstrap 关键行防回退断言；
- **端到端**：修复后直接运行 `python tools/dev/queue_head_telemetry.py`
  成功输出队列遥测（实测读到 queue_head=#1656、blocked 2m9s、
  FIFO 不变式告警 [1658]）——即用户本次要的「诊断队列位置」路径已可用；
- `ruff check backend/ tools/ scripts/ tests/` 全绿。

## Revisit

- 同类隐患扫描：其他 `tools/dev/*.py` 若含 `from tools...` 或 `from backend...`
  的 lazy import，也需 bootstrap（本次未全量扫；如再现同型报错，可直接套
  本模式）；
- 遥测里的 `FIFO 不变式告警：多个 PR 挂了 auto-merge` 属 #1246 的告警面，
  快照性出现，非本单范围。
