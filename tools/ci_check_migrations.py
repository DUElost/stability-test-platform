#!/usr/bin/env python3
"""Stub — 原迁移链检查已归档（2026-09-01）。

请改用：
  python -m pytest tests/test_alembic_heads.py tests/test_alembic_upgrade.py -q
  # PR 路径：CI job pr-migrate-empty-db（alembic upgrade + check_schema_sync）

历史实现见 tools/archive/ci_check_migrations.py（勿再运行）。
"""

import sys

print(__doc__)
sys.exit(2)
