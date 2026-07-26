"""#88: db_history 时间戳的时区解析与 UTC 换算。

生产实测(2026-07-26,ELA-LX2 `0000NX2622000414`)的真实 db_history 行:

    /data/aee_exp/db.01.NE,Native (NE),2777,...,com.android.settings,
    Fri Jul 17 13:41:47 CST 2026,1,10605466

原实现把 `CST` 匹配掉但不解析,调用方再盖上 UTC → 恒定 +8 小时偏差。
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.agent.aee.timestamp import (
    as_device_local_naive,
    format_timestamp_for_filename,
    parse_timestamp,
    to_utc,
)

CST = timezone(timedelta(hours=8))


class TestParseTimestampTimezone:
    def test_cst_token_is_parsed_not_discarded(self):
        """生产真实样本:CST 必须被解析成 +8,而不是丢弃。"""
        dt = parse_timestamp("Fri Jul 17 13:41:47 CST 2026")
        assert dt is not None
        assert dt.tzinfo is not None, "CST 被丢弃了 — 这正是 #88 的根因"
        assert dt.utcoffset() == timedelta(hours=8)

    def test_wall_clock_fields_are_not_shifted(self):
        """墙钟字段不换算 — format_timestamp_for_filename 依赖它生成事件目录名。"""
        dt = parse_timestamp("Fri Jul 17 13:41:47 CST 2026")
        assert (dt.year, dt.month, dt.day) == (2026, 7, 17)
        assert (dt.hour, dt.minute, dt.second) == (13, 41, 47)

    @pytest.mark.parametrize("token,offset_hours", [
        ("UTC", 0), ("GMT", 0), ("CST", 8), ("HKT", 8),
        ("SGT", 8), ("JST", 9), ("KST", 9),
    ])
    def test_known_timezone_tokens(self, token, offset_hours):
        dt = parse_timestamp(f"Fri Jul 17 13:41:47 {token} 2026")
        assert dt is not None and dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(hours=offset_hours)

    def test_unknown_timezone_falls_back_to_naive(self):
        """认不出的时区不猜 — 返回 naive,由 to_utc 按历史行为处理。"""
        dt = parse_timestamp("Fri Jul 17 13:41:47 XYZ 2026")
        assert dt is not None
        assert dt.tzinfo is None

    def test_no_timezone_token_stays_naive(self):
        dt = parse_timestamp("Fri Jul 17 13:41:47 2026")
        assert dt is not None
        assert dt.tzinfo is None

    def test_iso_like_format_unchanged(self):
        dt = parse_timestamp("2026-07-17 13:41:47.123")
        assert dt == datetime(2026, 7, 17, 13, 41, 47, 123000)

    def test_unparseable_returns_none(self):
        assert parse_timestamp("not a timestamp") is None
        assert parse_timestamp("") is None


class TestToUtc:
    def test_cst_converted_to_true_utc(self):
        """13:41:47 CST 的真实 UTC 是 05:41:47 — 原来错存成 13:41:47Z。"""
        utc = to_utc(parse_timestamp("Fri Jul 17 13:41:47 CST 2026"))
        assert utc == datetime(2026, 7, 17, 5, 41, 47, tzinfo=timezone.utc)

    def test_naive_input_returns_none_instead_of_guessing_utc(self):
        """#88 复审:设备没给时区时无从得知真实 UTC —— 返回 None,不能盖 +00:00。

        盖上 UTC 只是把「未知」伪装成「已知」,会让 aee_ts_utc 与 detected_at
        的差值不再等于设备时钟漂移。
        """
        assert to_utc(parse_timestamp("Fri Jul 17 13:41:47 2026")) is None

    def test_unknown_timezone_returns_none(self):
        """时区缩写不认识时同理 —— 不猜。"""
        assert to_utc(parse_timestamp("Fri Jul 17 13:41:47 XYZ 2026")) is None

    def test_iso_format_without_tz_returns_none(self):
        assert to_utc(parse_timestamp("2026-07-17 13:41:47")) is None

    def test_none_passes_through(self):
        assert to_utc(None) is None


class TestAsDeviceLocalNaive:
    def test_strips_tzinfo_for_device_side_comparison(self):
        """mobilelog 文件名时间戳是设备本地 naive,两边必须同类型才能比较。"""
        aware = parse_timestamp("Fri Jul 17 13:41:47 CST 2026")
        naive = as_device_local_naive(aware)
        assert naive.tzinfo is None
        assert naive == datetime(2026, 7, 17, 13, 41, 47)
        # 关键:能与设备文件名解析出的 naive datetime 直接比较而不抛 TypeError
        assert naive > datetime(2026, 7, 17, 13, 0, 0)

    def test_none_passes_through(self):
        assert as_device_local_naive(None) is None


class TestFilenameUnaffected:
    def test_event_dir_name_keeps_device_local_wall_clock(self):
        """事件目录名必须与设备侧一致 — 换算 UTC 会导致既有路径漂移。"""
        assert format_timestamp_for_filename(
            "Fri Jul 17 13:41:47 CST 2026"
        ) == "2026_0717_134147_000"

    def test_matches_production_artifact_path(self):
        """比对生产 job_log_signal.artifact_uri 里的实际目录名。"""
        assert format_timestamp_for_filename(
            "Fri Jul 17 13:36:21 CST 2026"
        ) == "2026_0717_133621_000"
