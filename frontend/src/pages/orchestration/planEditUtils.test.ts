import { describe, expect, it } from 'vitest';
import type { Plan, PipelineDef } from '@/utils/api';
import {
  EMPTY_LIFECYCLE,
  buildStepsForApi,
  draftSnapshot,
  findStepInLifecycle,
  lifecycleStepsOnlySnapshot,
  parsePlanPositiveIntInput,
  planDraftSnapshot,
  rebuildLifecycleFromPlan,
  validatePlanSettingsDraft,
  validateStepRuntimeDraft,
  watcherUnknownKeys,
  type PlanFormDraft,
} from './planEditUtils';

const basePlan: Plan = {
  id: 1,
  name: 'Smoke',
  description: 'desc',
  patrol_interval_seconds: 120,
  timeout_seconds: 3600,
  next_plan_id: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  steps: [
    {
      id: 1,
      step_key: 'step_init_1',
      script_name: 'check_device',
      script_version: '1.0.0',
      stage: 'init',
      sort_order: 0,
      timeout_seconds: 30,
      retry: 0,
      enabled: true,
    },
    {
      id: 2,
      step_key: 'step_patrol_1',
      script_name: 'monkey',
      script_version: '2.0.0',
      stage: 'patrol',
      sort_order: 0,
      timeout_seconds: 60,
      retry: 1,
      enabled: true,
    },
    {
      id: 3,
      step_key: 'step_teardown_1',
      script_name: 'cleanup',
      script_version: '1.0.0',
      stage: 'teardown',
      sort_order: 0,
      timeout_seconds: 20,
      retry: 0,
      enabled: false,
    },
  ],
};

describe('planEditUtils', () => {
  it('rebuildLifecycleFromPlan groups steps by stage and preserves patrol interval', () => {
    const lc = rebuildLifecycleFromPlan(basePlan);

    expect(lc.lifecycle.init).toHaveLength(1);
    expect(lc.lifecycle.init![0].action).toBe('script:check_device');
    expect(lc.lifecycle.patrol?.interval_seconds).toBe(120);
    expect(lc.lifecycle.patrol?.steps).toHaveLength(1);
    expect(lc.lifecycle.patrol?.steps![0].action).toBe('script:monkey');
    expect(lc.lifecycle.teardown).toHaveLength(1);
    expect(lc.lifecycle.teardown![0].enabled).toBe(false);
    expect(lc.lifecycle.timeout_seconds).toBe(3600);
  });

  it('buildStepsForApi emits PlanStepCreate rows with script: prefix stripped', () => {
    const lc: PipelineDef = {
      lifecycle: {
        init: [
          {
            step_id: 'step_init_1',
            action: 'script:check_device',
            version: '1.0.0',
            params: {},
            timeout_seconds: 30,
            retry: 0,
            enabled: true,
          },
        ],
        patrol: {
          interval_seconds: 90,
          steps: [
            {
              step_id: 'step_patrol_1',
              action: 'script:monkey',
              version: '2.0.0',
              params: {},
              timeout_seconds: 60,
              retry: 1,
              enabled: true,
            },
          ],
        },
        teardown: [],
      },
    };

    const steps = buildStepsForApi(lc);
    expect(steps).toEqual([
      expect.objectContaining({
        step_key: 'step_init_1',
        script_name: 'check_device',
        stage: 'init',
        sort_order: 0,
      }),
      expect.objectContaining({
        step_key: 'step_patrol_1',
        script_name: 'monkey',
        stage: 'patrol',
        sort_order: 0,
        retry: 1,
      }),
    ]);
  });

  it('findStepInLifecycle locates steps across init, patrol, and teardown', () => {
    const lc = rebuildLifecycleFromPlan(basePlan);

    expect(findStepInLifecycle(lc, 'step_init_1')).toEqual({ phase: 'init', index: 0 });
    expect(findStepInLifecycle(lc, 'step_patrol_1')).toEqual({ phase: 'patrol', index: 0 });
    expect(findStepInLifecycle(lc, 'step_teardown_1')).toEqual({ phase: 'teardown', index: 0 });
    expect(findStepInLifecycle(lc, null)).toEqual({ phase: null, index: -1 });
    expect(findStepInLifecycle(lc, 'missing')).toEqual({ phase: null, index: -1 });
  });

  it('EMPTY_LIFECYCLE seeds a default init step', () => {
    expect(EMPTY_LIFECYCLE.lifecycle.init).toHaveLength(1);
    expect(EMPTY_LIFECYCLE.lifecycle.init![0].action).toBe('script:check_device');
  });

  // 保存是整体替换 PlanStep 行：任何编辑器不暴露的字段只要在这条往返里漏掉，
  // 用户打开 Plan 点一次保存就会把它静默清成 NULL（生产 plan 2 / 6 中过招）。
  describe('#508 步骤级 params 往返', () => {
    const planWithParams: Plan = {
      ...basePlan,
      steps: basePlan.steps!.map((s, i) =>
        i === 0 ? { ...s, params: { apk_path: '/mnt/x/a.apk' } } : s,
      ),
    };

    it('rebuildLifecycleFromPlan 把 params 读进 lifecycle', () => {
      const lc = rebuildLifecycleFromPlan(planWithParams);
      expect(lc.lifecycle.init![0].params).toEqual({ apk_path: '/mnt/x/a.apk' });
    });

    it('buildStepsForApi 把 params 发回后端', () => {
      const steps = buildStepsForApi(rebuildLifecycleFromPlan(planWithParams));
      expect(steps.find((s) => s.step_key === 'step_init_1')?.params)
        .toEqual({ apk_path: '/mnt/x/a.apk' });
    });

    it('未配 params 的步骤不往 payload 里塞空对象', () => {
      const steps = buildStepsForApi(rebuildLifecycleFromPlan(basePlan));
      expect(steps[0]).not.toHaveProperty('params');
    });
  });

  describe('停滞钟往返', () => {
    const planWithStall: Plan = {
      ...basePlan,
      steps: basePlan.steps!.map((s, i) =>
        i === 0 ? { ...s, timeout_seconds: 600, stall_seconds: 120 } : s,
      ),
    };

    it('rebuildLifecycleFromPlan 把 stall_seconds 读进 lifecycle', () => {
      const lc = rebuildLifecycleFromPlan(planWithStall);
      expect(lc.lifecycle.init![0].stall_seconds).toBe(120);
    });

    it('buildStepsForApi 把 stall_seconds 发回后端', () => {
      const steps = buildStepsForApi(rebuildLifecycleFromPlan(planWithStall));
      expect(steps.find((s) => s.step_key === 'step_init_1')?.stall_seconds).toBe(120);
    });

    it('未配停滞钟的步骤发 null，且不往 lifecycle 里塞多余键', () => {
      const lc = rebuildLifecycleFromPlan(basePlan);
      // 脏检查靠 JSON.stringify 快照，凭空多一个键会让所有旧 Plan 一打开就显示"已修改"
      expect(lc.lifecycle.init![0]).not.toHaveProperty('stall_seconds');
      expect(buildStepsForApi(lc)[0].stall_seconds).toBeNull();
    });
  });

  describe('墙钟未配置往返（#2454）', () => {
    const planWithoutWallClock: Plan = {
      ...basePlan,
      steps: basePlan.steps!.map((s, i) => (i === 0 ? { ...s, timeout_seconds: null } : s)),
    };

    it('rebuildLifecycleFromPlan 不把 NULL 折成具体秒数', () => {
      const lc = rebuildLifecycleFromPlan(planWithoutWallClock);
      // 折成 30 会让「打开 + 保存」把「未配置」钉死成 30：脏检查/保存都是整行替换
      expect(lc.lifecycle.init![0]).not.toHaveProperty('timeout_seconds');
    });

    it('保存路径仍发 null（后端按「未配置」落库，回到回落链）', () => {
      const steps = buildStepsForApi(rebuildLifecycleFromPlan(planWithoutWallClock));
      expect(steps.find((s) => s.step_key === 'step_init_1')?.timeout_seconds).toBeNull();
    });

    it('显式配置的秒数原样往返（0 = 不限，不得折成默认）', () => {
      const planWithZero: Plan = {
        ...basePlan,
        steps: basePlan.steps!.map((s, i) => (i === 0 ? { ...s, timeout_seconds: 0 } : s)),
      };
      const lc = rebuildLifecycleFromPlan(planWithZero);
      expect(lc.lifecycle.init![0].timeout_seconds).toBe(0);
      expect(buildStepsForApi(lc)[0].timeout_seconds).toBe(0);
    });
  });

  describe('draftSnapshot / planDraftSnapshot（#966/#967/#3653 U6）', () => {
    const draft = (over: Partial<PlanFormDraft> = {}): PlanFormDraft => ({
      name: 'Smoke',
      description: 'desc',
      nextPlanId: null,
      projectKey: 'P1',
      specialtyKey: 'S1',
      suiteName: '',
      lifecycle: EMPTY_LIFECYCLE,
      patrolIntervalSeconds: null,
      timeoutSeconds: null,
      barrierTimeoutSeconds: null,
      barrierMaxWaitSeconds: null,
      autoArchiveIntervalSeconds: null,
      watcherPolicy: null,
      ...over,
    });

    it('归属项目/专项/套件任一变更都会改变脏快照（#966）', () => {
      const base = draftSnapshot(draft());
      expect(draftSnapshot(draft({ projectKey: 'P2' }))).not.toBe(base);
      expect(draftSnapshot(draft({ specialtyKey: 'S2' }))).not.toBe(base);
      expect(draftSnapshot(draft({ suiteName: 'SUITE-A' }))).not.toBe(base);
      expect(draftSnapshot(draft())).toBe(base);
    });

    it('计划设置与 watcher 变更进入脏快照；步骤图与设置分离', () => {
      const base = draftSnapshot(draft());
      expect(draftSnapshot(draft({ barrierMaxWaitSeconds: 1800 }))).not.toBe(base);
      expect(draftSnapshot(draft({ watcherPolicy: {} }))).not.toBe(base);
      const stepsOnly = lifecycleStepsOnlySnapshot(rebuildLifecycleFromPlan(basePlan));
      const settingsDirtySameSteps = draftSnapshot(
        draft({
          lifecycle: rebuildLifecycleFromPlan(basePlan),
          barrierMaxWaitSeconds: 1,
          patrolIntervalSeconds: basePlan.patrol_interval_seconds ?? null,
          timeoutSeconds: basePlan.timeout_seconds ?? null,
        }),
      );
      const settingsCleanSameSteps = draftSnapshot(
        draft({
          lifecycle: rebuildLifecycleFromPlan(basePlan),
          patrolIntervalSeconds: basePlan.patrol_interval_seconds ?? null,
          timeoutSeconds: basePlan.timeout_seconds ?? null,
        }),
      );
      expect(settingsDirtySameSteps).not.toBe(settingsCleanSameSteps);
      expect(lifecycleStepsOnlySnapshot(rebuildLifecycleFromPlan(basePlan))).toBe(stepsOnly);
    });

    it('planDraftSnapshot 与同值草稿快照一致（#967 远端比对键序稳定）', () => {
      expect(planDraftSnapshot(basePlan)).toBe(
        draftSnapshot({
          name: basePlan.name,
          description: basePlan.description || '',
          nextPlanId: basePlan.next_plan_id ?? null,
          projectKey: basePlan.project_key || '',
          specialtyKey: basePlan.specialty_key || '',
          suiteName: basePlan.suite_name || '',
          lifecycle: rebuildLifecycleFromPlan(basePlan),
          patrolIntervalSeconds: basePlan.patrol_interval_seconds ?? null,
          timeoutSeconds: basePlan.timeout_seconds ?? null,
          barrierTimeoutSeconds: basePlan.barrier_timeout_seconds ?? null,
          barrierMaxWaitSeconds: basePlan.barrier_max_wait_seconds ?? null,
          autoArchiveIntervalSeconds: basePlan.auto_archive_interval_seconds ?? null,
          watcherPolicy: basePlan.watcher_policy ? { ...basePlan.watcher_policy } : null,
        }),
      );
    });

    it('barrier 硬顶 null 不预填默认数字', () => {
      const plan = { ...basePlan, barrier_max_wait_seconds: null };
      expect(planDraftSnapshot(plan)).toContain('"barrierMaxWaitSeconds":null');
    });
  });

  describe('U6 边界校验', () => {
    it('parsePlanPositiveIntInput 拒绝 0/小数/负值，空为 null', () => {
      expect(parsePlanPositiveIntInput('')).toBeNull();
      expect(parsePlanPositiveIntInput('12')).toBe(12);
      expect(parsePlanPositiveIntInput('0')).toBe('invalid');
      expect(parsePlanPositiveIntInput('1.5')).toBe('invalid');
      expect(parsePlanPositiveIntInput('-2')).toBe('invalid');
    });

    it('validatePlanSettingsDraft：启用 patrol 时间隔必填；watcher enum/paths 拒非法', () => {
      const withPatrol = rebuildLifecycleFromPlan(basePlan);
      expect(
        validatePlanSettingsDraft({
          patrolIntervalSeconds: null,
          timeoutSeconds: 3600,
          barrierTimeoutSeconds: null,
          barrierMaxWaitSeconds: null,
          autoArchiveIntervalSeconds: null,
          watcherPolicy: null,
          lifecycle: withPatrol,
        }),
      ).toMatch(/巡检间隔/);
      expect(
        validatePlanSettingsDraft({
          patrolIntervalSeconds: 60,
          timeoutSeconds: 3600,
          barrierTimeoutSeconds: null,
          barrierMaxWaitSeconds: null,
          autoArchiveIntervalSeconds: null,
          watcherPolicy: { on_unavailable: 'nope' },
          lifecycle: withPatrol,
        }),
      ).toMatch(/fail/);
    });

    it('validateStepRuntimeDraft：timeout0 需 stall+progress；retry 上限 5', () => {
      expect(
        validateStepRuntimeDraft(
          { step_id: 's', action: 'script:x', timeout_seconds: 0, stall_seconds: 0, retry: 0 },
          ['progress_stamps'],
        ),
      ).toMatch(/停滞/);
      expect(
        validateStepRuntimeDraft(
          { step_id: 's', action: 'script:x', timeout_seconds: 0, stall_seconds: 10, retry: 0 },
          [],
        ),
      ).toMatch(/progress_stamps/);
      expect(
        validateStepRuntimeDraft(
          { step_id: 's', action: 'script:x', timeout_seconds: 0, stall_seconds: 10, retry: 6 },
          ['progress_stamps'],
        ),
      ).toMatch(/0\.\.5/);
    });

    it('watcherUnknownKeys 保留未登记子键名', () => {
      expect(watcherUnknownKeys({ enabled: true, batch_interval_seconds: 5 })).toEqual([
        'batch_interval_seconds',
      ]);
    });
  });
});
