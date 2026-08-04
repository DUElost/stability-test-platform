"""执行时可选 WiFi（资源池方案）的行为测试。

需求：WiFi 连接在计划执行前**可选** —— 缺省不连接，但保留连接选项。
落地由三段拼成，这里覆盖后两段：

1. `monkey_setup` v2.0.0 的 `step_wifi` 无 SSID 时 skipped 而非 failed（脚本侧）
2. `inject_wifi_params` 把选中的池凭据注入 `connect_wifi` 与 `monkey_setup`
3. `_sync_allocate_devices` 按 `pool_id` 限定到操作员选的那个网络
"""

import pytest

from backend.models.job import JobInstance
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.services.plan_dispatcher_core import inject_wifi_params
from backend.services.plan_dispatcher_sync import AllocationError, _sync_allocate_devices


def _pipeline(*actions):
    return {
        "lifecycle": {
            "init": [
                {"step_id": a.split(":")[1], "action": a, "params": {}}
                for a in actions
            ]
        }
    }


def _step(pipeline, index=0):
    return pipeline["lifecycle"]["init"][index]


class TestInjectWifiParams:
    def test_no_params_leaves_pipeline_untouched(self):
        """没选网络 = 不注入。monkey_setup v2.0.0 会因此跳过 wifi 子步骤。"""
        pipeline = _pipeline("script:monkey_setup")
        inject_wifi_params(pipeline, None)
        assert _step(pipeline)["params"] == {}

    def test_blank_ssid_is_treated_as_no_selection(self):
        pipeline = _pipeline("script:monkey_setup")
        inject_wifi_params(pipeline, {"ssid": "", "password": "p"})
        assert _step(pipeline)["params"] == {}

    def test_injects_top_level_for_connect_wifi(self):
        pipeline = _pipeline("script:connect_wifi")
        inject_wifi_params(pipeline, {"ssid": "office-5G", "password": "pw"})
        assert _step(pipeline)["params"] == {"ssid": "office-5G", "password": "pw"}

    def test_injects_nested_wifi_cfg_for_monkey_setup(self):
        """monkey_setup 的 wifi 子步骤读的是 params.wifi.ssid，不是顶层。"""
        pipeline = _pipeline("script:monkey_setup")
        inject_wifi_params(pipeline, {"ssid": "office-5G", "password": "pw"})
        assert _step(pipeline)["params"] == {
            "wifi": {"ssid": "office-5G", "password": "pw"}
        }

    def test_injects_into_both_script_shapes_in_one_pipeline(self):
        pipeline = _pipeline("script:connect_wifi", "script:monkey_setup")
        inject_wifi_params(pipeline, {"ssid": "lab", "password": "x"})
        assert _step(pipeline, 0)["params"]["ssid"] == "lab"
        assert _step(pipeline, 1)["params"]["wifi"]["ssid"] == "lab"

    def test_existing_plan_values_win(self):
        """计划里写死的 ssid 不被池覆盖。"""
        pipeline = _pipeline("script:monkey_setup")
        _step(pipeline)["params"] = {"wifi": {"ssid": "hardcoded"}}
        inject_wifi_params(pipeline, {"ssid": "from-pool", "password": "pw"})
        assert _step(pipeline)["params"]["wifi"]["ssid"] == "hardcoded"
        # 密码缺失时仍补齐，否则连不上
        assert _step(pipeline)["params"]["wifi"]["password"] == "pw"

    def test_unrelated_steps_are_not_touched(self):
        pipeline = _pipeline("script:monkey_launch")
        inject_wifi_params(pipeline, {"ssid": "office", "password": "pw"})
        assert _step(pipeline)["params"] == {}


class TestWifiAllocationGate:
    def test_monkey_setup_without_pool_does_not_need_wifi(self):
        from backend.services.plan_dispatcher_core import (
            lifecycle_consumes_wifi,
            lifecycle_has_connect_wifi_step,
        )

        pipeline = _pipeline("script:monkey_setup")
        assert lifecycle_consumes_wifi(pipeline["lifecycle"]) is True
        assert lifecycle_has_connect_wifi_step(pipeline["lifecycle"]) is False

    def test_check_device_plan_does_not_consume_wifi(self):
        from backend.services.plan_dispatcher_core import lifecycle_consumes_wifi

        pipeline = _pipeline("script:check_device")
        assert lifecycle_consumes_wifi(pipeline["lifecycle"]) is False


class TestAllocateFromChosenPool:
    @pytest.fixture
    def two_pools(self, db_session):
        a = ResourcePool(
            name="office-5G", resource_type="wifi",
            config={"ssid": "office-5G", "password": "pw-a"},
            max_concurrent_devices=10, is_active=True,
        )
        b = ResourcePool(
            name="lab-test", resource_type="wifi",
            config={"ssid": "lab-test-2.4G", "password": "pw-b"},
            max_concurrent_devices=10, is_active=True,
        )
        db_session.add_all([a, b])
        db_session.commit()
        return a, b

    def test_pool_id_pins_allocation_to_the_chosen_network(self, db_session, two_pools):
        _a, b = two_pools
        result = _sync_allocate_devices(db_session, [1, 2], pool_id=b.id)
        assert {pool.id for pool, _ in result.values()} == {b.id}
        assert all(p["ssid"] == "lab-test-2.4G" for _, p in result.values())
        assert all(p["pool_name"] == "lab-test" for _, p in result.values())

    def test_without_pool_id_any_active_pool_is_eligible(self, db_session, two_pools):
        a, b = two_pools
        result = _sync_allocate_devices(db_session, [1])
        assert next(iter(result.values()))[0].id in {a.id, b.id}

    def test_inactive_chosen_pool_raises_rather_than_silently_falling_back(
        self, db_session, two_pools
    ):
        """选了个已停用的网络时必须报错——静默改连别的网络会让结果不可解释。"""
        a, b = two_pools
        b.is_active = False
        db_session.commit()

        with pytest.raises(AllocationError) as exc:
            _sync_allocate_devices(db_session, [1], pool_id=b.id)
        assert str(b.id) in str(exc.value)
        # 另一个池仍然可用，证明失败来自「限定到停用池」而非「没有池」
        assert _sync_allocate_devices(db_session, [1], pool_id=a.id)

    def test_chosen_pool_capacity_is_enforced(self, db_session, two_pools, sample_plan_run,
                                              sample_device, sample_host):
        a, _b = two_pools
        a.max_concurrent_devices = 1
        job = JobInstance(
            plan_run_id=sample_plan_run.id, plan_id=sample_plan_run.plan_id,
            device_id=sample_device.id, host_id=sample_host.id, status="RUNNING",
            pipeline_def={"lifecycle": {}},
        )
        db_session.add(job)
        db_session.flush()
        db_session.add(ResourceAllocation(
            job_instance_id=job.id, resource_pool_id=a.id, device_id=sample_device.id,
            allocated_params={},
        ))
        db_session.commit()

        with pytest.raises(AllocationError):
            _sync_allocate_devices(db_session, [sample_device.id + 100], pool_id=a.id)
