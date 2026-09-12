# e7f8 passthrough setup 入口脚本 sha 修正与回填（#751）

Status: implemented
Class: bug-fix

## Decision

1. 原地修正 `e7f8a9b0c1d2` 三处 `content_sha256` 为入口脚本
   （`gpu_setup.py` / `powercycle_setup.py` / `sleep_setup.py`）真实 sha，
   供全新空库 INSERT 正确。
2. 新增 `dd44ee55ff66`：对仍保留旧 `_lib.py` sha 的行精确
   `UPDATE … WHERE content_sha256 = :old`（#1276 / t7u6 同模式）。
3. 静态单测断言 seed 常量 ≠ `_lib` 且 = 入口文件；docker 往返测回填幂等。

## Alternatives

- **只改 seed、不回填**：已部署库仍 conflict，直至人工 force_rebaseline；否决。
- **只要求运维 rebaseline**：新环境 CI/DR 空库仍踩坑；否决。

## Verification

- `python -m pytest backend/tests/migration/test_e7f8_passthrough_seed_entry_sha_751.py -q`
- `python -m pytest backend/tests/migration/test_passthrough_setup_entry_sha_backfill_751.py -q`（需 docker）
- `python -m pytest tests/test_alembic_heads.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

生产若仍见 conflict，对目标版本 `POST /scripts/scan?force_rebaseline=true`
（admin）；本回填只改「仍等于旧 _lib sha」的行。
