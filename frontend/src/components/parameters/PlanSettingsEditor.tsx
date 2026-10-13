import { useId, useMemo, useState } from 'react';
import type {
  ParameterItem,
  ParameterProjection,
  ParameterState,
  WatcherUnavailableAction,
} from '@/utils/api/types';
import { PIPELINE_EDITOR, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { ParameterProjectionList } from '@/components/parameters/ParameterProjectionList';
import {
  WATCHER_EDITABLE_KEYS,
  WATCHER_ON_UNAVAILABLE_VALUES,
  WATCHER_PATH_CATEGORIES,
  parsePlanPositiveIntInput,
  watcherEditableSlice,
  type WatcherPathCategory,
} from '@/pages/orchestration/planEditUtils';

export interface PlanSettingsValues {
  patrolIntervalSeconds: number | null;
  timeoutSeconds: number | null;
  barrierTimeoutSeconds: number | null;
  barrierMaxWaitSeconds: number | null;
  autoArchiveIntervalSeconds: number | null;
  watcherPolicy: Record<string, unknown> | null;
}

export interface PlanSettingsEditorProps {
  values: PlanSettingsValues;
  onChange: (next: PlanSettingsValues) => void;
  projection?: ParameterProjection;
  unavailable?: boolean;
  isDraft?: boolean;
  readOnly?: boolean;
  /** 路径分类被清空时的提示（按分类回落代码默认）。 */
  pathFallbackHints?: Partial<Record<WatcherPathCategory, boolean>>;
}

/** 与 ParameterProjectionList 同义的状态文案；仅用于控件旁投影提示，不复制业务语义。 */
const STATE_LABEL: Record<ParameterState, string> = {
  explicit: '已确定',
  unset_definite: '未设置',
  env_fallback: '沿回落链',
  pending_dispatch: '待派发确定',
  actual: '已下发',
};

const EDITABLE_PLAN_SETTING_KEYS = new Set([
  'patrol_interval_seconds',
  'timeout_seconds',
  'barrier_timeout_seconds',
  'barrier_max_wait_seconds',
  'auto_archive_interval_seconds',
]);

const EDITABLE_WATCHER_KEY_SET = new Set<string>(WATCHER_EDITABLE_KEYS);

function settingMeta(projection: ParameterProjection | undefined, key: string) {
  return projection?.plan_settings.find((item) => item.path.length === 1 && item.path[0] === key);
}

function watcherMeta(projection: ParameterProjection | undefined, key: string) {
  return projection?.watcher_policy.items.find((item) => item.path.length === 1 && item.path[0] === key);
}

function ProjectionStateHint({ item, unsetHint }: { item?: ParameterItem; unsetHint?: string }) {
  if (!item) return null;
  return (
    <p
      className={cn('text-[11px] leading-snug', TEXT.subtitle)}
      data-testid={`projection-state-${item.path.join('.')}`}
    >
      状态：{STATE_LABEL[item.state] ?? item.state}
      {item.fallback_chain ? ` · 回落链：${item.fallback_chain}` : ''}
      {!item.is_set && unsetHint ? ` · ${unsetHint}` : ''}
    </p>
  );
}

function NumberSettingField({
  label,
  meaning,
  fallback,
  stateItem,
  unit,
  value,
  placeholder,
  clearHint,
  readOnly,
  onCommit,
  testId,
}: {
  label: string;
  meaning?: string;
  fallback?: string;
  stateItem?: ParameterItem;
  unit?: string;
  value: number | null;
  placeholder: string;
  clearHint?: string;
  readOnly?: boolean;
  onCommit: (next: number | null) => void;
  testId: string;
}) {
  const hintId = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(false);
  return (
    <div className="grid gap-1" data-testid={testId}>
      <label className={cn('text-[11px] font-bold uppercase tracking-wide', TEXT.subtitle)}>
        {label}
        {unit ? <span className="ml-1 font-normal normal-case">（{unit}）</span> : null}
      </label>
      <input
        type="text"
        inputMode="numeric"
        value={draft ?? (value ?? '')}
        placeholder={placeholder}
        disabled={readOnly}
        aria-label={label}
        aria-describedby={hintId}
        aria-invalid={invalid || undefined}
        onChange={(e) => {
          const raw = e.target.value;
          setDraft(raw);
          const parsed = parsePlanPositiveIntInput(raw);
          if (parsed === 'invalid') {
            setInvalid(true);
            return;
          }
          setInvalid(false);
          onCommit(parsed);
        }}
        onBlur={() => {
          setDraft(null);
          setInvalid(false);
        }}
        className={cn('h-7 px-2 text-[12px]', PIPELINE_EDITOR.inputInline)}
      />
      <p id={hintId} className={cn('text-[11px] leading-snug', TEXT.subtitle)}>
        {invalid ? '请输入 ≥1 的整数，或留空。' : null}
        {!invalid && (meaning || '正在读取设置说明')}
        {!invalid && fallback ? ` ${fallback}` : ''}
        {!invalid && value == null && clearHint ? ` ${clearHint}` : ''}
      </p>
      {!invalid ? <ProjectionStateHint item={stateItem} /> : null}
    </div>
  );
}

export function PlanSettingsEditor({
  values,
  onChange,
  projection,
  unavailable,
  isDraft,
  readOnly,
  pathFallbackHints,
}: PlanSettingsEditorProps) {
  const editable = watcherEditableSlice(values.watcherPolicy);

  const patch = (partial: Partial<PlanSettingsValues>) => onChange({ ...values, ...partial });

  const patchWatcher = (partial: {
    enabled?: boolean;
    on_unavailable?: WatcherUnavailableAction;
    paths?: Record<string, string[]>;
    required_categories?: string[];
  }) => {
    const base = values.watcherPolicy ? { ...values.watcherPolicy } : {};
    onChange({ ...values, watcherPolicy: { ...base, ...partial } });
  };

  const clearWatcher = () => onChange({ ...values, watcherPolicy: {} });

  const patrolMeta = settingMeta(projection, 'patrol_interval_seconds');
  const timeoutMeta = settingMeta(projection, 'timeout_seconds');
  const barrierMeta = settingMeta(projection, 'barrier_timeout_seconds');
  const hardMaxMeta = settingMeta(projection, 'barrier_max_wait_seconds');
  const archiveMeta = settingMeta(projection, 'auto_archive_interval_seconds');
  const enabledMeta = watcherMeta(projection, 'enabled');
  const onUnavailMeta = watcherMeta(projection, 'on_unavailable');
  const pathsMeta = watcherMeta(projection, 'paths');
  const requiredMeta = watcherMeta(projection, 'required_categories');

  const enabledExplicit = values.watcherPolicy != null && 'enabled' in values.watcherPolicy;
  const requiredExplicit =
    values.watcherPolicy != null && 'required_categories' in values.watcherPolicy;

  const knownRequired = new Set<string>(WATCHER_PATH_CATEGORIES);
  const requiredAll = useMemo(() => {
    if (!Array.isArray(values.watcherPolicy?.required_categories)) return [] as string[];
    return (values.watcherPolicy!.required_categories as unknown[]).filter(
      (c): c is string => typeof c === 'string',
    );
  }, [values.watcherPolicy]);
  const unknownRequired = requiredAll.filter((c) => !knownRequired.has(c));

  const readonlyProjection = useMemo((): ParameterProjection | undefined => {
    if (!projection) return undefined;
    return {
      ...projection,
      steps: [],
      plan_settings: projection.plan_settings.filter(
        (item) => item.path[0] == null || !EDITABLE_PLAN_SETTING_KEYS.has(String(item.path[0])),
      ),
      watcher_policy: {
        note: projection.watcher_policy.note,
        items: projection.watcher_policy.items.filter(
          (item) => item.path[0] == null || !EDITABLE_WATCHER_KEY_SET.has(String(item.path[0])),
        ),
      },
      dispatch_decisions: [],
    };
  }, [projection]);

  return (
    <section
      className="rounded-md border bg-card"
      aria-label="计划设置编辑区"
      data-testid="plan-settings-editor"
    >
      <div className="px-3 py-2 text-xs font-medium border-b border-border">
        计划设置 · {isDraft ? 'L1草稿，未保存' : 'L1 已保存计划基线'}
        {unavailable ? ' · 投影说明暂不可用' : ''}
      </div>

      <div className="p-3 grid gap-3 sm:grid-cols-2">
        <NumberSettingField
          testId="plan-setting-patrol-interval"
          label={patrolMeta?.label ?? '巡检间隔'}
          meaning={patrolMeta?.meaning}
          fallback={patrolMeta?.fallback_chain ?? undefined}
          stateItem={patrolMeta}
          unit={patrolMeta?.unit ?? '秒'}
          value={values.patrolIntervalSeconds}
          placeholder="未设则无巡检"
          readOnly={readOnly}
          onCommit={(next) => patch({ patrolIntervalSeconds: next })}
        />
        <NumberSettingField
          testId="plan-setting-timeout"
          label={timeoutMeta?.label ?? '巡检时长'}
          meaning={timeoutMeta?.meaning}
          fallback={timeoutMeta?.fallback_chain ?? undefined}
          stateItem={timeoutMeta}
          unit={timeoutMeta?.unit ?? '秒'}
          value={values.timeoutSeconds}
          placeholder="未设则不限"
          readOnly={readOnly}
          onCommit={(next) => patch({ timeoutSeconds: next })}
        />
        <NumberSettingField
          testId="plan-setting-barrier-timeout"
          label={barrierMeta?.label ?? '初始化同步等待预算'}
          meaning={barrierMeta?.meaning}
          fallback={barrierMeta?.fallback_chain ?? undefined}
          stateItem={barrierMeta}
          unit={barrierMeta?.unit ?? '秒'}
          value={values.barrierTimeoutSeconds}
          placeholder="留空继承主机"
          clearHint="清空表示继承主机策略，不会预填默认秒数。"
          readOnly={readOnly}
          onCommit={(next) => patch({ barrierTimeoutSeconds: next })}
        />
        <NumberSettingField
          testId="plan-setting-barrier-max-wait"
          label={hardMaxMeta?.label ?? '初始化同步累计等待硬顶'}
          meaning={hardMaxMeta?.meaning}
          fallback={hardMaxMeta?.fallback_chain ?? undefined}
          stateItem={hardMaxMeta}
          unit={hardMaxMeta?.unit ?? '秒'}
          value={values.barrierMaxWaitSeconds}
          placeholder="留空继承主机"
          clearHint="清空表示继承主机安全策略，不是关闭硬顶。"
          readOnly={readOnly}
          onCommit={(next) => patch({ barrierMaxWaitSeconds: next })}
        />
        <NumberSettingField
          testId="plan-setting-auto-archive"
          label={archiveMeta?.label ?? '自动归档间隔'}
          meaning={archiveMeta?.meaning}
          fallback={archiveMeta?.fallback_chain ?? undefined}
          stateItem={archiveMeta}
          unit={archiveMeta?.unit ?? '秒'}
          value={values.autoArchiveIntervalSeconds}
          placeholder="未设则不自动归档"
          readOnly={readOnly}
          onCommit={(next) => patch({ autoArchiveIntervalSeconds: next })}
        />
      </div>

      <div className="px-3 pb-3 grid gap-2 border-t border-border pt-3" data-testid="plan-watcher-editor">
        <div className="flex items-center justify-between gap-2">
          <div className={cn('text-xs font-medium', TEXT.heading)}>异常采集策略（可编辑四键）</div>
          <button
            type="button"
            disabled={readOnly}
            onClick={clearWatcher}
            className={cn('text-[11px] underline', TEXT.subtitle)}
            data-testid="plan-watcher-clear"
          >
            清空策略（发送空对象）
          </button>
        </div>
        <p className={cn('text-[11px] leading-snug', TEXT.subtitle)}>
          不提供自由 JSON。清空用空对象；更新 null 原本不生效。未编辑的未知子键会保留。
        </p>

        <div className="flex items-center justify-between py-1">
          <div>
            <div className={cn('text-[11px] font-bold uppercase tracking-wide', TEXT.subtitle)}>
              {enabledMeta?.label ?? '异常采集开关'}
            </div>
            <p className={cn('text-[11px]', TEXT.subtitle)}>{enabledMeta?.meaning}</p>
            <ProjectionStateHint
              item={enabledMeta}
              unsetHint="控件未按下不等于确定关闭；写入只反映计划显式事实"
            />
          </div>
          <button
            type="button"
            disabled={readOnly}
            aria-pressed={enabledExplicit ? editable.enabled === true : undefined}
            aria-label={
              !enabledExplicit
                ? '异常采集未在计划显式设置'
                : editable.enabled === true
                  ? '关闭异常采集'
                  : '开启异常采集'
            }
            data-testid="plan-watcher-enabled"
            data-explicit={enabledExplicit ? '1' : '0'}
            onClick={() => patchWatcher({ enabled: !(editable.enabled === true) })}
            className={cn(
              'relative w-8 h-[18px] rounded-full transition',
              !enabledExplicit
                ? 'bg-muted-foreground/25'
                : editable.enabled === true
                  ? 'bg-success'
                  : 'bg-muted-foreground/40',
              readOnly ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
            )}
          >
            <span
              className={cn(
                'absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full bg-background shadow transition-transform',
                enabledExplicit && editable.enabled === true ? 'translate-x-[14px]' : 'translate-x-0',
              )}
            />
          </button>
        </div>

        <label className="grid gap-1">
          <span className={cn('text-[11px] font-bold uppercase tracking-wide', TEXT.subtitle)}>
            {onUnavailMeta?.label ?? '采集不可用时'}
          </span>
          <select
            value={editable.on_unavailable ?? ''}
            disabled={readOnly}
            aria-label={onUnavailMeta?.label ?? '采集不可用时'}
            data-testid="plan-watcher-on-unavailable"
            onChange={(e) => {
              const v = e.target.value;
              if (!v) {
                const next = values.watcherPolicy ? { ...values.watcherPolicy } : {};
                delete next.on_unavailable;
                onChange({ ...values, watcherPolicy: next });
                return;
              }
              if ((WATCHER_ON_UNAVAILABLE_VALUES as readonly string[]).includes(v)) {
                patchWatcher({ on_unavailable: v as WatcherUnavailableAction });
              }
            }}
            className={cn('h-7 px-2 text-[12px]', PIPELINE_EDITOR.inputInline)}
          >
            <option value="">未显式设置</option>
            {WATCHER_ON_UNAVAILABLE_VALUES.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
          <span className={cn('text-[11px]', TEXT.subtitle)}>{onUnavailMeta?.meaning}</span>
          <ProjectionStateHint item={onUnavailMeta} />
        </label>

        <div className="grid gap-2" data-testid="plan-watcher-paths">
          <div className={cn('text-[11px] font-bold uppercase tracking-wide', TEXT.subtitle)}>
            {pathsMeta?.label ?? '采集路径'}
          </div>
          <p className={cn('text-[11px]', TEXT.subtitle)}>{pathsMeta?.meaning}</p>
          <ProjectionStateHint item={pathsMeta} />
          {WATCHER_PATH_CATEGORIES.map((cat) => {
            const paths = editable.paths?.[cat] ?? [];
            const text = paths.join('\n');
            return (
              <label key={cat} className="grid gap-1">
                <span className={cn('text-[11px] font-mono', TEXT.subtitle)}>{cat}</span>
                <textarea
                  value={text}
                  disabled={readOnly}
                  aria-label={`${cat} 路径`}
                  rows={2}
                  placeholder="每行一条路径；留空表示该分类回落代码默认"
                  onChange={(e) => {
                    const lines = e.target.value
                      .split('\n')
                      .map((l) => l.trim())
                      .filter(Boolean);
                    // 从完整 policy.paths 起步，保留未知分类（如 CUSTOM）。
                    const basePaths =
                      values.watcherPolicy?.paths &&
                      typeof values.watcherPolicy.paths === 'object' &&
                      !Array.isArray(values.watcherPolicy.paths)
                        ? { ...(values.watcherPolicy.paths as Record<string, string[]>) }
                        : { ...(editable.paths ?? {}) };
                    if (lines.length === 0) delete basePaths[cat];
                    else basePaths[cat] = lines;
                    const nextPolicy = values.watcherPolicy ? { ...values.watcherPolicy } : {};
                    if (Object.keys(basePaths).length === 0) delete nextPolicy.paths;
                    else nextPolicy.paths = basePaths;
                    onChange({ ...values, watcherPolicy: nextPolicy });
                  }}
                  className={cn('px-2 py-1 text-[12px] font-mono', PIPELINE_EDITOR.inputInline)}
                />
                {pathFallbackHints?.[cat] || (editable.paths && !(cat in (editable.paths ?? {})) && values.watcherPolicy != null && Object.keys(values.watcherPolicy).length > 0 && 'paths' in values.watcherPolicy) ? (
                  <span className={cn('text-[11px]', TEXT.subtitle)} data-testid={`path-fallback-${cat}`}>
                    {cat} 未给出路径时按分类回落代码默认，不会删掉其它分类。
                  </span>
                ) : null}
              </label>
            );
          })}
        </div>

        <div className="grid gap-1" data-testid="plan-watcher-required-categories">
          <div className={cn('text-[11px] font-bold uppercase tracking-wide', TEXT.subtitle)}>
            {requiredMeta?.label ?? '必需日志分类'}
          </div>
          <p className={cn('text-[11px]', TEXT.subtitle)}>{requiredMeta?.meaning}</p>
          <ProjectionStateHint
            item={requiredMeta}
            unsetHint="全未勾选不等于确定无必需分类；未显式设置时沿投影回落"
          />
          {!requiredExplicit ? (
            <p className={cn('text-[11px]', TEXT.subtitle)} data-testid="plan-watcher-required-unset">
              计划未显式设置必需分类（勾选框不代表已确定全不选）。
            </p>
          ) : requiredAll.length === 0 ? (
            <p className={cn('text-[11px]', TEXT.subtitle)} data-testid="plan-watcher-required-empty">
              已显式设置为空数组 []（与字段省略 / 整体 {'{}'} 回落不同）。
            </p>
          ) : null}
          <div className="flex flex-wrap gap-3">
            {WATCHER_PATH_CATEGORIES.map((cat) => {
              const checked = requiredAll.includes(cat);
              return (
                <label key={cat} className="inline-flex items-center gap-1.5 text-[12px]">
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={readOnly}
                    aria-label={`必需分类 ${cat}`}
                    onChange={() => {
                      const knownSelected = new Set(
                        requiredAll.filter((c) => knownRequired.has(c)),
                      );
                      if (checked) knownSelected.delete(cat);
                      else knownSelected.add(cat);
                      const knownOrdered = WATCHER_PATH_CATEGORIES.filter((c) =>
                        knownSelected.has(c),
                      );
                      // 保留未知必需分类；无已知且无未知时发 []（勿 delete）。
                      const list = [...knownOrdered, ...unknownRequired];
                      const nextPolicy = values.watcherPolicy ? { ...values.watcherPolicy } : {};
                      nextPolicy.required_categories = list;
                      onChange({ ...values, watcherPolicy: nextPolicy });
                    }}
                  />
                  <span className="font-mono">{cat}</span>
                </label>
              );
            })}
          </div>
          {unknownRequired.length > 0 ? (
            <div
              data-testid="plan-watcher-required-unknown"
              className={cn('text-[11px] font-mono', TEXT.subtitle)}
            >
              未登记必需分类（只读保留）：{unknownRequired.join(', ')}
            </div>
          ) : null}
        </div>
      </div>

      {/* C3：只读登记项与未知子键只走安全投影；失败时不回退原始 JSON/敏感值。 */}
      <div className="border-t border-border" data-testid="plan-settings-readonly-projection">
        {unavailable ? (
          <div
            className={cn('px-3 py-3 text-xs', TEXT.subtitle)}
            data-testid="plan-settings-projection-unavailable"
          >
            参数清单不可用。没有安全投影时不展示原始参数。
          </div>
        ) : readonlyProjection ? (
          <div className="[&_[data-testid=parameter-steps]]:hidden [&_[data-testid=parameter-dispatch-decisions]]:hidden">
            <ParameterProjectionList projection={readonlyProjection} />
          </div>
        ) : (
          <div className={cn('px-3 py-3 text-xs', TEXT.subtitle)}>
            正在读取参数清单。没有安全投影时不展示原始参数。
          </div>
        )}
      </div>
    </section>
  );
}

export default PlanSettingsEditor;
