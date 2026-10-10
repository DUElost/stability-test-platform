import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type {
  ParameterItem,
  ParameterProjection,
  ParameterSource,
  ParameterState,
} from '@/utils/api/types';

const STATE_LABEL: Record<ParameterState, string> = {
  explicit: '已确定',
  unset_definite: '未设置',
  env_fallback: '沿回落链',
  pending_dispatch: '待派发确定',
  actual: '已下发',
};

const SOURCE_LABEL: Record<ParameterSource, string> = {
  schema_default: '模式默认',
  script_default: '脚本默认',
  step_override: '步骤覆盖',
  dispatch_injection: '派发填入',
};

export interface ParameterProjectionListProps {
  projection: ParameterProjection | null | undefined;
  /** 投影失败。缺响应与出错都不展示原始参数。 */
  unavailable?: boolean;
}

function shownValue(item: ParameterItem): string {
  if (item.sensitive) return item.is_set ? '已设置' : '未设置';
  if (item.value === null || item.value === undefined) {
    return STATE_LABEL[item.state] ?? item.state;
  }
  if (typeof item.value === 'string' || typeof item.value === 'number' || typeof item.value === 'boolean') {
    return String(item.value);
  }
  return JSON.stringify(item.value);
}

function ItemRow({ item, layer }: { item: ParameterItem; layer: string }) {
  const source = item.source ? SOURCE_LABEL[item.source] : '来源不可追溯';
  return (
    <div className="border-t px-3 py-2 text-xs" data-testid="parameter-item">
      <div className="font-medium">{item.label}</div>
      <p className={cn('mt-0.5', TEXT.subtitle)}>{item.meaning}</p>
      <dl className="mt-1 grid gap-1 sm:grid-cols-2">
        <div>
          <dt className={TEXT.subtitle}>值 / 状态</dt>
          <dd data-testid="parameter-value">{shownValue(item)}</dd>
          <dd className={TEXT.subtitle}>{STATE_LABEL[item.state] ?? item.state}</dd>
        </div>
        <div>
          <dt className={TEXT.subtitle}>来源 / 层</dt>
          <dd>{source}</dd>
          <dd>{layer}</dd>
        </div>
      </dl>
      {item.fallback_chain ? (
        <p className={cn('mt-1', TEXT.subtitle)} data-testid="parameter-fallback">
          回落链：{item.fallback_chain}
        </p>
      ) : null}
      {item.cautions ? (
        <p className={cn('mt-1', TEXT.subtitle)}>{item.cautions}</p>
      ) : null}
    </div>
  );
}

function ItemGroup({
  title,
  items,
  layer,
}: {
  title: string;
  items: ParameterItem[];
  layer: string;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className={cn('px-3 py-1.5 text-[11px] font-medium', TEXT.subtitle)}>{title}</div>
      {items.map((item) => (
        <ItemRow key={item.path.join('.')} item={item} layer={layer} />
      ))}
    </div>
  );
}

export function ParameterProjectionList({
  projection,
  unavailable = false,
}: ParameterProjectionListProps) {
  if (unavailable || !projection) {
    return (
      <div
        className={cn('px-3 py-4 text-xs', TEXT.subtitle)}
        data-testid="parameter-projection-unavailable"
      >
        {unavailable
          ? '参数清单不可用。没有安全投影时不展示原始参数。'
          : '正在读取参数清单。没有安全投影时不展示原始参数。'}
      </div>
    );
  }

  const layer = projection.layer;

  return (
    <div data-testid="parameter-projection-list">
      <div className="flex items-center justify-between gap-2 px-3 py-2 text-xs">
        <span className="font-medium">参数清单</span>
        <span data-testid="parameter-layer">{layer}</span>
      </div>
      <div data-testid="parameter-plan-settings">
        <ItemGroup title="计划设置" items={projection.plan_settings} layer={layer} />
      </div>
      <div data-testid="parameter-steps">
        {projection.steps.length === 0 ? (
          <p className={cn('px-3 py-2 text-xs', TEXT.subtitle)}>此投影没有步骤</p>
        ) : projection.steps.map((step, index) => {
          const title = [
            step.script_name || step.step_key || '未命名步骤',
            step.script_version,
          ].filter(Boolean).join(' · ');
          return (
            <section key={`${step.step_key ?? title}-${index}`} className="border-t">
              <div className="flex items-center justify-between gap-2 px-3 py-2 text-xs">
                <span>{title}</span>
                <span>{step.executes ? '执行' : '不执行'}</span>
              </div>
              {step.metadata_missing ? (
                <p className={cn('px-3 pb-2 text-[11px]', TEXT.subtitle)}>
                  {step.missing_reason || '脚本元数据缺失'}
                </p>
              ) : null}
              <ItemGroup title="参数" items={step.params} layer={layer} />
              <ItemGroup title="步骤设置" items={step.settings} layer={layer} />
            </section>
          );
        })}
      </div>
      <section className="border-t" data-testid="parameter-watcher">
        <div className="px-3 py-2 text-xs font-medium">异常采集策略</div>
        <p className={cn('px-3 pb-2 text-[11px]', TEXT.subtitle)}>{projection.watcher_policy.note}</p>
        <ItemGroup title="策略项" items={projection.watcher_policy.items} layer={layer} />
      </section>
      <section className="border-t" data-testid="parameter-dispatch-decisions">
        <div className="px-3 py-2 text-xs font-medium">待派发因素</div>
        {projection.dispatch_decisions.length === 0 ? (
          <p className={cn('px-3 pb-2 text-[11px]', TEXT.subtitle)}>没有待派发项</p>
        ) : projection.dispatch_decisions.map((decision, index) => (
          <div key={`${decision.kind}-${index}`} className="px-3 pb-2 text-xs">
            <div>{decision.kind}</div>
            <div className={TEXT.subtitle}>{STATE_LABEL[decision.state] ?? decision.state}</div>
            {decision.factors?.length ? (
              <div className={TEXT.subtitle}>{decision.factors.join('、')}</div>
            ) : null}
          </div>
        ))}
      </section>
    </div>
  );
}
