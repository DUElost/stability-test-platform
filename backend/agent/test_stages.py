# -*- coding: utf-8 -*-
"""
Universal test lifecycle stages.

All test types share this stage enumeration.
Each test type declares which stages it uses via a STAGES class attribute.

Stages map to the project vision's "稳定性专项统一流程":
1. PRECHECK     - 设备连接检测
2. PREPARE      - 前置准备
3. FILL_STORAGE - 资源填充
4. RUN          - 开始运行测试
5. MONITOR      - 日志检测 / 运行监控
6. RISK_SCAN    - 风险问题检查
7. EXPORT       - 日志回传导出
8. TEARDOWN     - 结束测试
9. POST_TEST    - 测试后置
"""

from enum import Enum
from typing import Dict, FrozenSet


class TestStage(str, Enum):
    PRECHECK = "PRECHECK"
    PREPARE = "PREPARE"
    FILL_STORAGE = "FILL_STORAGE"
    RUN = "RUN"
    MONITOR = "MONITOR"
    RISK_SCAN = "RISK_SCAN"
    EXPORT = "EXPORT"
    TEARDOWN = "TEARDOWN"
    POST_TEST = "POST_TEST"


# Default progress percentages per stage
STAGE_PROGRESS: Dict[TestStage, int] = {
    TestStage.PRECHECK: 5,
    TestStage.PREPARE: 15,
    TestStage.FILL_STORAGE: 25,
    TestStage.RUN: 35,
    TestStage.MONITOR: 55,
    TestStage.RISK_SCAN: 75,
    TestStage.EXPORT: 90,
    TestStage.TEARDOWN: 98,
    TestStage.POST_TEST: 100,
}


def stage_progress(stage: TestStage) -> int:
    """Get the progress percentage for a given stage."""
    return STAGE_PROGRESS.get(stage, 0)


# --- Stage profile sets ---

# Full 9-stage lifecycle (e.g. AIMONKEY)
FULL_STAGES: FrozenSet[TestStage] = frozenset(TestStage)

# Standard lifecycle without FILL_STORAGE and POST_TEST
STANDARD_STAGES: FrozenSet[TestStage] = frozenset({
    TestStage.PRECHECK,
    TestStage.PREPARE,
    TestStage.RUN,
    TestStage.MONITOR,
    TestStage.RISK_SCAN,
    TestStage.EXPORT,
    TestStage.TEARDOWN,
})

# Minimal lifecycle for simple tests (basic MONKEY, STANDBY, GPU, DDR)
MINIMAL_STAGES: FrozenSet[TestStage] = frozenset({
    TestStage.PRECHECK,
    TestStage.RUN,
    TestStage.TEARDOWN,
})
