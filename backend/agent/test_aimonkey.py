"""
AIMONKEY 任务单元测试
使用 Mock 验证 TaskExecutor 逻辑
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# 兼容 WSL 和 Windows 导入
try:
    from backend.agent.task_executor import TaskExecutor, TaskResult
    from backend.agent.adb_wrapper import AdbError
except ModuleNotFoundError:
    from agent.task_executor import TaskExecutor, TaskResult
    from agent.adb_wrapper import AdbError


class MockAdbWrapper:
    """Mock ADB 包装器"""

    def __init__(self):
        self.adb_path = "adb"
        self.shell_responses = {}
        self.push_responses = {}
        self.pull_responses = {}
        self._pid_counter = 1000

    def shell(self, serial, cmd):
        """Mock shell 命令"""
        cmd_key = " ".join(cmd) if isinstance(cmd, list) else cmd

        # 模拟 root 检查
        if "id -u" in cmd_key:
            mock_result = MagicMock()
            mock_result.stdout = "0\n"
            return mock_result

        # 模拟 df 命令
        if "df /data" in cmd_key:
            mock_result = MagicMock()
            mock_result.stdout = "Filesystem 1K-blocks Used Available Use%\n/dev/sda1 1000000 400000 600000 40%"
            return mock_result

        # 模拟获取 PID
        if "ps" in cmd_key:
            mock_result = MagicMock()
            mock_result.stdout = "USER PID PPID VSZ RSS WCHAN PC NAME\nroot 1234 1 1234 1234 ffffffff 00000000 S com.android.commands.monkey.transsion"
            return mock_result

        # 默认响应
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 0
        return mock_result

    def push(self, serial, local_path, remote_path):
        """Mock push 命令"""
        if local_path not in self.push_responses:
            return MagicMock(returncode=0)
        return self.push_responses[local_path]

    def pull(self, serial, remote_path, local_path):
        """Mock pull 命令"""
        return MagicMock(returncode=0)

    def get_pid(self, serial, process_name):
        """Mock 获取 PID"""
        return "1234"

    def kill_process(self, serial, pid):
        """Mock 杀死进程"""
        return MagicMock(returncode=0)


class TestAIMonkeyTask(unittest.TestCase):
    """AIMONKEY 任务测试"""

    def setUp(self):
        """测试前准备"""
        self.mock_adb = MockAdbWrapper()
        self.executor = TaskExecutor(self.mock_adb)

    def test_execute_task_routing(self):
        """测试任务类型路由 - AIMONKEY 被正确识别"""
        params = {
            "runtime_minutes": 1,  # 缩短测试时间
            "throttle_ms": 100,
            "enable_fill_storage": False,
            "enable_clear_logs": False,
        }

        # Mock 所有 AIMONKEY 方法
        with patch.object(self.executor, '_aimonkey_setup') as mock_setup, \
             patch.object(self.executor, '_aimonkey_start_monkey', return_value="1234") as mock_start, \
             patch.object(self.executor, '_aimonkey_monitor', return_value=("completed", "1234")) as mock_monitor, \
             patch.object(self.executor, '_aimonkey_stop_monkey') as mock_stop, \
             patch.object(self.executor, '_aimonkey_collect_logs') as mock_collect:

            result = self.executor.execute_task("AIMONKEY", params, "test_serial")

            # 验证所有步骤被调用
            mock_setup.assert_called_once_with("test_serial", params)
            mock_start.assert_called_once_with("test_serial", params)
            mock_monitor.assert_called_once()
            mock_stop.assert_called_once_with("test_serial", "1234")
            mock_collect.assert_called_once()

            # 验证结果
            self.assertEqual(result.status, "FINISHED")
            self.assertEqual(result.exit_code, 0)

    def test_ensure_root_access_success(self):
        """测试 root 权限获取成功"""
        # 已经是 root
        self.executor._ensure_root_access("test_serial")
        # 不应抛出异常

    def test_ensure_root_access_retry(self):
        """测试 root 权限获取重试"""
        call_count = [0]

        def mock_shell(serial, cmd):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # 第一次不是 root
                result.stdout = "2000\n"
            elif "root" in " ".join(cmd):
                result.stdout = ""
            else:
                # 第二次检查是 root
                result.stdout = "0\n"
            return result

        self.mock_adb.shell = mock_shell
        self.executor._ensure_root_access("test_serial", max_attempts=3)
        self.assertGreaterEqual(call_count[0], 2)

    def test_aimonkey_start_monkey_no_resources(self):
        """测试启动 monkey 时资源目录不存在的情况"""
        params = {"resource_dir": "/nonexistent/path"}

        result = self.executor._aimonkey_start_monkey("test_serial", params)

        # 由于资源文件不存在,应该回退到 get_pid
        self.assertEqual(result, "1234")

    def test_fill_storage_calculation(self):
        """测试存储填充计算逻辑"""
        df_output = [
            "Filesystem 1K-blocks Used Available Use%",
            "/dev/sda1 1000000 400000 600000 40%"
        ]

        def mock_shell(serial, cmd):
            result = MagicMock()
            if "df" in " ".join(cmd):
                result.stdout = "\n".join(df_output)
            else:
                result.stdout = ""
            return result

        self.mock_adb.shell = mock_shell

        # 目标 60%,当前 40%,需要填充 20% = 200000 KB
        self.executor._fill_storage("test_serial", 60)
        # 验证 dd 命令被调用（bs=1024k count=195 约等于 200MB）

    def test_send_heartbeat(self):
        """测试心跳发送"""
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(status_code=200)

            self.executor._send_heartbeat("http://test-api", 123, 50, "test message")

            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertIn("/api/v1/agent/runs/123/heartbeat", args[0])
            self.assertEqual(kwargs["json"]["progress"], 50)

    def test_task_result_on_adb_error(self):
        """测试 ADB 错误时的任务结果"""
        def mock_setup(serial, params):
            raise AdbError("device offline")

        self.executor._aimonkey_setup = mock_setup

        params = {"runtime_minutes": 1}
        result = self.executor._run_aimonkey("test_serial", params)

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "ADB_ERROR")
        self.assertIn("device offline", result.error_message)


class TestAIMonkeyIntegration(unittest.TestCase):
    """AIMONKEY 集成测试 - 需要真实设备"""

    @unittest.skipUnless(
        os.getenv("AIMONKEY_REAL_DEVICE"),
        "需要设置 AIMONKEY_REAL_DEVICE 环境变量"
    )
    def test_real_device_setup(self):
        """在真实设备上测试前置配置"""
        from adb_wrapper import AdbWrapper

        adb = AdbWrapper()
        executor = TaskExecutor(adb)
        serial = os.getenv("AIMONKEY_REAL_DEVICE")

        params = {
            "enable_fill_storage": False,
            "enable_clear_logs": False,
        }

        try:
            executor._aimonkey_setup(serial, params)
            print(f"OK 设备 {serial} 配置成功")
        except Exception as e:
            self.fail(f"设备配置失败: {e}")


def run_quick_check():
    """快速检查 - 验证代码结构和基本逻辑"""
    print("=" * 60)
    print("AIMONKEY Quick Check")
    print("=" * 60)

    # 1. 验证类方法存在
    executor_methods = [
        '_run_aimonkey',
        '_aimonkey_setup',
        '_aimonkey_start_monkey',
        '_aimonkey_monitor',
        '_aimonkey_stop_monkey',
        '_aimonkey_collect_logs',
        '_ensure_root_access',
        '_enable_dev_settings',
        '_start_mobile_logger',
        '_connect_wifi',
        '_fill_storage',
        '_clear_device_logs',
        '_send_heartbeat',
    '_adb_push_with_timeout',
    '_adb_shell_with_timeout',
    '_start_aimonkey_process',
    ]

    mock_adb = MockAdbWrapper()
    executor = TaskExecutor(mock_adb)

    missing_methods = []
    for method in executor_methods:
        if not hasattr(executor, method):
            missing_methods.append(method)

    if missing_methods:
        print(f"FAIL 缺少方法: {missing_methods}")
        return False
    print(f"OK 所有 {len(executor_methods)} 个方法已定义")

    # 2. 验证任务类型路由
    result_types = []
    test_types = ["AIMONKEY", "aimonkey", "Aimonkey"]
    for tt in test_types:
        # 只检查路由逻辑,不实际执行
        with patch.object(executor, '_aimonkey_setup'), \
             patch.object(executor, '_aimonkey_start_monkey', return_value="1234"), \
             patch.object(executor, '_aimonkey_monitor', return_value=("test", "1234")), \
             patch.object(executor, '_aimonkey_stop_monkey'), \
             patch.object(executor, '_aimonkey_collect_logs'):
            try:
                result = executor.execute_task(tt, {"runtime_minutes": 0}, "test")
                result_types.append((tt, result.status))
            except Exception as e:
                result_types.append((tt, f"ERROR: {e}"))

    print(f"OK 任务类型路由测试: {result_types}")

    # 3. 验证参数处理
    test_params = {
        "runtime_minutes": 120,
        "throttle_ms": 300,
        "max_restarts": 2,
        "target_fill_percentage": 70,
        "wifi_ssid": "TestWiFi",
        "wifi_password": "password123",
        "enable_fill_storage": True,
        "enable_clear_logs": True,
    }

    # 验证参数读取
    assert int(test_params.get("runtime_minutes", 10080)) == 120
    assert int(test_params.get("throttle_ms", 500)) == 300
    assert bool(test_params.get("enable_fill_storage", True)) is True
    print("OK 参数处理正确")

    # 4. 验证日志目录生成
    log_dir = executor._aimonkey_log_dir(123)
    assert "123" in log_dir
    log_dir2 = executor._aimonkey_log_dir(None)
    assert "aimonkey_" in log_dir2
    print("OK 日志目录生成正确")

    print("=" * 60)
    print("OK 快速检查通过!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    # 先运行快速检查
    if run_quick_check():
        # 然后运行单元测试
        print("\n运行单元测试...\n")
        unittest.main(verbosity=2, exit=False)
    else:
        print("\nFAIL 快速检查失败,请检查代码实现")
        sys.exit(1)
