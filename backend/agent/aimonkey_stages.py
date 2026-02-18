# -*- coding: utf-8 -*-
"""Backward compatibility shim — delegates to test_stages.py."""

from .test_stages import TestStage as AIMonkeyStage, STAGE_PROGRESS, stage_progress

__all__ = ["AIMonkeyStage", "STAGE_PROGRESS", "stage_progress"]
