import type {
  Plan,
  PipelineDef,
  PipelinePhase,
  PipelineStep,
  PlanStepCreate,
  WatcherPolicy,
  WatcherUnavailableAction,
} from '@/utils/api';

export const EMPTY_LIFECYCLE: PipelineDef = {
  lifecycle: {
    init: [
      {
        // 与 8 个种子模板同值（check_device 1.0.2 / 180s / retry 1）。
        // 编辑器新建的 Plan 直接提交本对象，所以这里就是**第二个生产者**：
        // 曾钉 check_device 1.0.0 + 30s + retry 0，用编辑器建的 Plan 拿不到
        // v1.0.1 的诊断修复与 v1.0.2 的 boot 门/150s 预算（#3109）。
        // 守卫：tests/test_plan_editor_empty_lifecycle_3109.py 同时解析本文件。
        step_id: 'step_init_1',
        action: 'script:check_device',
        version: '1.0.2',
        params: {},
        timeout_seconds: 180,
        retry: 1,
        enabled: true,
      },
    ],
    teardown: [],
  },
};

/** Watcher 四可编辑子键（#3653 §3.4）；其余子键只读保留。 */
export const WATCHER_EDITABLE_KEYS = [
  'enabled',
  'on_unavailable',
  'paths',
  'required_categories',
] as const;

export const WATCHER_PATH_CATEGORIES = ['AEE', 'VENDOR_AEE', 'MOBILELOG'] as const;
export type WatcherPathCategory = (typeof WATCHER_PATH_CATEGORIES)[number];

export const WATCHER_ON_UNAVAILABLE_VALUES = ['fail', 'degraded', 'skip'] as const;

export function snapshot(value: unknown): string {
  return JSON.stringify(value);
}

export function findStepInLifecycle(lc: PipelineDef, stepKey: string | null) {
  if (!stepKey) return { phase: null as PipelinePhase | null, index: -1 as number };
  const lifecycle = lc.lifecycle;
  const sources: Array<[PipelinePhase, PipelineStep[]]> = [
    ['init', lifecycle.init ?? []],
    ['patrol', lifecycle.patrol?.steps ?? []],
    ['teardown', lifecycle.teardown ?? []],
  ];
  for (const [phase, steps] of sources) {
    const idx = steps.findIndex((s) => s.step_id === stepKey);
    if (idx >= 0) return { phase, index: idx };
  }
  return { phase: null as PipelinePhase | null, index: -1 };
}

export function rebuildLifecycleFromPlan(plan: Plan): PipelineDef {
  const init: PipelineStep[] = [];
  const patrol: PipelineStep[] = [];
  const teardown: PipelineStep[] = [];

  const sorted = [...(plan.steps || [])].sort(
    (a, b) => a.stage.localeCompare(b.stage) || a.sort_order - b.sort_order,
  );

  for (const s of sorted) {
    const stepDef: PipelineStep = {
      step_id: s.step_key,
      action: `script:${s.script_name}`,
      version: s.script_version,
      // #508 步骤级 params：读回（null/缺省 → 空对象，保持 snapshot() 脏检查稳定）
      params: s.params ?? {},
      // #2454：与下面 stall_seconds 同一条规则——**无值就不写键**。此前读回把 NULL
      // 折成 30，而保存是整体替换 PlanStep 行，于是「打开 + 保存」一次就把「未配置」
      // 钉死成 30，压掉后端 `STP_STEP_WALL_CLOCK_SECONDS` → 300s 的回落链
      // （契约三态见 `components/pipeline/stepTiming.ts`）。
      ...(s.timeout_seconds != null ? { timeout_seconds: s.timeout_seconds } : {}),
      retry: s.retry ?? 0,
      enabled: s.enabled !== false,
      // 编辑器没有停滞钟输入框，但保存是整体替换 PlanStep 行：这里不读回来、
      // buildStepsForApi 不发回去，打开 Plan 点一次保存就把它清成 NULL。
      // 只在有值时写键，保持无停滞钟的 Plan 的 snapshot() 结果不变（脏检查依赖它）。
      ...(s.stall_seconds != null ? { stall_seconds: s.stall_seconds } : {}),
    };
    if (s.stage === 'init') init.push(stepDef);
    else if (s.stage === 'patrol') patrol.push(stepDef);
    else teardown.push(stepDef);
  }

  return {
    lifecycle: {
      init,
      patrol: patrol.length
        ? {
            // 结构上 PipelinePatrol.interval_seconds 是 number；真实 Plan 可能为 null。
            // 计划级间隔以 PlanFormDraft.patrolIntervalSeconds 为准，这里只保结构。
            interval_seconds: plan.patrol_interval_seconds ?? 60,
            steps: patrol,
          }
        : undefined,
      teardown,
      timeout_seconds: plan.timeout_seconds ?? undefined,
    },
  };
}

/** 仅步骤图（不含计划级巡检间隔/时长），用于判断是否需要 PUT steps。 */
export function lifecycleStepsOnlySnapshot(lifecycle: PipelineDef): string {
  return snapshot({
    init: lifecycle.lifecycle.init ?? [],
    patrolSteps: lifecycle.lifecycle.patrol?.steps ?? [],
    teardown: lifecycle.lifecycle.teardown ?? [],
  });
}

export function cloneWatcherPolicy(
  policy: WatcherPolicy | Record<string, unknown> | null | undefined,
): Record<string, unknown> | null {
  if (policy == null) return null;
  return structuredClone(policy) as Record<string, unknown>;
}

/** 从完整 watcher 草稿取出 UI 四键；未知子键不进入编辑面。 */
export function watcherEditableSlice(policy: Record<string, unknown> | null): {
  enabled?: boolean;
  on_unavailable?: WatcherUnavailableAction;
  paths?: Record<string, string[]>;
  required_categories?: string[];
} {
  if (!policy) return {};
  const out: {
    enabled?: boolean;
    on_unavailable?: WatcherUnavailableAction;
    paths?: Record<string, string[]>;
    required_categories?: string[];
  } = {};
  if ('enabled' in policy) out.enabled = Boolean(policy.enabled);
  if (
    typeof policy.on_unavailable === 'string' &&
    (WATCHER_ON_UNAVAILABLE_VALUES as readonly string[]).includes(policy.on_unavailable)
  ) {
    out.on_unavailable = policy.on_unavailable as WatcherUnavailableAction;
  }
  if (policy.paths && typeof policy.paths === 'object' && !Array.isArray(policy.paths)) {
    out.paths = policy.paths as Record<string, string[]>;
  }
  if (Array.isArray(policy.required_categories)) {
    out.required_categories = policy.required_categories.filter((x): x is string => typeof x === 'string');
  }
  return out;
}

/** 未知（非四可编辑）子键列表，供只读展示并在保存时随对象保留。 */
export function watcherUnknownKeys(policy: Record<string, unknown> | null): string[] {
  if (!policy) return [];
  const editable = new Set<string>(WATCHER_EDITABLE_KEYS);
  return Object.keys(policy).filter((k) => !editable.has(k)).sort();
}

/**
 * 解析计划级正整数设置：空 → null（继承/未设）；非法小数/负值/0 → invalid。
 * barrier 硬顶清空必须是 null（继承主机），不得预填回落链默认数字。
 */
export function parsePlanPositiveIntInput(raw: string): number | null | 'invalid' {
  if (raw.trim() === '') return null;
  if (!/^\d+$/.test(raw.trim())) return 'invalid';
  const n = Number(raw.trim());
  if (!Number.isInteger(n) || n < 1) return 'invalid';
  return n;
}

export function parseStepNonNegativeIntInput(raw: string): number | null | 'invalid' {
  if (raw.trim() === '') return null;
  if (!/^\d+$/.test(raw.trim())) return 'invalid';
  const n = Number(raw.trim());
  if (!Number.isInteger(n) || n < 0) return 'invalid';
  return n;
}

export function isWatcherOnUnavailable(value: unknown): value is WatcherUnavailableAction {
  return typeof value === 'string' && (WATCHER_ON_UNAVAILABLE_VALUES as readonly string[]).includes(value);
}

/** 编辑表单草稿的统一快照形状（isDirty 与远端变更比对共用，键序固定）。 */
export interface PlanFormDraft {
  name: string;
  description: string;
  nextPlanId: number | null;
  projectKey: string;
  specialtyKey: string;
  suiteName: string;
  lifecycle: PipelineDef;
  /** 计划级设置：从真实 Plan 加载，不用登记默认数字回填（#3653 U6）。 */
  patrolIntervalSeconds: number | null;
  timeoutSeconds: number | null;
  barrierTimeoutSeconds: number | null;
  barrierMaxWaitSeconds: number | null;
  autoArchiveIntervalSeconds: number | null;
  /** 完整 watcher 对象（含未知子键）；null=未设；{}=显式清空。 */
  watcherPolicy: Record<string, unknown> | null;
}

/**
 * 草稿快照：所有可编辑业务字段（含归属项目/专项/套件绑定，#966；含 U6 计划设置）。
 * orig 与 current 必须走同一函数，保证键序一致、脏检查稳定。
 */
export function draftSnapshot(draft: PlanFormDraft): string {
  return snapshot({
    name: draft.name,
    description: draft.description,
    nextPlanId: draft.nextPlanId,
    projectKey: draft.projectKey,
    specialtyKey: draft.specialtyKey,
    suiteName: draft.suiteName,
    steps: lifecycleStepsOnlySnapshot(draft.lifecycle),
    patrolIntervalSeconds: draft.patrolIntervalSeconds,
    timeoutSeconds: draft.timeoutSeconds,
    barrierTimeoutSeconds: draft.barrierTimeoutSeconds,
    barrierMaxWaitSeconds: draft.barrierMaxWaitSeconds,
    autoArchiveIntervalSeconds: draft.autoArchiveIntervalSeconds,
    watcherPolicy: draft.watcherPolicy,
  });
}

/** 由远端 Plan 行构造同形状草稿快照（远端变更比对，#967）。 */
export function planDraftSnapshot(plan: Plan): string {
  return draftSnapshot({
    name: plan.name,
    description: plan.description || '',
    nextPlanId: plan.next_plan_id ?? null,
    projectKey: plan.project_key || '',
    specialtyKey: plan.specialty_key || '',
    suiteName: plan.suite_name || '',
    lifecycle: rebuildLifecycleFromPlan(plan),
    patrolIntervalSeconds: plan.patrol_interval_seconds ?? null,
    timeoutSeconds: plan.timeout_seconds ?? null,
    barrierTimeoutSeconds: plan.barrier_timeout_seconds ?? null,
    barrierMaxWaitSeconds: plan.barrier_max_wait_seconds ?? null,
    autoArchiveIntervalSeconds: plan.auto_archive_interval_seconds ?? null,
    watcherPolicy: cloneWatcherPolicy(plan.watcher_policy),
  });
}

export function buildStepsForApi(lifecycle: PipelineDef): PlanStepCreate[] {
  const out: PlanStepCreate[] = [];
  const lc = lifecycle.lifecycle;
  const append = (phase: 'init' | 'patrol' | 'teardown', steps: PipelineStep[]) => {
    steps.forEach((s, i) => {
      const action = s.action || '';
      const scriptName = action.startsWith('script:') ? action.slice(7) : '';
      out.push({
        step_key: s.step_id || `step_${phase}_${i}`,
        script_name: scriptName,
        script_version: s.version || '',
        stage: phase,
        sort_order: i,
        timeout_seconds: s.timeout_seconds ?? null,
        stall_seconds: s.stall_seconds ?? null,
        // #508 步骤级 params：空对象不写键，避免既有 Plan 保存后多出
        // ``params: {}``（后端 NULL 语义 = 纯 default_params）。
        ...(s.params && Object.keys(s.params).length > 0 ? { params: s.params } : {}),
        retry: s.retry ?? 0,
        enabled: s.enabled !== false,
      });
    });
  };
  if (lc.init) append('init', lc.init);
  if (lc.patrol?.steps) append('patrol', lc.patrol.steps);
  if (lc.teardown) append('teardown', lc.teardown);
  return out;
}

/** 是否存在启用中的 patrol 步骤（与巡检间隔互相约束）。 */
export function hasEnabledPatrolSteps(lifecycle: PipelineDef): boolean {
  return (lifecycle.lifecycle.patrol?.steps ?? []).some((s) => s.enabled !== false);
}

export function validatePlanSettingsDraft(draft: {
  patrolIntervalSeconds: number | null;
  timeoutSeconds: number | null;
  barrierTimeoutSeconds: number | null;
  barrierMaxWaitSeconds: number | null;
  autoArchiveIntervalSeconds: number | null;
  watcherPolicy: Record<string, unknown> | null;
  lifecycle: PipelineDef;
}): string | null {
  const ints: Array<[string, number | null]> = [
    ['巡检间隔', draft.patrolIntervalSeconds],
    ['巡检时长', draft.timeoutSeconds],
    ['初始化同步等待预算', draft.barrierTimeoutSeconds],
    ['初始化同步累计等待硬顶', draft.barrierMaxWaitSeconds],
    ['自动归档间隔', draft.autoArchiveIntervalSeconds],
  ];
  for (const [label, value] of ints) {
    if (value != null && (!Number.isInteger(value) || value < 1)) {
      return `${label}必须是 ≥1 的整数，或留空`;
    }
  }
  if (hasEnabledPatrolSteps(draft.lifecycle) && draft.patrolIntervalSeconds == null) {
    return '存在启用的巡检步骤时，巡检间隔必须填写（≥1 秒）';
  }
  const wp = draft.watcherPolicy;
  if (wp) {
    if ('on_unavailable' in wp && wp.on_unavailable != null && !isWatcherOnUnavailable(wp.on_unavailable)) {
      return '采集不可用时只接受 fail / degraded / skip';
    }
    if ('paths' in wp && wp.paths != null) {
      if (typeof wp.paths !== 'object' || Array.isArray(wp.paths)) {
        return '采集路径必须是分类到路径数组的对象';
      }
      for (const [cat, paths] of Object.entries(wp.paths as Record<string, unknown>)) {
        if (!(WATCHER_PATH_CATEGORIES as readonly string[]).includes(cat)) {
          return `采集路径分类只接受 ${WATCHER_PATH_CATEGORIES.join(' / ')}`;
        }
        if (!Array.isArray(paths) || paths.some((p) => typeof p !== 'string')) {
          return `${cat} 路径必须是字符串数组`;
        }
      }
    }
    if ('required_categories' in wp && wp.required_categories != null) {
      if (
        !Array.isArray(wp.required_categories) ||
        wp.required_categories.some(
          (c) => typeof c !== 'string' || !(WATCHER_PATH_CATEGORIES as readonly string[]).includes(c),
        )
      ) {
        return `必需日志分类只接受 ${WATCHER_PATH_CATEGORIES.join(' / ')}`;
      }
    }
  }
  return null;
}

export function validateStepRuntimeDraft(
  step: PipelineStep,
  scriptCapabilities: string[] | undefined,
): string | null {
  const retry = step.retry ?? 0;
  if (!Number.isInteger(retry) || retry < 0 || retry > 5) {
    return '重试次数必须是 0..5 的整数';
  }
  const timeout = step.timeout_seconds;
  const stall = step.stall_seconds;
  if (timeout != null && (!Number.isInteger(timeout) || timeout < 0)) {
    return '步骤超时必须是 ≥0 的整数，或留空继承主机';
  }
  if (stall != null && (!Number.isInteger(stall) || stall < 0)) {
    return '步骤停滞上限必须是 ≥0 的整数，或留空';
  }
  if (timeout === 0) {
    if (stall == null || stall < 1) {
      return '超时=0（不限）时必须同时配置停滞上限 ≥1 秒';
    }
    if (!scriptCapabilities?.includes('progress_stamps')) {
      return '超时=0 且配置停滞钟时，脚本版本必须声明 progress_stamps 能力';
    }
  }
  if (stall != null && stall > 0 && !scriptCapabilities?.includes('progress_stamps')) {
    return '停滞上限 >0 要求脚本版本已接入 PROGRESS（progress_stamps）';
  }
  return null;
}
