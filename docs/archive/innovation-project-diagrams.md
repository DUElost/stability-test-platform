# 创新项目申报附件图示（正式版）

## 1. 平台总体架构图

```mermaid
flowchart TB
    subgraph U["用户与展示层"]
        FE["前端 Web Dashboard<br/>任务创建 / 状态监控 / 日志查看 / 报表展示"]
    end

    subgraph C["控制面 Control Plane"]
        API["FastAPI API / WebSocket"]
        ORCH["编排层<br/>WorkflowDefinition / WorkflowRun"]
        SCH["调度层<br/>TaskTemplate / JobInstance / Dispatcher / Cron"]
        POST["后处理<br/>Report / JIRA Draft / Summary"]
    end

    subgraph D["数据与消息层"]
        DB["PostgreSQL<br/>元数据 / 状态 / 结果"]
        MQ["Redis / MQ<br/>控制指令 / 状态流 / 日志流"]
        FS["中心存储 / 文件系统 / MinIO<br/>日志归档 / 附件产物"]
    end

    subgraph L["连通性验证层"]
        CONN["SSH / 挂载点 / 主机可达性验证"]
    end

    subgraph E["执行面 Execution Plane"]
        AG1["Linux Host Agent A"]
        AG2["Linux Host Agent B"]
        AGN["Linux Host Agent N"]
    end

    subgraph T["终端设备层"]
        DV["Android Device 集群<br/>1000+ 设备"]
    end

    FE --> API
    API --> ORCH
    API --> SCH
    ORCH --> POST
    ORCH --> DB
    SCH --> DB
    POST --> DB
    POST --> FS
    API <--> MQ
    SCH --> MQ
    CONN --> API
    CONN --> AG1
    CONN --> AG2
    CONN --> AGN
    MQ <--> AG1
    MQ <--> AG2
    MQ <--> AGN
    AG1 <--> DV
    AG2 <--> DV
    AGN <--> DV
    AG1 --> FS
    AG2 --> FS
    AGN --> FS
```

## 2. 控制面与 Agent 协同流程图

```mermaid
sequenceDiagram
    participant User as 用户/前端
    participant CP as 控制面 API/调度器
    participant DB as 数据库
    participant Agent as Linux Host Agent
    participant Device as Android Device
    participant Post as 报告/JIRA后处理

    User->>CP: 创建 Workflow / 触发任务
    CP->>DB: 写入 WorkflowRun / JobInstance(PENDING)
    Agent->>CP: 周期心跳（主机/设备/挂载状态）
    CP->>DB: 更新 Host / Device 状态
    Agent->>CP: 拉取并认领待执行任务
    CP->>DB: JobInstance PENDING -> RUNNING
    CP-->>Agent: 下发 pipeline_def / task params
    Agent->>Device: 执行专项测试步骤
    Agent-->>CP: 回传日志 / 进度 / StepTrace / 心跳
    CP->>DB: 持久化运行轨迹与状态
    Agent->>CP: 上报完成结果 / 产物摘要
    CP->>DB: JobInstance 进入终态
    CP->>Post: 触发报告汇总与 JIRA Draft 生成
    Post->>DB: 写入 report_json / jira_draft_json
    CP-->>User: 展示状态、日志、报告与问题草稿
```

## 3. 稳定性专项测试标准流程图

```mermaid
flowchart LR
    A["1 设备连接检测"]
    B["2 前置准备"]
    C["3 资源填充"]
    D["4 开始运行测试"]
    E["5 日志检测"]
    F["6 风险问题检查"]
    G["7 日志回传导出"]
    H["8 结束测试"]
    I["9 测试后置"]

    A --> B --> C --> D --> E --> F --> G --> H --> I
```

## 4. 任务调度与状态流转图

```mermaid
flowchart LR
    S["TaskSchedule / 手动触发"]
    C["CronScheduler / Workflow Run"]
    D["Dispatcher 扇出<br/>按 设备 × 模板 创建 JobInstance"]
    P["PENDING<br/>等待 Agent 认领"]
    T["PENDING_TOOL<br/>工具未就绪"]
    R["RUNNING<br/>执行中 / 心跳续租 / StepTrace 回传"]
    U["UNKNOWN<br/>主机心跳超时 / 状态待确认"]
    OK["COMPLETED"]
    F["FAILED"]
    A["ABORTED"]
    W["Workflow 聚合<br/>SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED"]

    S --> C --> D --> P
    P -->|Agent claim| R
    P -->|工具缺失/版本异常| T
    T -->|工具就绪后重排队| P
    R -->|全部步骤成功| OK
    R -->|步骤失败| F
    R -->|看门狗终止| A
    R -->|Host 心跳超时| U
    U -->|Agent 恢复| R
    U -->|补报完成| OK
    U -->|宽限期超时| F
    OK --> W
    F --> W
    A --> W
    U --> W
```

## 5. 图示说明

- 平台总体架构图用于展示“控制面 + 执行面 Agent + 连通性验证层 + 数据层”的整体协同关系。
- 控制面与 Agent 协同流程图用于展示从任务创建、心跳上报、任务认领、执行回传到报告/JIRA 后处理的闭环。
- 稳定性专项测试标准流程图严格对应项目既有九阶段标准流程，可直接用于申报附件或汇报材料。
- 任务调度与状态流转图用于展示从计划触发、任务扇出、Agent 认领到 Job 状态迁移和 Workflow 聚合判定的完整链路。
