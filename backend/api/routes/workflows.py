# -*- coding: utf-8 -*-
"""
Legacy Workflow API endpoints — DEPRECATED

This module has been superseded by ``backend/api/routes/orchestration.py``
which uses the new WorkflowDefinition / WorkflowRun / JobInstance models.

It is NOT mounted in ``main.py`` and MUST NOT be imported.  The file is
retained only as a historical reference.  See ADR-0007.

Original models (Workflow, WorkflowStep, WorkflowStatus, StepStatus) were
removed from schemas.py during the Phase-1 model migration.  Importing this
module would raise ImportError.
"""

raise ImportError(
    "backend.api.routes.workflows is deprecated and cannot be imported. "
    "Use backend.api.routes.orchestration instead. See ADR-0007."
)
