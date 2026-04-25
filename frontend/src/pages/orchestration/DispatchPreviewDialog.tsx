import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { X, Play, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api, type PipelineStep, type PipelineStepOverride, type WorkflowRun } from '@/utils/api';

interface DispatchPreviewDialogProps {
  open: boolean;
  workflowId: number;
  deviceIds: number[];
  failureThreshold: number;
  onClose: () => void;
  onStarted: (run: WorkflowRun) => void;
}

const STAGE_LABELS: Record<string, string> = {
  prepare: 'Prepare',
  execute: 'Execute',
  post_process: 'Post Process',
};

function overrideKey(templateName: string, stage: string, stepId: string) {
  return `${templateName}::${stage}::${stepId}`;
}

function upsertOverride(
  overrides: PipelineStepOverride[],
  templateName: string,
  stage: 'prepare' | 'execute' | 'post_process',
  stepId: string,
  patch: Partial<PipelineStepOverride>,
): PipelineStepOverride[] {
  const key = overrideKey(templateName, stage, stepId);
  const existingIndex = overrides.findIndex((item) => overrideKey(item.template_name, item.stage, item.step_id) === key);
  const nextItem: PipelineStepOverride = {
    ...(existingIndex >= 0 ? overrides[existingIndex] : { template_name: templateName, stage, step_id: stepId }),
    ...patch,
  };
  if (existingIndex < 0) return [...overrides, nextItem];
  return overrides.map((item, index) => (index === existingIndex ? nextItem : item));
}

function StepPreviewRow({
  templateName,
  stage,
  step,
  onOverride,
}: {
  templateName: string;
  stage: 'prepare' | 'execute' | 'post_process';
  step: PipelineStep;
  onOverride: (patch: Partial<PipelineStepOverride>) => void;
}) {
  return (
    <div className="grid gap-2 rounded-md border border-gray-100 bg-white px-3 py-2 text-xs md:grid-cols-[minmax(0,1fr)_96px_72px_72px] md:items-center">
      <div className="min-w-0">
        <div className="truncate font-medium text-gray-800">{step.step_id}</div>
        <div className="truncate text-gray-400">{step.action}</div>
      </div>
      <input
        aria-label={`覆盖 timeout ${templateName} ${stage} ${step.step_id}`}
        type="number"
        min={1}
        value={step.timeout_seconds}
        className="h-8 rounded border border-gray-200 px-2"
        onChange={(event) => onOverride({ timeout_seconds: Math.max(1, Number.parseInt(event.target.value, 10) || 1) })}
      />
      <input
        aria-label={`覆盖 retry ${templateName} ${stage} ${step.step_id}`}
        type="number"
        min={0}
        max={10}
        value={step.retry ?? 0}
        className="h-8 rounded border border-gray-200 px-2"
        onChange={(event) => onOverride({ retry: Math.min(10, Math.max(0, Number.parseInt(event.target.value, 10) || 0)) })}
      />
      <button
        type="button"
        className={`h-8 rounded border px-2 ${step.enabled === false ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-gray-200 text-gray-600'}`}
        onClick={() => onOverride({ enabled: step.enabled === false })}
      >
        {step.enabled === false ? 'Disabled' : 'Enabled'}
      </button>
    </div>
  );
}

export default function DispatchPreviewDialog({
  open,
  workflowId,
  deviceIds,
  failureThreshold,
  onClose,
  onStarted,
}: DispatchPreviewDialogProps) {
  const [overrides, setOverrides] = useState<PipelineStepOverride[]>([]);
  const payload = useMemo(() => ({
    device_ids: deviceIds,
    failure_threshold: failureThreshold,
    step_overrides: overrides,
  }), [deviceIds, failureThreshold, overrides]);

  const previewQuery = useQuery({
    queryKey: ['workflow-dispatch-preview', workflowId, payload],
    queryFn: () => api.orchestration.previewRun(workflowId, payload),
    enabled: open && workflowId > 0 && deviceIds.length > 0,
  });

  const runMutation = useMutation({
    mutationFn: () => api.orchestration.run(workflowId, payload),
    onSuccess: onStarted,
  });

  if (!open) return null;

  const preview = previewQuery.data;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-5xl flex-col rounded-xl bg-white shadow-xl">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Dispatch Preview</h2>
            <p className="text-xs text-gray-500">
              {deviceIds.length} devices · threshold {Math.round(failureThreshold * 100)}%
            </p>
          </div>
          <button type="button" className="rounded p-1 text-gray-400 hover:bg-gray-100" onClick={onClose}>
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {previewQuery.isLoading && (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <RefreshCw className="h-4 w-4 animate-spin" />
              生成预览中...
            </div>
          )}
          {previewQuery.isError && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {(previewQuery.error as Error).message || '预览失败'}
            </div>
          )}
          {preview && (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-lg bg-slate-50 p-3">
                  <div className="text-xs text-gray-500">Job Count</div>
                  <div className="text-lg font-semibold text-gray-900">{preview.job_count}</div>
                </div>
                <div className="rounded-lg bg-slate-50 p-3">
                  <div className="text-xs text-gray-500">Templates</div>
                  <div className="text-lg font-semibold text-gray-900">{preview.template_count}</div>
                </div>
                <div className="rounded-lg bg-slate-50 p-3">
                  <div className="text-xs text-gray-500">Executable Steps / Device</div>
                  <div className="text-lg font-semibold text-gray-900">{preview.executable_steps_per_device}</div>
                </div>
              </div>

              {preview.templates.map((template) => (
                <section key={template.name} className="rounded-lg border border-gray-200 p-3">
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold text-gray-900">{template.name}</h3>
                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                      {template.executable_steps}/{template.total_steps} executable
                    </span>
                  </div>
                  <div className="space-y-3">
                    {(['prepare', 'execute', 'post_process'] as const).map((stage) => {
                      const steps = template.resolved_pipeline.stages[stage] ?? [];
                      if (steps.length === 0) return null;
                      return (
                        <div key={stage} className="space-y-2">
                          <div className="text-xs font-medium text-gray-500">{STAGE_LABELS[stage]}</div>
                          {steps.map((step) => (
                            <StepPreviewRow
                              key={`${stage}-${step.step_id}`}
                              templateName={template.name}
                              stage={stage}
                              step={step}
                              onOverride={(patch) => setOverrides((prev) => (
                                upsertOverride(prev, template.name, stage, step.step_id, patch)
                              ))}
                            />
                          ))}
                        </div>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t px-4 py-3">
          <Button type="button" variant="outline" onClick={onClose} disabled={runMutation.isPending}>
            取消
          </Button>
          <Button
            type="button"
            onClick={() => runMutation.mutate()}
            disabled={!preview || previewQuery.isLoading || runMutation.isPending}
          >
            <Play className="mr-1 h-4 w-4" />
            {runMutation.isPending ? '发起中...' : '确认发起'}
          </Button>
        </div>
      </div>
    </div>
  );
}
