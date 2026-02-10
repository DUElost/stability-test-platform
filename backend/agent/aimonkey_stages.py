from enum import Enum


class AIMonkeyStage(str, Enum):
    PRECHECK = "PRECHECK"
    PREPARE = "PREPARE"
    FILL_STORAGE = "FILL_STORAGE"
    RUN = "RUN"
    MONITOR = "MONITOR"
    RISK_SCAN = "RISK_SCAN"
    EXPORT = "EXPORT"
    TEARDOWN = "TEARDOWN"


STAGE_PROGRESS = {
    AIMonkeyStage.PRECHECK: 5,
    AIMonkeyStage.PREPARE: 15,
    AIMonkeyStage.FILL_STORAGE: 25,
    AIMonkeyStage.RUN: 35,
    AIMonkeyStage.MONITOR: 55,
    AIMonkeyStage.RISK_SCAN: 75,
    AIMonkeyStage.EXPORT: 90,
    AIMonkeyStage.TEARDOWN: 98,
}


def stage_progress(stage: AIMonkeyStage) -> int:
    return STAGE_PROGRESS.get(stage, 0)
