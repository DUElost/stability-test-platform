import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, AlertTriangle } from 'lucide-react';
import type { PipelinePhase, PipelineStep, ScriptEntry } from '@/utils/api/types';
import { ALERT_BANNER, PIPELINE_EDITOR, STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

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
  const scriptName = useMemo(
    () => (step?.action?.startsWith('script:') ? step.action.slice(7) : (step?.action ?? '')),
    [step?.action],
  );

  const matchedScript = useMemo(
    () => scripts.find(s => s.name === scriptName && s.version === step?.version) ?? null,
    [scripts, scriptName, step?.version],
  );

  const versionsForName = useMemo(
    () => scripts.filter(s => s.name === scriptName).sort((a, b) => b.version.localeCompare(a.version)),
    [scripts, scriptName],
  );

  return (
    <aside className={cn(PIPELINE_EDITOR.panel, 'border-l')}>
      <header className={cn('px-4 py-3', PIPELINE_EDITOR.panelHeader)}>
        <div className={cn('text-sm font-bold', TEXT.heading)}>步骤属性</div>
        <div className={cn('text-[11px] mt-0.5 font-mono truncate', TEXT.subtitle)}>
          {step
            ? `${scriptName || '—'} / v${(step.version || '').replace(/^v/, '') || '—'}`
            : '未选择步骤'}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-2.5 grid gap-2">
        {!step ? (
          <EmptyState />
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
                onUpdateStep({
                  ...step,
                  action: `script:${name}`,
                  version,
                  params: {},
                });
              }}
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

function EmptyState() {
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
  const params = matchedScript?.default_params ?? {};
  const paramKeys = Object.keys(params);
  const allActive = scripts.filter(s => s.is_active);
  const knownNames = Array.from(new Set(allActive.map(s => s.name))).sort();
  const isUnknown = scriptName && !matchedScript;

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
              当前脚本 <code className="font-mono">{scriptName}@{step.version || '?'}</code> 未在已激活脚本中找到，请重新选择。
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

        <Row label="参数" align="start">
          {paramKeys.length === 0 ? (
            <span className={cn('text-[11px]', TEXT.subtitle)}>无默认参数</span>
          ) : (
            <div className="flex flex-wrap gap-1 justify-end max-w-[68%]">
              {paramKeys.map(k => (
                <span
                  key={k}
                  title={JSON.stringify(params[k])}
                  className={cn('font-mono text-[10px] px-1.5 py-px rounded-[3px]', STATUS_CHIP.muted)}
                >
                  {k}
                </span>
              ))}
            </div>
          )}
        </Row>
      </CardBody>
    </Card>
  );
}

interface RuntimeConfigCardProps {
  step: PipelineStep;
  onUpdateStep: (next: PipelineStep) => void;
  readOnly?: boolean;
}

function RuntimeConfigCard({ step, onUpdateStep, readOnly }: RuntimeConfigCardProps) {
  const inputCls = cn('h-7 px-2 text-[12px]', PIPELINE_EDITOR.inputInline);

  return (
    <Card>
      <CardHead>
        <span>执行配置</span>
      </CardHead>
      <CardBody>
        <div className="grid grid-cols-2 gap-1.5">
          <FieldGroup label="超时 (秒)">
            <input
              type="number"
              min={1}
              value={step.timeout_seconds ?? ''}
              placeholder="-"
              disabled={readOnly}
              onChange={e => {
                const raw = e.target.value;
                const next = raw === '' ? 0 : Math.max(1, parseInt(raw, 10) || 30);
                onUpdateStep({ ...step, timeout_seconds: next });
              }}
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
