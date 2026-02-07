from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

# 工具根目录：后端目录向上三级即仓库根
TOOLS_ROOT = Path(__file__).resolve().parents[3]


def _tool_path(*parts: str) -> str:
    """生成工具相对路径，便于前后端一致引用"""
    return str(TOOLS_ROOT.joinpath(*parts))


@dataclass(frozen=True)
class TaskTemplateDef:
    type: str
    name: str
    description: str
    default_params: Dict[str, object]
    script_paths: Dict[str, str]


# 六类稳定性测试模板定义
TEMPLATES: Dict[str, TaskTemplateDef] = {
    "MONKEY": TaskTemplateDef(
        type="MONKEY",
        name="Monkey 随机事件压测",
        description="基于 ADB Monkey 的随机事件稳定性验证。",
        default_params={
            "packages": [],
            "event_count": 20000,
            "throttle": 100,
            "seed": None,
            "script_path": _tool_path("Monkey_test", "AIMonkeyTest_2025mtk", "MonkeyTest.sh"),
        },
        script_paths={
            "default": _tool_path("Monkey_test", "AIMonkeyTest_2025mtk", "MonkeyTest.sh"),
        },
    ),
    "AIMONKEY": TaskTemplateDef(
        type="AIMONKEY",
        name="AI Monkey 智能压测",
        description="智能化随机事件压测，支持存储填充、日志清理和 WiFi 配置。",
        default_params={
            "runtime_minutes": 60,
            "throttle_ms": 500,
            "max_restarts": 1,
            "enable_fill_storage": False,
            "enable_clear_logs": False,
            "wifi_ssid": "",
            "wifi_password": "",
            "target_fill_percentage": 60,
            "run_id": "",
            "script_path": _tool_path("Monkey_test", "AIMonkeyTest_2025mtk", "MonkeyTest.sh"),
        },
        script_paths={
            "default": _tool_path("Monkey_test", "AIMonkeyTest_2025mtk", "MonkeyTest.sh"),
        },
    ),
    "MTBF": TaskTemplateDef(
        type="MTBF",
        name="MTBF 稳定性回归",
        description="推送资源并通过 Instrumentation 启动回归用例。",
        default_params={
            "resource_dir": _tool_path("MTBF_stress_test", "stress_task"),
            "remote_dir": "/sdcard/mtbf",
            "apk_path": None,
            "runner": "com.transsion.stresstest.test/androidx.test.runner.AndroidJUnitRunner",
            "instrument_args": {"loop": 1},
        },
        script_paths={
            "config": _tool_path("MTBF_stress_test", "stress_task", "TranssionStressTest.xml"),
        },
    ),
    "DDR": TaskTemplateDef(
        type="DDR",
        name="DDR 内存专项",
        description="Root 环境下运行 memtester 校验内存稳定性。",
        default_params={
            "memtester_path": _tool_path("DDR_test", "memtester"),
            "remote_path": "/data/local/tmp/memtester",
            "mem_size_mb": 512,
            "loops": 1,
        },
        script_paths={
            "package": _tool_path("DDR_test"),
        },
    ),
    "GPU": TaskTemplateDef(
        type="GPU",
        name="GPU 压力循环",
        description="基于安兔兔 GPU 测试循环启动验证图形稳定性。",
        default_params={
            "apk_path": _tool_path("GPU_stress_test", "Antutu_v10_Lite.zip"),
            "activity": "com.antutu.ABenchMark/.ABenchMarkStart",
            "loops": 3,
            "interval": 120,
        },
        script_paths={
            "package": _tool_path("GPU_stress_test", "Antutu_v10_Lite.zip"),
        },
    ),
    "STANDBY": TaskTemplateDef(
        type="STANDBY",
        name="待机视频场景",
        description="启动 YouTube 视频并进入待机，验证长时间休眠稳定性。",
        default_params={
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "standby_seconds": 1800,
            "screen_off": True,
        },
        script_paths={
            "scenario": _tool_path("Reboot_test+Sleep_test"),
        },
    ),
}


def list_templates() -> List[TaskTemplateDef]:
    """返回全部模板列表"""
    return list(TEMPLATES.values())


def get_template(type_name: str) -> Optional[TaskTemplateDef]:
    """按类型名（忽略大小写）获取模板"""
    if not type_name:
        return None
    return TEMPLATES.get(type_name.upper())
