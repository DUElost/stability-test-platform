from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "ansible" / "enable_mobilelog.sh"


def test_enable_mobilelog_uses_strict_shell_mode():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "set -euo pipefail" in text


def test_enable_mobilelog_waits_for_device_after_root_before_setprop():
    text = SCRIPT.read_text(encoding="utf-8")

    root_idx = text.index('adb -s "$serial" root')
    wait_idx = text.index('adb -s "$serial" wait-for-device')
    setprop_idx = text.index('adb -s "$serial" shell setprop persist.vendor.mtk.aee.mode 3')

    assert root_idx < wait_idx < setprop_idx
