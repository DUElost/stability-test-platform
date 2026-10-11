import { useMemo, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, AlertTriangle } from 'lucide-react';
import type { PipelinePhase, PipelineStep, ScriptEntry, ParameterProjection, ParameterItem } from '@/utils/api/types';
import { ALERT_BANNER, PIPELINE_EDITOR, STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { mergeDraftParameters } from '@/components/parameters/draftParameterMerge';
import { ParameterProjectionList } from '@/components/parameters/ParameterProjectionList';
import { formatStepTimeout, stepTimeoutHint } from './stepTiming';

/* ── param_schema field descriptor ───────────────────────────────────────── */

interface ParamFieldSchema {
  type: 'string' | 'integer' | 'boolean' | 'number';
  required?: boolean;
  label?: string;
  description?: string;
  enum?: string[];
  default?: unknown;
  minimum?: number;
}

interface PlanStepInspectorProps {
  projection?: ParameterProjection;
  editableProjection?: ParameterProjection;
  projectionUnavailable?: boolean;
  projectionIsDraft?: boolean;
  step: PipelineStep | null;
  phase: PipelinePhase | null;
  index: number | null;
  scripts: ScriptEntry[];
  onUpdateStep: (next: PipelineStep) => void;
  readOnly?: boolean;
}

export default function PlanStepInspector({
  projection,
  editableProjection,
  projectionUnavailable = false,
  projectionIsDraft = false,
  step,
  phase,
  index,
  scripts,
  onUpdateStep,
  readOnly,
}: PlanStepInspectorProps) {
  const scriptName = step?.action?.startsWith('script:') ? step.action.slice(7) : (step?.action ?? '');
  const matchedScript = scripts.find(s => s.name === scriptName && s.version === step?.version) ?? null;

  const versionsForName = useMemo(
    () => scripts.filter(s => s.name === scriptName).sort((a, b) => b.version.localeCompare(a.version)),
    [scripts, scriptName],
  );

  return (
    <aside className={cn('flex flex-col min-h-0 overflow-hidden bg-card border-border border-l')}>
      <header className={cn('px-4 py-3', PIPELINE_EDITOR.panelHeader)}>
        <div className={cn('text-sm font-bold', TEXT.heading)}>步骤属性</div>
        <div className={cn('text-[11px] mt-0.5 font-mono truncate', TEXT.subtitle)}>
          {step
            ? `${scriptName || '—'} / v${(step.version || '').replace(/^v/, '') || '—'}`
            : '未选择步骤'}
        </div>
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto p-2.5 flex flex-col gap-2 [&>*]:shrink-0 custom-scrollbar">
        {!step ? (
          <StepEmptyState />
        ) : (
          <>
            <ScriptInfoCard
              step={step}
              scriptName={scriptName}
              matchedScript={matchedScript}
              scripts={scripts}
              versionsForName={versionsForName}
              onPickScript={(name, version) => {
                if (readOnly) return;
                const sameScript = name === scriptName;
                let params: Record<string, unknown> = {};
                if (sameScript && step.params && Object.keys(step.params).length > 0) {
                  const newScript = scripts.find(s => s.name === name && s.version === version);
                  const schemaKeys = Object.keys(newScript?.param_schema ?? {});
                  const defaultKeys = Object.keys(newScript?.default_params ?? {});
                  // #508 自由键模式：无 schema 且无 default_params 时，步骤级
                  // params 是用户自定义自由键（如 apk_path），切换版本应保留，
                  // 不能按 validKeys（空集）把已有自定义参数清空。
                  const hasKnownKeys = schemaKeys.length > 0 || defaultKeys.length > 0;
                  if (hasKnownKeys) {
                    const validKeys = new Set([...schemaKeys, ...defaultKeys]);
                    params = Object.fromEntries(
                      Object.entries(step.params).filter(([k]) => validKeys.has(k)),
                    );
                  } else {
                    params = { ...step.params };
                  }
                }
                onUpdateStep({
                  ...step,
                  action: `script:${name}`,
                  version,
                  params,
                });
              }}
              readOnly={readOnly}
            />

            <ParamFormCard
              key={`${step.step_id}:${step.action}:${step.version}`}
              step={step}
              projection={projection}
              editableProjection={editableProjection}
              matchedScript={matchedScript}
              projectionUnavailable={projectionUnavailable}
              projectionIsDraft={projectionIsDraft}
              onUpdateStep={onUpdateStep}
              readOnly={readOnly}
            />

            <RuntimeConfigCard step={step} onUpdateStep={onUpdateStep} readOnly={readOnly} />

            {scriptName && (
              <Link
                // 路由是 `/script-management`（`/scripts` 不存在，曾落 404）；带 `version` 与
                // 快照抽屉的深链同形（#3350），脚本库页据此展开该版本。
                to={`/script-management?name=${encodeURIComponent(scriptName)}${
                  step.version ? `&version=${encodeURIComponent(step.version)}` : ''
                }`}
                className={cn(
                  'inline-flex items-center justify-center gap-1.5 px-2.5 py-2 text-[11px] font-bold rounded-md transition',
                  PIPELINE_EDITOR.linkBtn,
                )}
              >
                在脚本库中编辑参数
                <ArrowUpRight className="w-3.5 h-3.5" />
              </Link>
            )}
          </>
        )}
      </div>

      {/* 空态引导只在 StepEmptyState 卡里说一次；无步骤时不再渲染脚注 */}
      {step && (
        <footer className={cn('px-3 py-2 text-[11px] leading-relaxed', PIPELINE_EDITOR.panelHeader, TEXT.subtitle)}>
          {`位于 ${phase ?? '—'} #${(index ?? 0) + 1}。脚本的参数和默认值在脚本库页面维护。`}
        </footer>
      )}
    </aside>
  );
}

function StepEmptyState() {
  return (
    <div className={cn('m-1 px-3 py-6 rounded-md', PIPELINE_EDITOR.emptyState)}>
      <div className={cn('font-medium mb-1', TEXT.body)}>未选择步骤</div>
      <div className={cn('text-[11px]', TEXT.subtitle)}>在中央画布点击任意步骤以查看其属性。</div>
    </div>
  );
}

interface ScriptInfoCardProps {
  step: PipelineStep;
  scriptName: string;
  matchedScript: ScriptEntry | null;
  scripts: ScriptEntry[];
  versionsForName: ScriptEntry[];
  onPickScript: (name: string, version: string) => void;
  readOnly?: boolean;
}

function ScriptInfoCard({
  step,
  scriptName,
  matchedScript,
  scripts,
  versionsForName,
  onPickScript,
  readOnly,
}: ScriptInfoCardProps) {
  const allActive = scripts.filter(s => s.is_active);
  const knownNames = Array.from(new Set(allActive.map(s => s.name))).sort();
  // matchedScript 不过滤 is_active（参数表单仍需要停用版本的 schema 才能显示已填的值），
  // 所以「异常」判定必须自己补上 is_active——只写 !matchedScript 的话，停用版本会被
  // 当成正常匹配，下面 deactivatedMatch 是它的严格子集，那条提示永远不会出现。
  const isUnknown = !!scriptName && (!matchedScript || !matchedScript.is_active);

  // Distinguish: version deactivated vs. completely unknown
  const deactivatedMatch = isUnknown
    ? scripts.find(s => s.name === scriptName && s.version === step.version && !s.is_active)
    : null;
  const warningMessage = deactivatedMatch
    ? <>当前脚本 <code className="font-mono">{scriptName}@{step.version}</code> 版本已停用，请选择已激活版本后再执行调度。</>
    : <>当前脚本 <code className="font-mono">{scriptName}@{step.version || '?'}</code> 未在已激活脚本中找到，请重新选择。</>;

  const selectCls = cn('max-w-[60%] h-7 px-1.5', PIPELINE_EDITOR.inputInline, 'text-[12px]');

  return (
    <Card>
      <CardHead>
        <span>脚本信息</span>
        <span className={cn('inline-flex items-center px-1.5 py-px rounded-full text-[11px] font-bold', STATUS_CHIP.primary)}>
          来自脚本库
        </span>
      </CardHead>
      <CardBody>
        {isUnknown && (
          <div className={cn('flex items-start gap-1.5 px-2 py-1.5 rounded-md text-[11px]', ALERT_BANNER.warning)}>
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>
              {warningMessage}
            </span>
          </div>
        )}

        <Row label="脚本名">
          <select
            disabled={readOnly}
            value={scriptName}
            onChange={e => {
              const newName = e.target.value;
              const firstVersion = scripts.find(s => s.name === newName && s.is_active);
              if (firstVersion) onPickScript(newName, firstVersion.version);
              else onPickScript(newName, '');
            }}
            className={selectCls}
          >
            <option value="">— 选择脚本 —</option>
            {knownNames.map(n => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
            {scriptName && !knownNames.includes(scriptName) && (
              <option value={scriptName}>{scriptName} (未匹配)</option>
            )}
          </select>
        </Row>

        <Row label="版本">
          <select
            disabled={readOnly || !scriptName}
            value={step.version || ''}
            onChange={e => onPickScript(scriptName, e.target.value)}
            className={selectCls}
          >
            <option value="">—</option>
            {versionsForName.map(s => (
              <option key={s.version} value={s.version}>
                {s.version}
                {!s.is_active ? ' (已停用)' : ''}
              </option>
            ))}
            {step.version && !versionsForName.find(s => s.version === step.version) && (
              <option value={step.version}>{step.version} (未匹配)</option>
            )}
          </select>
        </Row>

        <Row label="类型 / 分类">
          <span className={cn('font-semibold', TEXT.body)}>
            {matchedScript ? `${matchedScript.script_type} / ${matchedScript.category ?? '—'}` : '—'}
          </span>
        </Row>
      </CardBody>
    </Card>
  );
}

/* ── ParamFormCard: structured parameter form driven by param_schema ─────── */

interface ParamFormCardProps {
  matchedScript: ScriptEntry | null;
  step: PipelineStep;
  projection?: ParameterProjection;
  editableProjection?: ParameterProjection;
  projectionUnavailable: boolean;
  projectionIsDraft: boolean;
  onUpdateStep: (next: PipelineStep) => void;
  readOnly?: boolean;
}

function schemaAtPath(schema: Record<string, unknown>, path: ParameterItem['path']): ParamFieldSchema | undefined {
  let field: unknown = schema[path[0]];
  for (const segment of path.slice(1)) {
    if (!field || typeof field !== 'object') return undefined;
    const object = field as Record<string, unknown>;
    field = typeof segment === 'number'
      ? object.items
      : (object.properties as Record<string, unknown> | undefined)?.[segment];
  }
  return field && typeof field === 'object' ? field as ParamFieldSchema : undefined;
}

/** Clone only the edited path. Untouched siblings retain their original save facts. */
function updateParamPath(original: Record<string, unknown>, path: ParameterItem['path'], value: unknown): Record<string, unknown> {
  const next = { ...original };
  let target: Record<string | number, unknown> = next;
  let source: unknown = original;
  for (let i = 0; i < path.length - 1; i++) {
    const key = path[i];
    const old = source && typeof source === 'object' ? (source as Record<string | number, unknown>)[key] : undefined;
    const child = Array.isArray(old) ? [...old] : old && typeof old === 'object' ? { ...old } : {};
    Object.defineProperty(target, key, { value: child, enumerable: true, writable: true, configurable: true });
    target = child as Record<string | number, unknown>;
    source = old;
  }
  Object.defineProperty(target, path[path.length - 1], { value, enumerable: true, writable: true, configurable: true });
  return next;
}

function ParamFormCard({ step, projection, editableProjection, matchedScript, projectionUnavailable, projectionIsDraft, onUpdateStep, readOnly }: ParamFormCardProps) {
  const projectedStep = projection?.steps.find(s => s.step_key === step.step_id
    && s.script_name === step.action.replace(/^script:/, '') && s.script_version === step.version);
  const controlsProjection = projection ?? editableProjection;
  const controlStep = controlsProjection?.steps.find(s => s.step_key === step.step_id
    && s.script_name === step.action.replace(/^script:/, '') && s.script_version === step.version);
  const debug = controlsProjection?.safe_debug.steps.find(s => s.step_key === step.step_id);
  const schema = (debug?.param_schema ?? {}) as Record<string, unknown>;
  const single = projectedStep && projection ? {
    ...projection, steps: [projectedStep], plan_settings: [],
    watcher_policy: { note: '', items: [] }, dispatch_decisions: [],
  } : undefined;
  const params = step.params ?? {};
  const editableItems = controlStep?.params ?? [];
  const setParam = (item: ParameterItem, value: unknown) => {
    if (readOnly || item.sensitive || !controlStep) return;
    // Only an explicit edit may copy an inherited object into the internal
    // write facts. Safe projection nulls never enter the write payload.
    const internal = item.path.length > 1 && !Object.prototype.hasOwnProperty.call(params, item.path[0])
      ? { ...params, [item.path[0]]: mergeDraftParameters(matchedScript?.param_schema,
        matchedScript?.default_params, params)[item.path[0]] }
      : params;
    let next = updateParamPath(internal, item.path, value);
    const defaults = (debug?.default_params ?? {}) as Record<string, unknown>;
    if (item.path.length === 1 && (value === undefined || value === defaults[item.path[0]])) {
      next = { ...params };
      delete next[item.path[0]];
    }
    onUpdateStep({ ...step, params: next });
  };
  const canEdit = (item: ParameterItem) => !item.sensitive && !readOnly;

  return (
    <Card>
      <CardHead><span>脚本参数</span><span>{projectionIsDraft ? 'L1草稿，未保存' : 'L1 已保存计划基线'}</span></CardHead>
      <CardBody>
        {controlStep && !readOnly && Object.keys(schema).filter(key =>
          !editableItems.some(item => item.path[0] === key)).map(key => (
          <button key={key} type="button" className={cn('px-2 py-1 text-xs rounded', PIPELINE_EDITOR.linkBtn)}
            onClick={() => onUpdateStep({ ...step, params: updateParamPath(params, [key], '') })}>
            添加未设置参数 {key}
          </button>
        ))}
        {controlStep && Object.keys(schema).length === 0 && <p className={TEXT.subtitle}>自由键值：可手动添加自定义参数。</p>}
        <div className="[&_[data-testid=parameter-watcher]]:hidden [&_[data-testid=parameter-dispatch-decisions]]:hidden">
          <ParameterProjectionList projection={single} unavailable={projectionUnavailable || (!!projection && !projectedStep)} />
        </div>
        {!projection && controlStep && <p className={TEXT.subtitle}>正在更新参数清单，可继续编辑。</p>}
        {controlStep && editableItems.map(item => {
          if (item.sensitive) return null;
          const field = schemaAtPath(schema, item.path);
          const key = JSON.stringify(item.path);
          const name = item.path.join('.');
          if (field) return (
            <ParamFieldRow key={key} fieldKey={name}
              field={{ ...field, label: item.label, description: item.meaning }}
              value={item.value} onChange={value => setParam(item, value)} disabled={!canEdit(item)} />
          );
          return <FreeformParamRow key={key} paramKey={item.label} value={item.value}
            onChange={value => setParam(item, value)}
            onRemove={() => {
              if (!canEdit(item) || item.path.length !== 1) return;
              const next = { ...params };
              delete next[item.path[0]];
              onUpdateStep({ ...step, params: next });
            }} disabled={!canEdit(item) || item.path.length !== 1} />;
        })}
        {controlStep && !readOnly && (
          <FreeformParamAdder existingKeys={new Set(editableItems.map(item => String(item.path[0])))}
            onAdd={key => onUpdateStep({ ...step, params: updateParamPath(params, [key], '') })} />
        )}
      </CardBody>
    </Card>
  );
}

interface FreeformParamRowProps {
  paramKey: string;
  value: unknown;
  onChange: (v: unknown) => void;
  onRemove: () => void;
  disabled: boolean;
}

function FreeformParamRow({ paramKey, value: projectedValue, onChange: commit, onRemove, disabled }: FreeformParamRowProps) {
  const [value, setValue] = useState(projectedValue);
  const [previousValue, setPreviousValue] = useState(projectedValue);
  if (previousValue !== projectedValue) {
    setPreviousValue(projectedValue);
    setValue(projectedValue);
  }
  const onChange = (next: unknown) => { setValue(next); commit(next); };
  const inputCls = cn('max-w-[60%] h-7 px-2 text-[12px] font-mono', PIPELINE_EDITOR.inputInline);
  // 无 schema 无法声明类型：按当前值推断输入控件，新增键默认字符串。
  if (typeof value === 'boolean') {
    return (
      <Row label={paramKey}>
        <button
          type="button"
          disabled={disabled}
          onClick={() => onChange(!value)}
          className={cn(
            'relative w-8 h-[18px] rounded-full transition',
            value ? 'bg-success' : 'bg-muted-foreground/40',
            disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
          )}
          aria-pressed={value}
          aria-label={`${paramKey}: ${value ? '是' : '否'}`}
        >
          <span className={cn(
            'absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full bg-background shadow transition-transform',
            value ? 'translate-x-[14px]' : 'translate-x-0',
          )} />
        </button>
      </Row>
    );
  }
  if (typeof value === 'number') {
    return (
      <Row label={paramKey}>
        <input
          type="number"
          disabled={disabled}
          value={value != null ? Number(value) : ''}
          onChange={e => {
            const raw = e.target.value;
            if (raw === '') { onChange(undefined); return; }
            const num = parseFloat(raw);
            if (!isNaN(num)) onChange(num);
          }}
          className={inputCls}
        />
      </Row>
    );
  }
  return (
    <Row label={paramKey} align="start">
      <div className="flex items-center gap-1">
        <input
          type="text"
          disabled={disabled}
          value={String(value ?? '')}
          onChange={e => onChange(e.target.value || undefined)}
          className={inputCls}
        />
        {!disabled && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`移除参数 ${paramKey}`}
            className={cn('text-[11px] px-1 py-0.5 rounded', STATUS_CHIP.muted)}
            title="移除该参数"
          >
            ✕
          </button>
        )}
      </div>
    </Row>
  );
}

interface FreeformParamAdderProps {
  existingKeys: Set<string>;
  onAdd: (key: string) => void;
}

function FreeformParamAdder({ existingKeys, onAdd }: FreeformParamAdderProps) {
  const [draftKey, setDraftKey] = useState('');
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const key = draftKey.trim();
    if (!key) return;
    if (existingKeys.has(key)) {
      setError('参数已存在');
      return;
    }
    onAdd(key);
    setDraftKey('');
    setError(null);
  };

  return (
    <div className="mt-2 pt-2 border-t border-border">
      <div className="flex items-center gap-1.5">
        <input
          value={draftKey}
          onChange={e => { setDraftKey(e.target.value); setError(null); }}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submit(); } }}
          placeholder="新参数名，如 apk_path"
          className={cn('h-7 px-2 text-[11px] font-mono flex-1', PIPELINE_EDITOR.inputInline)}
          aria-label="新参数名"
        />
        <button
          type="button"
          onClick={submit}
          className={cn('h-7 px-2 text-[11px] font-medium rounded-md', PIPELINE_EDITOR.linkBtn)}
        >
          添加
        </button>
      </div>
      {error && <p className={cn('mt-1 text-[11px] text-destructive')}>{error}</p>}
    </div>
  );
}

/* ── Single parameter field row ──────────────────────────────────────────── */

interface ParamFieldRowProps {
  fieldKey: string;
  field: ParamFieldSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled: boolean;
}

function ParamFieldRow({ fieldKey, field, value: projectedValue, onChange: commit, disabled }: ParamFieldRowProps) {
  const [value, setValue] = useState(projectedValue);
  const [previousValue, setPreviousValue] = useState(projectedValue);
  if (previousValue !== projectedValue) {
    setPreviousValue(projectedValue);
    setValue(projectedValue);
  }
  const onChange = (next: unknown) => { setValue(next); commit(next); };
  const label = field.label || fieldKey;
  const isRequired = !!field.required;
  const displayLabel = isRequired ? `${label} *` : label;
  const inputCls = cn('max-w-[60%] h-7 px-2 text-[12px]', PIPELINE_EDITOR.inputInline);
  // 说明常显在字段下方：此前只当字符串输入框的 placeholder，字段一有值（通常来自
  // default_params）就看不见，数字/枚举/布尔字段则从不显示。
  const descriptionId = useId();
  const description = typeof field.description === 'string' ? field.description.trim() : '';
  const describedBy = description ? descriptionId : undefined;
  const shell = (control: React.ReactNode) => (
    <ParamFieldShell label={displayLabel} description={description} descriptionId={descriptionId}>
      {control}
    </ParamFieldShell>
  );

  // string with enum → <select>
  if (field.type === 'string' && field.enum && field.enum.length > 0) {
    return shell(
      <select
        disabled={disabled}
        value={String(value ?? '')}
        onChange={e => onChange(e.target.value || undefined)}
        className={inputCls}
        aria-label={label}
      aria-describedby={describedBy}
      >
        <option value="">—</option>
        {field.enum.map(opt => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>,
    );
  }

  // boolean → toggle switch
  if (field.type === 'boolean') {
    const checked = value === true || value === 'true';
    return shell(
      <button
        type="button"
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative w-8 h-[18px] rounded-full transition',
          checked ? 'bg-success' : 'bg-muted-foreground/40',
          disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
        )}
        aria-pressed={checked}
        aria-label={`${label}: ${checked ? '是' : '否'}`}
      aria-describedby={describedBy}
      >
        <span
          className={cn(
            'absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full bg-background shadow transition-transform',
            checked ? 'translate-x-[14px]' : 'translate-x-0',
          )}
        />
      </button>,
    );
  }

  // integer / number → numeric input
  if (field.type === 'integer' || field.type === 'number') {
    return shell(
      <input
        type="number"
        min={field.minimum}
        value={value != null ? Number(value) : ''}
        placeholder={String(field.default ?? '')}
        disabled={disabled}
        onChange={e => {
          const raw = e.target.value;
          if (raw === '') { onChange(undefined); return; }
          const num = field.type === 'integer'
            ? parseInt(raw, 10)
            : parseFloat(raw);
          if (!isNaN(num)) onChange(num);
        }}
        className={inputCls}
        aria-label={label}
      aria-describedby={describedBy}
      />,
    );
  }

  // string (no enum) → text input；placeholder 与数字字段同口径取 schema 默认（说明已常显）
  return shell(
    <input
      type="text"
      value={String(value ?? '')}
      placeholder={String(field.default ?? '')}
      disabled={disabled}
      onChange={e => onChange(e.target.value || undefined)}
      className={cn(inputCls, 'min-w-[50%]')}
      aria-label={label}
      aria-describedby={describedBy}
    />,
  );
}

/** 参数行 + 其下方常显的说明（无说明时只渲染参数行，不留空段落）。 */
function ParamFieldShell({
  label,
  description,
  descriptionId,
  children,
}: {
  label: string;
  description: string;
  descriptionId: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-0.5">
      <Row label={label}>{children}</Row>
      {description && (
        <p
          id={descriptionId}
          className={cn('text-[11px] leading-snug whitespace-pre-wrap break-words', TEXT.subtitle)}
        >
          {description}
        </p>
      )}
    </div>
  );
}

/**
 * 超时输入框：编辑期允许「空」，但**不把空落库**。
 *
 * 从编辑器存得下去的取值只有 `>= 1` 的整数——`0` 要求同时配 `stall_seconds >= 1`
 * （本面板不暴露该字段），`null` 会被 `_assemble_lifecycle_for_validation` 原样
 * 传给 schema 的 `type: integer` 拒掉。此前清空输入框会静默写入 `0`，用户直到
 * 保存才收到一条原始 jsonschema 报错。
 *
 * 用 draft 承接编辑中的原始文本，是为了让「清空再重输」这套动作正常：直接把空
 * 折算成某个默认值会让输入框立刻跳成 `30`，接着输入就变成 `306` / `3060`。
 * 空值只是不提交，失焦后回落到最后一次提交的值。
 * 非数字输入同样不提交（`|| 1` 会把一次手滑改写成 1 秒超时），失焦一并回落。
 */
function TimeoutInput({
  step,
  onUpdateStep,
  readOnly,
  className,
}: {
  step: PipelineStep;
  onUpdateStep: (next: PipelineStep) => void;
  readOnly?: boolean;
  className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);

  return (
    <input
      type="number"
      min={1}
      value={draft ?? step.timeout_seconds ?? ''}
      placeholder="默认"
      disabled={readOnly}
      onChange={e => {
        const raw = e.target.value;
        setDraft(raw);
        if (raw === '') {
          // #2513：清空 = 回到「未配置」并**提交**（不是只清本地草稿）。
          // #2382 之后「未配置」是可落库的：组装侧对 None 省略键 → Agent 回落
          // STP_STEP_WALL_CLOCK_SECONDS → 300s；#2454 已让读回不再把它折成 30。
          // 旧行为（只 setDraft、失焦弹回旧值）使「未配置」在 UI 上不可达。
          onUpdateStep({ ...step, timeout_seconds: undefined });
          return;
        }
        const n = parseInt(raw, 10);
        if (Number.isNaN(n)) return;
        // 0（不限）仍不放开：契约要求与 stall_seconds ≥ 1 配对，而本 Inspector
        // 没有停滞钟输入框——放开会造出必然 422 的组合。
        onUpdateStep({ ...step, timeout_seconds: Math.max(1, n) });
      }}
      onBlur={() => setDraft(null)}
      className={className}
    />
  );
}

interface RuntimeConfigCardProps {
  step: PipelineStep;
  onUpdateStep: (next: PipelineStep) => void;
  readOnly?: boolean;
}

function RuntimeConfigCard({ step, onUpdateStep, readOnly }: RuntimeConfigCardProps) {
  const inputCls = cn('h-7 px-2 text-[12px]', PIPELINE_EDITOR.inputInline);
  // 0 与 null 两个非常规取值光看输入框读不出语义（0 像"零秒"，空像"没设"），
  // 各自的实际行为差 300 秒起步，所以显式把结论写在字段下面。
  const timeoutIsSpecial = step.timeout_seconds === 0 || step.timeout_seconds == null;

  return (
    <Card>
      <CardHead>
        <span>执行配置</span>
      </CardHead>
      <CardBody>
        <div className="grid grid-cols-2 gap-1.5">
          <FieldGroup label="超时 (秒)">
            <TimeoutInput
              key={step.step_id}
              step={step}
              onUpdateStep={onUpdateStep}
              readOnly={readOnly}
              className={inputCls}
            />
          </FieldGroup>
          <FieldGroup label="重试次数">
            <input
              type="number"
              min={0}
              max={5}
              value={step.retry ?? 0}
              disabled={readOnly}
              onChange={e => {
                const next = Math.min(5, Math.max(0, parseInt(e.target.value, 10) || 0));
                onUpdateStep({ ...step, retry: next });
              }}
              className={inputCls}
            />
          </FieldGroup>
        </div>

        {timeoutIsSpecial && (
          <div
            data-testid="step-timeout-hint"
            className={cn(
              'flex items-start gap-1.5 px-2 py-1.5 rounded-md text-[11px]',
              ALERT_BANNER.warning,
            )}
          >
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>
              超时 = <strong className="font-mono">{formatStepTimeout(step.timeout_seconds)}</strong>
              ：{stepTimeoutHint(step.timeout_seconds)}
              {step.timeout_seconds === 0 && (
                <>。本编辑器不提供停滞钟字段，该值需经 API 配置后才存得下来。</>
              )}
            </span>
          </div>
        )}

        <div className="flex items-center justify-between py-1">
          <span className={cn('text-[11px] font-bold uppercase tracking-wide', TEXT.subtitle)}>启用步骤</span>
          <button
            type="button"
            disabled={readOnly}
            onClick={() => onUpdateStep({ ...step, enabled: !(step.enabled !== false) })}
            className={cn(
              'relative w-8 h-[18px] rounded-full transition',
              step.enabled !== false ? 'bg-success' : 'bg-muted-foreground/40',
              readOnly ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
            )}
            aria-pressed={step.enabled !== false}
            aria-label={step.enabled !== false ? '禁用步骤' : '启用步骤'}
          >
            <span
              className={cn(
                'absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full bg-background shadow transition-transform',
                step.enabled !== false ? 'translate-x-[14px]' : 'translate-x-0',
              )}
            />
          </button>
        </div>

        <Row label="step_id">
          <input
            type="text"
            value={step.step_id}
            disabled={readOnly}
            onChange={e => onUpdateStep({ ...step, step_id: e.target.value })}
            className={cn('max-w-[60%] h-7 px-2 text-[11px] font-mono', PIPELINE_EDITOR.inputInline)}
          />
        </Row>
      </CardBody>
    </Card>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className={PIPELINE_EDITOR.cardInner}>{children}</div>;
}

function CardHead({ children }: { children: React.ReactNode }) {
  return <div className={PIPELINE_EDITOR.cardHead}>{children}</div>;
}

function CardBody({ children }: { children: React.ReactNode }) {
  return <div className="px-2.5 py-2 grid gap-1.5">{children}</div>;
}

function Row({
  label,
  children,
  align,
}: {
  label: string;
  children: React.ReactNode;
  align?: 'start';
}) {
  return (
    <div
      className={cn(
        'flex justify-between text-[11px] gap-2.5',
        align === 'start' ? 'items-start' : 'items-center',
      )}
    >
      <span className={cn('whitespace-nowrap', TEXT.subtitle)}>{label}</span>
      <div className={cn('text-right text-[12px] font-medium flex-1 flex justify-end items-center gap-1.5', TEXT.body)}>
        {children}
      </div>
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1">
      <span className={cn('text-[11px] font-bold tracking-wide', TEXT.subtitle)}>{label}</span>
      {children}
    </div>
  );
}
