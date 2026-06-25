/**
 * JiraSubmitPanel — ADR-0025 §10 去重→Jira 提单面板（实用优先）。
 */
import { useState } from 'react';
import { Play, Square, Upload } from 'lucide-react';
import { dedup, type JiraVendor, type JiraStage } from '@/utils/api/dedup';
import LiveConsole from '@/components/console/LiveConsole';
import { Button } from '@/components/ui/button';
import { ALERT_BANNER, FORM, PANEL, STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

export default function JiraSubmitPanel() {
  const [vendor, setVendor] = useState<JiraVendor>('transsion');
  const [stage, setStage] = useState<JiraStage>('upload_list');
  const [dryRun, setDryRun] = useState(true);
  const [reporter, setReporter] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [consoleRunId, setConsoleRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const running = status === 'RUNNING' || submitting;

  const onRun = async () => {
    setError(null);
    if (!file) {
      setError('请先选择文件：生成上传模板=去重后 Result_*.xls；建单=stage1 产出的 JIRA_Upload_List_*.xlsx');
      return;
    }
    setSubmitting(true);
    try {
      const res = await dedup.startJiraRun({ vendor, stage, dryRun, reporter: reporter || undefined, file });
      setConsoleRunId(res.console_run_id);
      setStatus('RUNNING');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const onCancel = async () => {
    if (!consoleRunId) return;
    try {
      await dedup.cancelRun(consoleRunId);
    } catch {
      /* ignore */
    }
  };

  return (
    <section className={cn(PANEL.root)} data-testid="jira-submit-panel">
      <div className={cn('border-b px-4 py-2 text-sm font-semibold', TEXT.heading)}>
        Jira 提单（去重报告）
      </div>

      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-1">
            厂商
            <select
              data-testid="jira-vendor"
              value={vendor}
              onChange={(e) => setVendor(e.target.value as JiraVendor)}
              className={FORM.selectSm}
              disabled={running}
            >
              <option value="transsion">Transsion</option>
              <option value="tinno">Tinno</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            阶段
            <select
              data-testid="jira-stage"
              value={stage}
              onChange={(e) => setStage(e.target.value as JiraStage)}
              className={FORM.selectSm}
              disabled={running}
            >
              <option value="upload_list">生成上传模板</option>
              <option value="create">建单</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              data-testid="jira-dryrun"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              disabled={running}
            />
            dry-run（仅预览，不真建单）
          </label>
        </div>

        {stage === 'create' && (
          <input
            type="text"
            data-testid="jira-reporter"
            placeholder="reporter（建单负责人，可选）"
            value={reporter}
            onChange={(e) => setReporter(e.target.value)}
            disabled={running}
            className={cn(FORM.inputSm, 'w-full pl-3')}
          />
        )}

        <label className={cn('flex items-center gap-2 text-xs', TEXT.body)}>
          <Upload className="h-4 w-4" />
          <span className={TEXT.subtitle}>
            {stage === 'upload_list' ? '去重后 Result_*.xls' : 'JIRA_Upload_List_*.xlsx'}
          </span>
          <input
            type="file"
            data-testid="jira-file"
            accept=".xls,.xlsx"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            disabled={running}
          />
          {file && <span className={TEXT.subtitle}>{file.name}</span>}
        </label>

        <div className="flex items-center gap-2">
          <Button
            data-testid="jira-run-btn"
            onClick={onRun}
            disabled={running}
            size="sm"
            className="gap-1 text-xs h-8"
          >
            <Play className="h-3.5 w-3.5" /> 一键执行
          </Button>
          {consoleRunId && status === 'RUNNING' && (
            <Button
              data-testid="jira-cancel-btn"
              onClick={onCancel}
              variant="outline"
              size="sm"
              className={cn('gap-1 text-xs h-8', STATUS_CHIP.destructive, 'border-destructive/25 hover:bg-destructive/15')}
            >
              <Square className="h-3.5 w-3.5" /> 取消
            </Button>
          )}
        </div>

        {error && (
          <div data-testid="jira-error" className={cn('rounded border px-3 py-2 text-xs', ALERT_BANNER.destructive)}>
            {error}
          </div>
        )}

        {consoleRunId && (
          <LiveConsole consoleRunId={consoleRunId} onStatusChange={setStatus} />
        )}
      </div>
    </section>
  );
}
