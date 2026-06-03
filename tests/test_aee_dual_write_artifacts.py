from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RECON_SCRIPT = REPO_ROOT / "backend" / "scripts" / "aee_dual_write_recon.py"
RUNBOOK = REPO_ROOT / "docs" / "plans" / "watcher-aee-m1-dual-write-runbook.md"


def test_dual_write_recon_uses_vendor_aee_exp_in_nfs_command():
    text = RECON_SCRIPT.read_text(encoding="utf-8")

    assert "vendor_aee_exp" in text
    assert "vendor/aee_exp" not in text


def test_dual_write_runbook_uses_vendor_aee_exp_in_nfs_command():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "vendor_aee_exp" in text
    assert "vendor/aee_exp" not in text
