"""PlanRun-scoped HDD scan root helpers."""

from pathlib import Path

from backend.agent.aee.scan_scope import (
    build_scoped_scan_root,
    folder_matches_run_date_stamp,
    is_safe_path_token,
    iter_serial_scan_dirs,
    normalize_str_list,
    path_has_serial,
)


def test_folder_matches_run_date_stamp():
    name = "V551A-15-260701V104_0808_MonkeyAEEinfo"
    assert folder_matches_run_date_stamp(name, "0808")
    assert not folder_matches_run_date_stamp(name, "0731")
    assert not folder_matches_run_date_stamp(name, "")


def test_normalize_str_list_dedupes_and_splits():
    assert normalize_str_list("a;b,a") == ["a", "b"]
    assert normalize_str_list([" x ", "", "x"]) == ["x"]
    assert normalize_str_list(None) == []
    assert normalize_str_list(["ok", "../x", "/abs", "a/b", ".."]) == ["ok"]


def test_is_safe_path_token():
    assert is_safe_path_token("0000NX2622000670")
    assert is_safe_path_token("0808")
    assert not is_safe_path_token("")
    assert not is_safe_path_token("../x")
    assert not is_safe_path_token("/abs")
    assert not is_safe_path_token("a/b")


def test_path_has_serial_requires_full_component():
    path = "/hdd/V551A_0808_MonkeyAEEinfo/0000NX2622000670/db.00.ANR"
    assert path_has_serial(path, ["0000NX2622000670"])
    assert not path_has_serial(path, ["0000NX2622000662"])
    assert not path_has_serial(path, ["0000NX"])


def test_build_scoped_scan_root_keeps_only_matching_serial_and_stamp(tmp_path: Path):
    hdd = tmp_path / "hdd"
    keep = hdd / "V551A_0808_MonkeyAEEinfo" / "SER-A" / "evt-keep"
    skip_serial = hdd / "V551A_0808_MonkeyAEEinfo" / "SER-B" / "evt-other"
    skip_day = hdd / "V551A_0731_MonkeyAEEinfo" / "SER-A" / "evt-old"
    keep.mkdir(parents=True)
    skip_serial.mkdir(parents=True)
    skip_day.mkdir(parents=True)
    (keep / "ZZ_INTERNAL").write_text("keep")
    (skip_serial / "ZZ_INTERNAL").write_text("other")
    (skip_day / "ZZ_INTERNAL").write_text("old")

    dest = tmp_path / "scoped"
    build_scoped_scan_root(hdd, dest, ["SER-A"], ["0808"])

    assert (dest / keep.relative_to(hdd) / "ZZ_INTERNAL").read_text() == "keep"
    assert not (dest / skip_serial.relative_to(hdd)).exists()
    assert not (dest / skip_day.relative_to(hdd)).exists()
    assert iter_serial_scan_dirs(hdd, ["SER-A"], ["0808"]) == [keep.parent]


def test_iter_serial_scan_dirs_rejects_unsafe_and_symlink(tmp_path: Path):
    hdd = tmp_path / "hdd"
    real = hdd / "V551A_0808_MonkeyAEEinfo" / "SER-A"
    real.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link_dir = hdd / "V551A_0808_MonkeyAEEinfo" / "SER-LINK"
    link_dir.symlink_to(outside)

    assert iter_serial_scan_dirs(
        hdd, ["SER-A", "../x", "SER-A/../SER-A"], ["0808"],
    ) == [real]
    assert iter_serial_scan_dirs(hdd, ["SER-LINK"], ["0808"]) == []
