/**
 * JiraSubmitPanel — ADR-0025 §10 去重→Jira 批量提单面板（表单化美化版）。
 *
 * 保留原一键执行链路：上传文件或选 PlanRun 产物 + vendor/stage/dry-run/reporter
 * → 厂商脚本 subprocess → RunConsole 实时日志。布局规范化为表单排版。
 */
import { useEffect, useState } from 'react';
import { Play, Square, Upload, FileSpreadsheet } from 'lucide-react';
import { dedup, type JiraVendor, type JiraStage } from '@/utils/api/dedup';
import { api, type PlanRun } from '@/utils/api';
import LiveConsole from '@/components/console/LiveConsole';
import { Button } from '@/components/ui/button';
import { ALERT_BANNER, FORM, INTERACTIVE, STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatLocalDateTime } from '@/utils/format';

interface ScanArtifact {
  id: number;
  host_id: string | null;
  storage_uri: string;
  artifact_type: string;
  size_bytes: number | null;
  created_at: string | null;
}

type Source = 'upload' | 'plan_run';

export default function JiraSubmitPanel() {
  const [vendor, setVendor] = useState<JiraVendor>('transsion');
  const [stage, setStage] = useState<JiraStage>('upload_list');
  const [dryRun, setDryRun] = useState(true);
  const [reporter, setReporter] = useState('');
  const [source, setSource] = useState<Source>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [planRunId, setPlanRunId] = useState<number | ''>('');
  const [artifactId, setArtifactId] = useState<number | ''>('');
  const [consoleRunId, setConsoleRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // PlanRun 产物选择（plan_run 模式）：手动拉取，避开 react-query Provider 依赖
  const [planRuns, setPlanRuns] = useState<PlanRun[]>([]);
  const [artifacts, setArtifacts] = useState<ScanArtifact[]>([]);
  const [loadingArtifacts, setLoadingArtifacts] = useState(false);

  useEffect(() => {
    if (source !== 'plan_run') return;
    let cancelled = false;
    api.planRuns.list(0, 10).then(rs => { if (!cancelled) setPlanRuns(rs); }).catch(() => {});
    return () => { cancelled = true; };
  }, [source]);

  useEffect(() => {
    if (source !== 'plan_run' || planRunId === '') { setArtifacts([]); return; }
    let cancelled = false;
    setLoadingArtifacts(true);
    api.planRuns.getDedupStatus(planRunId).then(res => {
      if (cancelled) return;
      setArtifacts((res.artifacts as ScanArtifact[]).filter(
        a => a.artifact_type === 'scan_result_xls' || a.artifact_type === 'merge_result_xls',
      ));
    }).catch(() => {}).finally(() => { if (!cancelled) setLoadingArtifacts(false); });
    return () => { cancelled = true; };
  }, [source, planRunId]);

  const running = status === 'RUNNING' || submitting;

  const onRun = async () => {
    setError(null);
    if (source === 'upload') {
      if (!file) {
        setError('请先选择文件：生成上传模板=去重后 Result_*.xls；建单=stage1 产出的 JIRA_Upload_List_*.xlsx');
        return;
      }
    } else {
      if (!artifactId) {
        setError('请选择一个 PlanRun 去重产物');
        return;
      }
    }
    setSubmitting(true);
    try {
      const res = await dedup.startJiraRun({
        vendor, stage, dryRun,
        reporter: reporter || undefined,
        source,
        artifactId: source === 'plan_run' ? Number(artifactId) : undefined,
        file: source === 'upload' && file ? file : undefined,
      });
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

  const expectedFileLabel = stage === 'upload_list' ? '去重后 Result_*.xls' : 'JIRA_Upload_List_*.xlsx';

  return (
    <section className="space-y-5" data-testid="jira-submit-panel">
      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <label htmlFor="jira-vendor" className={FORM.label}>厂商</label>
          <select
            id="jira-vendor"
            data-testid="jira-vendor"
            value={vendor}
            onChange={(e) => setVendor(e.target.value as JiraVendor)}
            className={cn(FORM.select, 'min-w-0')}
            disabled={running}
          >
            <option value="transsion">Transsion</option>
            <option value="tinno">Tinno</option>
          </select>
        </div>

        <div>
          <label htmlFor="jira-stage" className={FORM.label}>阶段</label>
          <select
            id="jira-stage"
            data-testid="jira-stage"
            value={stage}
            onChange={(e) => setStage(e.target.value as JiraStage)}
            className={cn(FORM.select, 'min-w-0')}
            disabled={running}
          >
            <option value="upload_list">生成上传模板</option>
            <option value="create">建单</option>
          </select>
          <p className={FORM.hint}>
            {stage === 'upload_list'
              ? '消费去重 Result_*.xls，产出 JIRA 上传模板'
              : '消费上传模板，批量创建 Jira issue'}
          </p>
        </div>

        <div>
          <label className={FORM.label}>运行模式</label>
          <label className={cn('flex h-10 items-center gap-2 text-sm', TEXT.body)}>
            <input
              type="checkbox"
              data-testid="jira-dryrun"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              disabled={running}
              className="h-4 w-4 rounded border-border"
            />
            dry-run（仅预览，不真建单）
          </label>
        </div>
      </div>

      <div>
        <label className={FORM.label}>数据来源</label>
        <div className="flex gap-4">
          <label className={cn('flex items-center gap-2 text-sm', TEXT.body)}>
            <input
              type="radio"
              data-testid="jira-source-upload"
              checked={source === 'upload'}
              onChange={() => { setSource('upload'); setArtifactId(''); setPlanRunId(''); }}
              disabled={running}
            />
            手动上传文件
          </label>
          <label className={cn('flex items-center gap-2 text-sm', TEXT.body)}>
            <input
              type="radio"
              data-testid="jira-source-plan-run"
              checked={source === 'plan_run'}
              onChange={() => setSource('plan_run')}
              disabled={running}
            />
            选择 PlanRun 去重产物
          </label>
        </div>
      </div>

      {source === 'upload' && (
        <div>
          <label className={FORM.label}>输入文件</label>
          <label
            className={cn(
              'flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-border bg-muted/30 px-4 py-3 text-sm transition-colors',
              running && 'pointer-events-none opacity-60',
              INTERACTIVE.hover,
            )}
          >
            {file ? (
              <FileSpreadsheet className="h-5 w-5 text-primary" />
            ) : (
              <Upload className="h-5 w-5 text-muted-foreground" />
            )}
            <span className={file ? TEXT.heading : TEXT.subtitle}>
              {file ? file.name : `选择 ${expectedFileLabel}`}
            </span>
            <input
              type="file"
              data-testid="jira-file"
              accept=".xls,.xlsx"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={running}
              className="hidden"
            />
          </label>
          <p className={FORM.hint}>期望：{expectedFileLabel}</p>
        </div>
      )}

      {source === 'plan_run' && (
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label htmlFor="jira-plan-run" className={FORM.label}>PlanRun</label>
            <select
              id="jira-plan-run"
              data-testid="jira-plan-run"
              className={cn(FORM.select, 'min-w-0')}
              value={planRunId}
              onChange={(e) => { setPlanRunId(e.target.value ? Number(e.target.value) : ''); setArtifactId(''); }}
              disabled={running}
            >
              <option value="">选择 PlanRun…</option>
              {(planRuns).map((r: PlanRun) => (
                <option key={r.id} value={r.id}>
                  #{r.id} {r.ended_at ? `· ${formatLocalDateTime(r.ended_at)}` : ''}
                </option>
              ))}
            </select>
            <p className={FORM.hint}>列出最近 10 个 PlanRun</p>
          </div>
          <div>
            <label htmlFor="jira-artifact" className={FORM.label}>去重产物</label>
            <select
              id="jira-artifact"
              data-testid="jira-artifact"
              className={cn(FORM.select, 'min-w-0')}
              value={artifactId}
              onChange={(e) => setArtifactId(e.target.value ? Number(e.target.value) : '')}
              disabled={running || !planRunId}
            >
              <option value="">选择产物…</option>
              {artifacts.map(a => (
                <option key={a.id} value={a.id}>
                  {a.artifact_type === 'merge_result_xls' ? 'Merge' : 'Scan'} · {a.storage_uri.split('/').pop() || a.storage_uri}
                </option>
              ))}
            </select>
            <p className={FORM.hint}>
              {planRunId && loadingArtifacts ? '加载中…' : `共 ${artifacts.length} 个产物`}
            </p>
          </div>
        </div>
      )}

      {stage === 'create' && (
        <div>
          <label htmlFor="jira-reporter" className={FORM.label}>Reporter（建单负责人，可选）</label>
          <input
            id="jira-reporter"
            type="text"
            data-testid="jira-reporter"
            placeholder="如 zhangsan"
            value={reporter}
            onChange={(e) => setReporter(e.target.value)}
            disabled={running}
            className={FORM.input}
          />
        </div>
      )}

      <div className="flex items-center gap-2">
        <Button
          data-testid="jira-run-btn"
          onClick={onRun}
          disabled={running}
          size="sm"
          className="gap-1"
        >
          <Play className="h-4 w-4" /> 一键执行
        </Button>
        {consoleRunId && status === 'RUNNING' && (
          <Button
            data-testid="jira-cancel-btn"
            onClick={onCancel}
            variant="outline"
            size="sm"
            className={cn('gap-1', STATUS_CHIP.destructive, 'border-destructive/25 hover:bg-destructive/15')}
          >
            <Square className="h-4 w-4" /> 取消
          </Button>
        )}
        {status && status !== 'RUNNING' && (
          <span className={cn('text-xs', TEXT.subtitle)}>状态：{status}</span>
        )}
      </div>

      {error && (
        <div data-testid="jira-error" className={cn('rounded border px-3 py-2 text-sm', ALERT_BANNER.destructive)}>
          {error}
        </div>
      )}

      {consoleRunId && (
        <LiveConsole consoleRunId={consoleRunId} onStatusChange={setStatus} enableIssueCount />
      )}
    </section>
  );
}