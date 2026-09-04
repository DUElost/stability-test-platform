# 外部工具统一接入架构与包管理实施计划（Tool-Kit Ecosystem Implementation Plan）

- 状态：**Living**
- 日期：2026-09-03
- 对应决策：[`ADR-0033：外部工具统一接入契约规范与包管理解耦模型`](../adr/ADR-0033-tool-kit-ecosystem-integration.md)
- 追踪 Issue：[#745](https://github.com/DUElost/stability-test-platform/issues/745)
- 涉及范围：控制面 SAQ 任务链路、Agent 脚本执行器（PipelineEngine）、script catalog 注册流、中心存储布局、外部专项执行包（MTK/展锐去重、Jira 提单、GPU、开关机、休眠唤醒）

---

## 1. 架构全景与分层模型

为解决原厂工具与自研专项在线下独立可运行、接入平台后频繁引发代码腐化的问题，STP 平台将外部工具根据**运行宿主与生命周期**严格划分为三层，每一层均通过**标准适配器（Adapter）**接入系统：

```mermaid
graph TD
    subgraph ControlPlane ["1. 控制面 (Platform Tool 运行域)"]
        SAQ["SAQ Worker 队列"] --> AdapterBE["Platform Tool Adapter<br>(如: DedupMergeEngine)"]
        AdapterBE --> ToolBE["外部批处理工具<br>• MTK 去重 (start_log_scan)<br>• 展锐去重 (Scan-Result-GT)<br>• Jira 自动提单工具"]
    end

    subgraph HostNode ["2. 测试主机 (Host Tool 运行域)"]
        Agent["STP Agent Daemon"] --> AdapterHost["Host Tool Adapter<br>(独立子进程监控)"]
        AdapterHost --> ToolHost["外部日志扫描与采集器<br>• 展锐 Uniview/YPLog 扫描<br>• MTK AEE 日志扫描"]
    end

    subgraph DeviceCluster ["3. 被测设备 (Device Tool 运行域)"]
        Pipeline["Agent PipelineEngine"] --> AdapterDev["Device Tool Adapter<br>(PlanStep 容器)"]
        AdapterDev --> ToolDev["外部专项压测执行包<br>• GPU 压测 (Antutu APK)<br>• 开关机稳定性测试<br>• 休眠唤醒专项测试"]
        ToolDev --> AndroidDevice["Android Target Device (ADB)"]
    end

    subgraph SharedStorage ["统一资产与产物仓 (NFS / CIFS)"]
        PkgStore["{STP_AEE_NFS_ROOT}/tools/<br>版本化工具包 (.tar.gz)"] -.->|按需下载并校验 sha256| ControlPlane
        PkgStore -.->|按需下载并校验 sha256| HostNode
    end
```

---

## 2. STP Standard Tool Contract 统一契约规范

任何合入平台的外部工具，必须通过其 Adapter 对齐如下四项标准契约。

### 2.1 输入契约：`context.json` 规范
平台在调用工具时，通过命令行参数指定输入配置路径：
```bash
entrypoint --context /path/to/context.json --output-dir /path/to/output_dir
```
`context.json` 包含了该步骤运行所需的所有上下文信息，彻底杜绝在平台核心代码中手动拼接命令行参数：
```json
{
  "$schema": "https://stp.internal/schemas/tool-context.v1.json",
  "plan_run_id": 1024,
  "job_id": 2048,
  "execution_tier": "device",
  "device": {
    "serial": "DEVICE_SERIAL_EXAMPLE",
    "platform": "unisoc",
    "model": "MYOS16-Z2581",
    "build_version": "V1.0.0B05"
  },
  "params": {
    "test_duration_hours": 12,
    "interval_seconds": 30,
    "skip_if_exists": true
  },
  "secrets": {
    "_comment": "机密凭据按层隔离，仅 platform 级工具注入，严禁透传至 device 端"
  },
  "support_files": {
    "gpu_apk": "/local/cache/support_files/antutu_v10.apk"
  }
}
```

### 2.2 输出契约：`summary.json` 与 `artifacts/` 目录规范
工具执行结束时，必须在 `--output-dir` 下输出结构化指标文件 `summary.json`，并将原始报告与日志统一收拢在 `artifacts/`：
```text
output_dir/
├── summary.json          # 平台解析的权威结构化指标
└── artifacts/            # 待上送或归档的全部原始文件
    ├── run_log.txt
    ├── Result_Dedup.xls
    └── stack_traces/
```
`summary.json` 数据结构定义：
```json
{
  "tool_name": "gpu_check",
  "tool_version": "1.2.0",
  "status": "PASS",
  "duration_seconds": 3612.5,
  "metrics": {
    "total_cycles": 100,
    "success_cycles": 99,
    "crash_count": 1,
    "avg_fps": 58.4
  },
  "failure_reason": null,
  "artifacts": [
    "artifacts/run_log.txt",
    "artifacts/Result_Dedup.xls"
  ]
}
```

### 2.3 退出码语义（Fail-Fast 区分）
进程退出码是平台判定任务终态与触发重试的关键依据。**命名空间分层**：工具作者只拥有 `{0, 1, 2}`；`≥124` 为平台执行器保留；`3–123` 与信号死亡视为工具缺陷：
* `0`：**PASS / SUCCESS**。任务正常完成，用例通过；
* `1`：**TEST_FAILURE**。业务断言未通过（如被测设备发生重启、用例跑出 Crash）。平台记录为测试失败，生成失败报告；
* `2`：**ENVIRONMENT_ERROR**。执行环境异常（如 ADB 断连、目标目录无写权限、原厂脚本底层报错）。平台将其定性为环境故障，可触发重试或设备隔离巡检；
* `3–123` / 信号死亡：**工具缺陷**。按 ENVIRONMENT_ERROR 处理并告警（工具不得在该区间自定义语义）；
* `124` / `125`：**TIMEOUT (Wall Clock / Stall Clock)**。由平台执行器双层钟沙箱强制终止时产生，工具自身不得伪造。

退出码到步骤终态的完整映射（含 `failure_kind` 标注与重试资格）见 §2.5。

### 2.4 环境预检契约：`--check-env`
外部工具必须支持 `--check-env` 命令行参数。该命令须在 **5 秒内执行完毕**，检查：
1. 依赖的二进制（如 `adb`、Python 虚拟环境库）是否存在；
2. 目标设备的 ADB 连通性及 root 权限状态；
3. **判据标准**：以退出码 `0`（就绪）与 `2`（环境故障）为准；stdout 输出诊断 JSON：`{"ready": true, "error": null}`。

**挂点**：Tier 3 设备端工具由 Agent 在步骤启动前调用（复用现有 precheck 槽位——ADB 连通性只有持有设备的主机可判）；Tier 1 平台工具由 SAQ 任务在长任务前自调。未就绪 → 步骤不启动、按 ENV_ERROR + `precheck_failed` 记录，不计入测试失败统计。

### 2.5 契约与现态执行器的衔接（双轨运行声明）

**总原则：契约翻译发生在 Agent 边缘，控制面状态机零改动。** ADR-0026 四层调度、Job/PlanRun 状态机、watcher 全程不感知契约存在。

1. **`summary.json` 的消费者是 Agent 执行器内的 contract 分支**，结果落 step_trace 现有字段体系；控制面继续读取与现态相同的 Job/PlanRun 数据。
2. **双轨开关不加新表**：script 行登记契约能力（沿用 `capabilities` JSONB 机制，#171 先例），控制面 dispatch payload 携带该标志，Agent 据此分支（Agent 不读 DB，符合现态派发模型）。
3. **退出码 → 步骤终态映射**（`failure_kind` 为 step_trace 标注字段，**不新增状态机终态**；重试沿用现有重试旋钮，本 ADR 不新造重试机制）：

   | 进程结果 | outcome 类 | 步骤终态 | 重试资格 |
   |---|---|---|---|
   | `0` | PASS | SUCCESS | — |
   | `1` | TEST_FAILURE | FAILED + `failure_kind=assert` | 否（计入质量统计） |
   | `2` / `3–123` / 信号死亡 | ENV_ERROR | FAILED + `failure_kind=env` | 是 |
   | `124` / `125` | TIMEOUT_WALL / STALL | FAILED + `failure_kind=env` | 是 |

4. **stdout → 文件的过渡即 secrets 治理**：contract 步骤的结果以 `summary.json` 为权威，stdout 不再要求结构化；契约禁止工具将 context 内容回显至 stdout/stderr（step_trace 收割 stdout/stderr 落库）；secrets 仅在 `execution_tier: "platform"` 的 context 注入。
5. **存量兼容 = 显式双轨**：legacy 路径（argv + stdout JSON）原样保留；新工具族必须 contract + 包存储；既有族的新版本目录允许继续 legacy，直至该族 Phase 3 迁移（对齐 ADR-0033 D0 分级准入）。

---

## 3. 工具包分发、版本化与本地缓存机制

### 3.1 元数据清单：`tool_manifest.yaml`
外部工具以配置清单形式在平台登记，主仓内不再存放工具实现代码：
```yaml
name: unisoc_scan_result
version: "1.0.4"
tier: "platform"               # platform / host / device
description: "展锐 YPLog 汇总去重与 Excel 生成工具"
package:
  source: "nfs://tools/unisoc_scan_result/1.0.4/unisoc_scan_result-1.0.4.tar.gz"
  sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
entrypoint:
  runtime: "python"
  cmd: "python -m unisoc_scan_result.adapter"
capabilities:
  platforms: ["unisoc"]
  timeout_seconds: 1800
```

### 3.2 共享存储布局与 Agent 本地缓存
- **中心存储归档点**：
  `{STP_AEE_NFS_ROOT}/tools/{name}/{version}/{name}-{version}.tar.gz`
- **Agent / 控制面工作节点本地缓存**：
  本地维护 `tools_cache/{name}/{version}/`：
  1. 检查本地缓存是否存在且 `sha256sum` 与 Manifest 一致；
  2. 若不存在，自共享存储复制到本地并解压；
  3. 执行器在隔离的工作目录（Working Dir）中运行，避免多进程并发执行产生文件写入冲突；
  4. 支持基于最后访问时间的 LRU 磁盘清理策略，保持本地磁盘水位健康。

### 3.3 注册流与门禁分工

**发布流**（对齐 ADR-0033 D3 双版本体系权威裁定——DB script catalog 仍是唯一运行时权威，manifest 是发布格式）：

1. 构建 `{name}-{version}.tar.gz` 并上传至 `{STP_AEE_NFS_ROOT}/tools/{name}/{version}/`；
2. 注册（扩展现有 script catalog scan，读取 `tool_manifest.yaml`）创建**新的 script 版本行**，`content_sha256 := tarball sha256`，并登记入口 / tier / 契约能力；
3. 此后一切照旧：ADR-0020 不可变与 422、`plan_step` 引用、退役 409 守卫（`SCRIPT_STILL_REFERENCED`）原样复用，零新机制。

**CI 门禁分工**（GitHub Actions runner 无 NFS 访问，这是硬约束）：

| 校验 | 在哪里做 | 时机 |
|---|---|---|
| manifest YAML schema lint | PR 门禁（Git 侧） | 每次 PR |
| 已登记版本条目 append-only（不可变） | PR 门禁（Git 侧） | 每次 PR |
| tarball 存在性 + sha256 一致 | 控制面 | 注册时 |
| tarball sha256 防篡改复核 | Agent | 每次拉取（D3） |
| 包存储健康巡检（缺失 / 损坏清单） | 控制面 | 周期任务 |

---

## 4. 典型外部工具接入实现指南

### 4.1 控制面汇总去重：统一 `DedupMergeEngine` 适配器
针对 MTK 与展锐两种并列去重工具，控制面抽离通用抽象基类。**接口收窄**：基类只包 vendor CLI 调用（argv 构造、子进程执行、产物校验、错误映射）；round 选择、水位线、发布中心与产物注册等编排留在 `backend/services/dedup_scan.py` 编排层调用引擎：
```python
# backend/services/dedup/base.py
from abc import ABC, abstractmethod
from pathlib import Path
from pydantic import BaseModel

class MergeResult(BaseModel):
    produced_files: list[Path]   # 本轮 merge 产出的文件清单
    total_issues: int
    deduped_issues: int
    status: str

class DedupMergeEngine(ABC):
    @abstractmethod
    def run_merge(self, input_files: list[Path], work_dir: Path) -> MergeResult:
        """执行单次 vendor 去重合并（不负责 round/水位线/发布编排）"""
        pass
```
现态事实：`backend/tasks/saq_tasks.py` 的 `merge_task` 调用 service 层 `run_merge_all_platforms_sync`（同一扫描工具按 mtk/unisoc 分区跑两遍），厂商专用参数（`build_merge_argv`、`-side`、merge 产物目录探测）在 **service 层** `dedup_scan.py`。迁移路径：unisoc 分区先行接入第一个引擎实现（展锐 `Scan-Result-GT`，#463 P2，插在 ADR-0032 已建的 per-platform 循环内），mtk 分支随后以行为等价验收迁入。

### 4.2 设备端专项测试：标准 PlanStep 适配器
针对 GPU（Antutu）、开关机、休眠唤醒专项，统一采用标准化 Python 适配器模板：
```python
# 示例：gpu_check 适配器标准结构
import argparse, json, sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--check-env", action="store_true")
    args = parser.parse_args()

    if args.check_env:
        # 5 秒快速自检：ADB 通道、依赖与 APK 就绪（退出码权威：0=就绪 / 2=未就绪）
        ready, error = check_env()
        print(json.dumps({"ready": ready, "error": error}))
        sys.exit(0 if ready else 2)

    context = json.loads(Path(args.context).read_text())
    output_dir = Path(args.output_dir)
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. 设备指纹读取与自适应（依照 issue #507 要求）
    model = context["device"]["model"]
    # 2. 调用具体的专项测试驱动逻辑
    # ...
    # 3. 产生标准 summary.json
    summary = {
        "tool_name": "gpu_check",
        "status": "PASS",
        "metrics": {"cycles": 50, "crashes": 0}
    }
    (output_dir / "summary.json").write_text(json.dumps(summary))
    sys.exit(0)

if __name__ == "__main__":
    main()
```

### 4.3 自动化 Jira 提单工具解耦
- 各厂商专有凭据（Transsion Cookie / Tinno P12 证书 / Moto PAT Token）由工具内部自身配置管理，平台仅提供标准输入参数（`jira_project_key`、`summary`、`issue_type`）；
- 提单执行过程输出标准日志流（透传至前端终端组件）；
- 提单结果统一回填结构化的 `jira_issue_key`（如 `PROJ-1234`）到数据库 `jira_run` 表。

---

## 5. 分阶段实施路线图（Phased Implementation Plan）

```mermaid
gantt
    title ADR-0033 落地排期与里程碑
    dateFormat  YYYY-MM-DD
    section Phase 1 规范与止血
    发布 ADR-0033 与实施设计文档           :done, 2026-09-03, 1d
    阻断新工具源码全量拷贝入仓             :active, 2026-09-03, 3d
    推进 Issue #735 退役 47 个历史空脚本   :2026-09-04, 3d
    section Phase 2 标杆打样接入
    展锐去重工具 DedupMergeEngine 适配    :2026-09-07, 5d
    Agent 工具包拉取、解压与校验缓存机制  :2026-09-12, 5d
    GPU/开关机/休眠唤醒三专项标准模板打样  :2026-09-17, 7d
    section Phase 3 存量收敛
    存量 MTK 去重与 Jira 提单适配器收敛   :2026-09-24, 7d
    前端管理面暴露 Tool Manifest 管理     :2026-09-29, 7d
```

> 依赖序：设备端三专项打样排在工具包缓存机制之后（模板打样依赖包分发通道就绪），并对齐可行性评审（2026-08-26）的前置依赖序（如 G1 上传 API 先于方向 5）。

---

## 6. 验收标准与架构守卫（DoD & CI Gates）

1. **主代码仓零外部源码膨胀**（可判定规则，非口号）：
   - PR 门禁：`backend/agent/scripts/` 新增顶层工具目录必须伴随对应 `tool_manifest.yaml` 注册记录，否则 gate 红；
   - PR 门禁：manifest 已登记版本条目 append-only（tarball 校验不在 PR CI——runner 无 NFS 访问，分工见 §3.3）；
2. **契约自测套件**：
   - 编写 `tools/dev/verify_tool_contract.py`，任何新接入的工具适配器必须能通过标准测试（验证 `--check-env`、退出码映射与 `summary.json` 合规性）——§4.2 模板即测试靶子，必须真正实现契约；
3. **环境去黑盒化**：
   - 新工具接入不再向平台根 `.env.backend` 引入工具私有路径变量，全部收拢至 Manifest 与工具本地配置中。
