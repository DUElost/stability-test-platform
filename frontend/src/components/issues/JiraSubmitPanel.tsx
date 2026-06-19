/**
 * JiraSubmitPanel — ADR-0025 §10 去重→Jira 提单面板（实用优先）。
 *
 * 文档上传 + 参数菜单(厂商/阶段/dry-run) + 一键执行 + Jenkins 式 web 实时日志(LiveConsole)。
 * 平台只 subprocess 调用成熟厂商工具(Transsion/Tinno)，不重造提单逻辑。
 */
import { useState } from 'react';
import { Play, Square, Upload } from 'lucide-react';
import { dedup, type JiraVendor, type JiraStage } from '@/utils/api/dedup';
import LiveConsole from '@/components/console/LiveConsole';

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
    <section className="rounded-xl border border-gray-200 bg-white" data-testid="jira-submit-panel">
      <div className="border-b px-4 py-2 text-sm font-semibold text-gray-800">Jira 提单（去重报告）</div>

      <div className="space-y-3 p-4">
        {/* 参数菜单 */}
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-1">
            厂商
            <select
              data-testid="jira-vendor"
              value={vendor}
              onChange={(e) => setVendor(e.target.value as JiraVendor)}
              className="rounded border border-gray-300 px-2 py-1"
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
              className="rounded border border-gray-300 px-2 py-1"
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

        {/* reporter（create 阶段建单负责人，可选） */}
        {stage === 'create' && (
          <input
            type="text"
            data-testid="jira-reporter"
            placeholder="reporter（建单负责人，可选）"
            value={reporter}
            onChange={(e) => setReporter(e.target.value)}
            disabled={running}
            className="w-full rounded border border-gray-300 px-2 py-1 text-xs"
          />
        )}

        {/* 文档上传（两阶段均需）：upload_list=Result_*.xls；create=JIRA_Upload_List_*.xlsx */}
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <Upload className="h-4 w-4" />
          <span className="text-gray-500">
            {stage === 'upload_list' ? '去重后 Result_*.xls' : 'JIRA_Upload_List_*.xlsx'}
          </span>
          <input
            type="file"
            data-testid="jira-file"
            accept=".xls,.xlsx"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            disabled={running}
          />
          {file && <span className="text-gray-500">{file.name}</span>}
        </label>

        {/* 一键执行 / 取消 */}
        <div className="flex items-center gap-2">
          <button
            data-testid="jira-run-btn"
            onClick={onRun}
            disabled={running}
            className="inline-flex items-center gap-1 rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          >
            <Play className="h-3.5 w-3.5" /> 一键执行
          </button>
          {consoleRunId && status === 'RUNNING' && (
            <button
              data-testid="jira-cancel-btn"
              onClick={onCancel}
              className="inline-flex items-center gap-1 rounded border border-red-300 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100"
            >
              <Square className="h-3.5 w-3.5" /> 取消
            </button>
          )}
        </div>

        {error && (
          <div data-testid="jira-error" className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </div>
        )}

        {/* Jenkins 式 web 实时日志 */}
        {consoleRunId && (
          <LiveConsole consoleRunId={consoleRunId} onStatusChange={setStatus} />
        )}
      </div>
    </section>
  );
}
