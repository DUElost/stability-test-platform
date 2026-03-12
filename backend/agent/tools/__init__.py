# -*- coding: utf-8 -*-
"""
测试工具模块
"""

from .monkey_aee_stability_test import MonkeyAEEAction

# backward compat alias
MonkeyAEEStabilityTest = MonkeyAEEAction

__all__ = ["MonkeyAEEAction", "MonkeyAEEStabilityTest"]
