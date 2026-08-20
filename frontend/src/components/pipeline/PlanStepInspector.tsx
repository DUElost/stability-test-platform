import { useMemo, useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, AlertTriangle } from 'lucide-react';
import type { PipelinePhase, PipelineStep, ScriptEntry } from '@/utils/api/types';
import { ALERT_BANNER, PIPELINE_EDITOR, STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
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
  step: PipelineStep | null;
  phase: PipelinePhase | null;
  index: number | null;
  scripts: ScriptEntry[];
  onUpdateStep: (next: PipelineStep) => void;
  readOnly?: boolean;
}

export default function PlanStepInspector({
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
                  const validKeys = new Set([
                    ...Object.keys(newScript?.param_schema ?? {}),
                    ...Object.keys(newScript?.default_params ?? {}),
                  ]);
                  params = Object.fromEntries(
                    Object.entries(step.params).filter(([k]) => validKeys.has(k)),
                  );
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
              step={step}
              matchedScript={matchedScript}
              onUpdateStep={onUpdateStep}
              readOnly={readOnly}
            />

            <RuntimeConfigCard step={step} onUpdateStep={onUpdateStep} readOnly={readOnly} />

            {scriptName && (
              <Link
                to={`/scripts?name=${encodeURIComponent(scriptName)}`}
                className={cn(
                  'inline-flex items-center justify-center gap-1.5 px-2.5 py-2 text-[11px] font-bold rounded-md transition',
                  PIPELINE_EDITOR.linkBtn,
                )}
              >
                在脚本管理中编辑参数
                <ArrowUpRight className="w-3.5 h-3.5" />
              </Link>
            )}
          </>
        )}
      </div>

      <footer className={cn('px-3 py-2 text-[10px] leading-relaxed', PIPELINE_EDITOR.panelHeader, TEXT.subtitle)}>
        {step
          ? `位于 ${phase ?? '—'} #${(index ?? 0) + 1}。脚本的参数和默认值在脚本管理页面维护。`
          : '点击中央画布的步骤可在此查看 / 编辑属性。'}
      </footer>
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
        <span className={cn('inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-bold', STATUS_CHIP.primary)}>
          来自脚本管理
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
  step: PipelineStep;
  matchedScript: ScriptEntry | null;
  onUpdateStep: (next: PipelineStep) => void;
  readOnly?: boolean;
}

function ParamFormCard({ step, matchedScript, onUpdateStep, readOnly }: ParamFormCardProps) {
  const schema = matchedScript?.param_schema ?? {};
  const schemaKeys = Object.keys(schema);
  const defaultParams = useMemo(
    () => matchedScript?.default_params ?? {},
    [matchedScript?.default_params],
  );
  const stepParams = useMemo(() => step.params ?? {}, [step.params]);

  /** Merge: step.params overrides default_params overrides schema.default */
  const resolvedValue = useCallback(
    (key: string, field: ParamFieldSchema): unknown => {
      if (key in stepParams) return stepParams[key];
      if (key in defaultParams) return defaultParams[key];
      return field.default;
    },
    [stepParams, defaultParams],
  );

  const setParam = useCallback(
    (key: string, value: unknown) => {
      const next = { ...stepParams, [key]: value };
      // Remove entry if it equals the default — keep payload minimal
      if (value === defaultParams[key] || (value === undefined && !(key in defaultParams))) {
        delete next[key];
      }
      onUpdateStep({ ...step, params: next });
    },
    [step, stepParams, defaultParams, onUpdateStep],
  );

  if (schemaKeys.length === 0) {
    const dpKeys = Object.keys(defaultParams);
    if (dpKeys.length === 0) return null;
    // Fallback: no schema but has default_params — show as read-only tags
    return (
      <Card>
        <CardHead>
          <span>脚本参数</span>
          <span className={cn('inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-bold', STATUS_CHIP.muted)}>
            无 schema
          </span>
        </CardHead>
        <CardBody>
          <div className="flex flex-wrap gap-1">
            {dpKeys.map(k => (
              <span
                key={k}
                title={JSON.stringify(defaultParams[k])}
                className={cn('font-mono text-[10px] px-1.5 py-px rounded-[3px]', STATUS_CHIP.muted)}
              >
                {k}
              </span>
            ))}
          </div>
        </CardBody>
      </Card>
    );
  }

  // Sort: required first, then optional
  const sortedKeys = [...schemaKeys].sort((a, b) => {
    const aField = schema[a] as ParamFieldSchema | undefined;
    const bField = schema[b] as ParamFieldSchema | undefined;
    const aReq = aField?.required ? 0 : 1;
    const bReq = bField?.required ? 0 : 1;
    return aReq - bReq;
  });

  return (
    <Card>
      <CardHead>
        <span>脚本参数</span>
        {matchedScript && (
          <span className={cn('inline-flex items-center px-1.5 py-px rounded-full text-[10px] font-bold', STATUS_CHIP.primary)}>
            {schemaKeys.length} 项
          </span>
        )}
      </CardHead>
      <CardBody>
        {sortedKeys.map(key => {
          const field = schema[key] as ParamFieldSchema | undefined;
          if (!field) return null;
          return (
            <ParamFieldRow
              key={key}
              fieldKey={key}
              field={field}
              value={resolvedValue(key, field)}
              onChange={v => setParam(key, v)}
              disabled={!!readOnly}
            />
          );
        })}
      </CardBody>
    </Card>
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

function ParamFieldRow({ fieldKey, field, value, onChange, disabled }: ParamFieldRowProps) {
  const label = field.label || fieldKey;
  const isRequired = !!field.required;
  const displayLabel = isRequired ? `${label} *` : label;
  const inputCls = cn('max-w-[60%] h-7 px-2 text-[12px]', PIPELINE_EDITOR.inputInline);

  // string with enum → <select>
  if (field.type === 'string' && field.enum && field.enum.length > 0) {
    return (
      <Row label={displayLabel}>
        <select
          disabled={disabled}
          value={String(value ?? '')}
          onChange={e => onChange(e.target.value || undefined)}
          className={inputCls}
        >
          <option value="">—</option>
          {field.enum.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </Row>
    );
  }

  // boolean → toggle switch
  if (field.type === 'boolean') {
    const checked = value === true || value === 'true';
    return (
      <Row label={displayLabel}>
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
        >
          <span
            className={cn(
              'absolute top-0.5 left-0.5 w-3.5 h-3.5 rounded-full bg-background shadow transition-transform',
              checked ? 'translate-x-[14px]' : 'translate-x-0',
            )}
          />
        </button>
      </Row>
    );
  }

  // integer / number → numeric input
  if (field.type === 'integer' || field.type === 'number') {
    return (
      <Row label={displayLabel}>
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
        />
      </Row>
    );
  }

  // string (no enum) → text input
  return (
    <Row label={displayLabel}>
      <input
        type="text"
        value={String(value ?? '')}
        placeholder={field.description || ''}
        disabled={disabled}
        onChange={e => onChange(e.target.value || undefined)}
        className={cn(inputCls, 'min-w-[50%]')}
      />
    </Row>
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
        if (raw === '') return;
        const n = parseInt(raw, 10);
        if (Number.isNaN(n)) return;
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
          <span className={cn('text-[10px] font-bold uppercase tracking-wide', TEXT.subtitle)}>启用步骤</span>
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
      <span className={cn('text-[10px] font-bold tracking-wide', TEXT.subtitle)}>{label}</span>
      {children}
    </div>
  );
}
