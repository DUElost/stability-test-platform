# MTBF 中心结果路径加入跨设备稳定身份（脚本 v1.5.0）

Status: implemented
Class: bug-fix

## Decision

#1030（R08-F12，设计风险）：中心结果文件名只用 `{run_dir}.json`，而 `run_dir` 是
**设备本地毫秒时间戳**、非全局唯一（设计即如此）。同项目两台设备产生同名 run_dir
时写向同一文件，后写者覆盖先写者 —— 先完成那台的结果永久丢失。写入还是直接
`write_text`，消费方可能读到写了一半的 JSON。本轮未验证生产是否已发生碰撞。

修复（新脚本版本 **v1.5.0**，v1.4.0 原地不动 —— ADR-0020 已发布版本不可变）：

- 新增 `_result_stem(run_dir)`：文件名 `{run_dir}__job{STP_JOB_ID}__{STP_DEVICE_SERIAL}.json`
  —— 维度缺失则省略（手动跑也能用），非文件名安全字符归一化为 `_`；
- 同一 Job 重跑仍得到同一名字 → **幂等覆盖，不产垃圾**；不同设备天然隔离；
- 新增 `_write_json_atomic`：同目录临时文件 + `os.replace` 发布；发布失败清掉临时
  文件（隐藏的 `.tmp-*` 留在 results 目录会污染人工排查）；
- payload 结构不变（run_dir / metrics / testpoints），消费方只读 `detail_uri`
  （`backend/services/case_result_ingest.py` 不按文件名约定扫目录），路径变更对其透明；
- 同步权威文档：`docs/design/2026-08-mtbf-p0-runner-design.md` 与
  `backend/models/case_result.py` 里写死旧文件名的地方。

## Alternatives

- 中心路径加 `plan_run_id`：语义同样稳定，但脚本侧只有 `STP_JOB_ID` 与
  `STP_DEVICE_SERIAL` 两个现成 env（pipeline_engine 注入），引入新 env 要改 Agent，
  收益不高于 job_id；
- 目录分设备（`results/{serial}/{run_dir}.json`）：同样解决碰撞，但会改变目录布局，
  与「控制面按 project 找结果」的既有约定偏离更大；文件名方案是最小改动；
- 加 UUID/随机后缀：能防覆盖但破坏幂等（重跑留垃圾），放弃。

## Verification

- `pytest backend/agent/tests/test_mtbf_finish_v150.py`：7 passed —— 文件名含
  job+serial / 缺失维度省略 / 特殊字符归一 / 重跑幂等 / 双设备同名 run_dir 两个文件
  各自保留正确内容 / 原子发布成功与失败都不留临时文件；
- `pytest backend/agent/tests`：全目录通过；
- `tools/dev/check-script-version-immutability.py --base origin/main`：OK（无已发布
  版本被原地改动）。

## Revisit

- 消费方目前只读 `detail_uri`；若将来出现「按 run_dir 反查结果文件」的需求，必须
  同时带 job/serial，否则会查错文件（这也是本单把身份放进文件名的原因）；
- 历史 `results/{run_dir}.json` 存量文件不在本单迁移范围 —— 若生产已发生覆盖，
  需另开单按 step_trace 重放；
- #810（结果目录绑定 setup 记录的 run_dir）与本单相邻但不同：本单解决命名唯一性，
  #810 解决「拉到的是不是本轮目录」。
