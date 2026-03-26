# -*- coding: utf-8 -*-
"""
DEPRECATED — Legacy task dispatcher based on schemas.py Task/TaskRun models.

All dispatch logic has been migrated to ``backend.services.dispatcher``.
This module is no longer imported anywhere and is preserved only for reference.
Do not restore any imports from this module.

Deprecated since: Wave 3a (ADR-0008)
"""

_DEPRECATED = True

raise ImportError(
    "backend.scheduler.dispatcher is DEPRECATED. "
    "Use backend.services.dispatcher instead. "
    "See ADR-0008 Wave 3a."
)
