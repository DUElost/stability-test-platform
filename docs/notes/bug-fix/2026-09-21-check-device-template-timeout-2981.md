# check_device 模板 timeout 对齐重试预算（#2981）

Status: implemented
Class: bug-fix

## Decision

8 个种子模板里 `script:check_device` 的 `timeout_seconds` **30 → 180**，
容纳 v1.0.2 默认 `total_budget_seconds=150`（+ 余量，与脚本 docstring 建议一致）。

引擎墙钟到点 `killpg`，30s 下第二次探测与 boot 门不可达，失败报文退化为裸
`script timeout after 30s`——比 v1.0.1 的证据报文更差。本单只改模板种子；
**不**原地改已发布脚本版本。

守卫：`tests/test_check_device_template_timeout_2981.py` 钉
`timeout_seconds >= 180` 且下限高于脚本默认预算。

## Alternatives

- **下调脚本 `total_budget_seconds` 到 ≤30**：否决——等于撤销 #2802 吸收窗口的设计。
- **新建 check_device v1.0.3 只改文档**：对运行时无帮助；欠账在模板墙钟。
- **顺手改生产 plan_step**：需运维授权写库，不进本 PR（见 Revisit）。

## Verification

- `python -m pytest tests/test_check_device_template_timeout_2981.py tests/test_pipeline_template_script_pins_2865.py -q`
- `python tools/dev/check-script-version-immutability.py --base origin/main`
- `python scripts/run_gates.py check:quick`

## Revisit

存量 Plan（作者称 ~33 处仍 30s）不会随模板自动迁——合入后需运维把在跑链
（含 plan 54）的 `check_device` 步骤 timeout 抬到 ≥180，否则真机窗仍不可达。
