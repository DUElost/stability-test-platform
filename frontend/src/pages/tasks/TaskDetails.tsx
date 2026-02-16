import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { LogViewer } from '../../components/log/LogViewer';
import { api, AgentLogOut, JiraDraft } from '../../utils/api';

export default function TaskDetails() {
  const { taskId } = useParams();
  const id = Number(taskId);
  const [agentLogContent, setAgentLogContent] = useState<string>('');
  const [showAgentLog, setShowAgentLog] = useState(false);
  const [jiraDraftContent, setJiraDraftContent] = useState<string>('');

  const { data: task } = useQuery({
    queryKey: ['tasks', id],
    queryFn: () => api.tasks.get(id).then(res => res.data),
    enabled: !!id,
  });

  // 获取任务的运行记录，用于获取 run_id
  const { data: runs } = useQuery({
    queryKey: ['tasks', id, 'runs'],
    queryFn: () => api.tasks.getRuns(id).then(res => res.data),
    enabled: !!id,
    refetchInterval: task?.status === 'RUNNING' ? 5000 : false,
  });

  // 获取当前活跃的 run（最新的运行记录）
  const activeRun = runs?.[0];
  const riskSummary = activeRun?.risk_summary;
  const { data: runReport } = useQuery({
    queryKey: ['runs', activeRun?.id, 'report'],
    queryFn: () => api.tasks.getRunReport(activeRun!.id).then(res => res.data),
    enabled: !!activeRun?.id,
    refetchInterval: task?.status === 'RUNNING' ? 5000 : false,
  });
  const reportRiskSummary = runReport?.risk_summary;
  const riskAlerts = runReport?.alerts || [];
  const latestArtifact = activeRun?.artifacts?.length
    ? activeRun.artifacts[activeRun.artifacts.length - 1]
    : null;

  // 查询Agent日志
  const queryAgentLogMutation = useMutation({
    mutationFn: async () => {
      if (!activeRun?.host_id) {
        throw new Error('No host_id available');
      }
      const response = await api.tasks.queryAgentLogs({
        host_id: activeRun.host_id,
        log_path: '/tmp/agent.log',
        lines: 200,
      });
      return response.data;
    },
    onSuccess: (data: AgentLogOut) => {
      if (data.error) {
        setAgentLogContent(`Error: ${data.error}`);
      } else {
        setAgentLogContent(data.content || 'No log content');
      }
      setShowAgentLog(true);
    },
    onError: (error: Error) => {
      setAgentLogContent(`Error: ${error.message}`);
      setShowAgentLog(true);
    },
  });

  const createJiraDraftMutation = useMutation({
    mutationFn: async () => {
      if (!activeRun?.id) {
        throw new Error('No run_id available');
      }
      const response = await api.tasks.createRunJiraDraft(activeRun.id);
      return response.data;
    },
    onSuccess: (data: JiraDraft) => {
      setJiraDraftContent(
        `Project: ${data.project_key}\n` +
        `Component: ${data.component || '-'}\n` +
        `Fix Version: ${data.fix_version || '-'}\n` +
        `Assignee: ${data.assignee || '-'}\n` +
        `Summary: ${data.summary}\n` +
        `Priority: ${data.priority}\n` +
        `Labels: ${(data.labels || []).join(', ')}\n\n` +
        `${data.description}`
      );
    },
    onError: (error: Error) => {
      setJiraDraftContent(`Error: ${error.message}`);
    },
  });

  if (!task) return <div>Loading...</div>;

  // Dynamic WebSocket URL based on current host and run_id
  // 使用 run_id 而不是 task_id 来连接 WebSocket
  const wsUrl = activeRun
    ? `ws://${window.location.hostname}:8000/ws/logs/${activeRun.id}`
    : null;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[calc(100vh-8rem)]">
      <div className="lg:col-span-1 bg-white p-6 rounded-lg shadow-sm border border-slate-200">
        <h2 className="text-lg font-semibold mb-4">Task Details</h2>
        <div className="space-y-3 text-sm">
          <div>
            <label className="text-slate-500 block">ID</label>
            <span className="font-mono">{task.id}</span>
          </div>
          <div>
            <label className="text-slate-500 block">Name</label>
            <span>{task.name}</span>
          </div>
          <div>
            <label className="text-slate-500 block">Status</label>
            <span className="font-medium">{task.status}</span>
          </div>
          {activeRun && (
            <>
              <div className="pt-4 mt-2 border-t border-slate-100">
                <div className="flex justify-between items-center mb-1">
                  <label className="text-slate-500 text-xs font-semibold uppercase">Execution Progress</label>
                  <span className="text-indigo-600 font-bold text-xs">{activeRun.progress || 0}%</span>
                </div>
                <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden border border-slate-200">
                  <div 
                    className="bg-indigo-600 h-full transition-all duration-500 ease-out" 
                    style={{ width: `${activeRun.progress || 0}%` }}
                  />
                </div>
                {activeRun.progress_message && (
                  <p className="text-[10px] text-slate-400 mt-1 italic">{activeRun.progress_message}</p>
                )}
              </div>
              <div className="grid grid-cols-2 gap-3 pt-4">
                <div>
                  <label className="text-slate-500 block text-[10px] uppercase">Run ID</label>
                  <span className="font-mono text-xs">{activeRun.id}</span>
                </div>
                <div>
                  <label className="text-slate-500 block text-[10px] uppercase">Run Status</label>
                  <span className={`text-xs font-semibold ${
                    activeRun.status === 'RUNNING' ? 'text-green-600 animate-pulse' : 
                    activeRun.status === 'FINISHED' ? 'text-indigo-600' : 'text-slate-600'
                  }`}>{activeRun.status}</span>
                </div>
              </div>
              <div>
                <label className="text-slate-500 block">Host ID</label>
                <span className="font-mono">{activeRun.host_id}</span>
              </div>
              <div>
                <label className="text-slate-500 block">Artifacts</label>
                <span className="font-mono">{activeRun.artifacts?.length || 0}</span>
              </div>
              <div>
                <label className="text-slate-500 block">Risk Level</label>
                <span className="font-mono">{reportRiskSummary?.risk_level || riskSummary?.risk_level || 'N/A'}</span>
              </div>
              <div>
                <label className="text-slate-500 block">Risk Events</label>
                <span className="font-mono">{reportRiskSummary?.counts?.events_total ?? riskSummary?.counts?.events_total ?? 0}</span>
              </div>
              <div>
                <label className="text-slate-500 block">Alerts</label>
                <span className="font-mono">{riskAlerts.length}</span>
              </div>
              <div>
                <label className="text-slate-500 block">Run Summary</label>
                <div className="text-xs break-all font-mono">{activeRun.log_summary || '-'}</div>
              </div>
              {riskAlerts.length > 0 && (
                <div>
                  <label className="text-slate-500 block">Top Alert</label>
                  <div className="text-xs break-all font-mono text-amber-700">
                    [{riskAlerts[0].severity}] {riskAlerts[0].message}
                  </div>
                </div>
              )}
              {latestArtifact && (
                <div>
                  <label className="text-slate-500 block">Latest Artifact</label>
                  <div className="text-xs break-all font-mono">{latestArtifact.storage_uri}</div>
                  <button
                    className="mt-2 px-3 py-1.5 text-xs bg-indigo-600 text-white rounded hover:bg-indigo-500"
                    onClick={() => {
                      window.open(
                        api.tasks.artifactDownloadUrl(task.id, activeRun.id, latestArtifact.id),
                        '_blank'
                      );
                    }}
                  >
                    Download Latest Artifact
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Agent日志查询按钮 */}
        {activeRun?.host_id && (
          <div className="mt-6 pt-4 border-t border-slate-200">
            <h3 className="text-sm font-medium mb-3">Debug Tools</h3>
            <button
              onClick={() => queryAgentLogMutation.mutate()}
              disabled={queryAgentLogMutation.isPending}
              className="w-full px-4 py-2 bg-slate-800 text-white text-sm rounded hover:bg-slate-700 disabled:bg-slate-400 disabled:cursor-not-allowed transition-colors"
            >
              {queryAgentLogMutation.isPending ? 'Querying...' : 'Query Agent Logs'}
            </button>
            <p className="text-xs text-slate-500 mt-2">
              Query agent logs from Linux host via SSH
            </p>
            <button
              onClick={() => window.open(api.tasks.getRunReportExportUrl(activeRun.id, 'markdown'), '_blank')}
              className="mt-3 w-full px-4 py-2 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-500 transition-colors"
            >
              Export Run Report (Markdown)
            </button>
            <button
              onClick={() => createJiraDraftMutation.mutate()}
              disabled={createJiraDraftMutation.isPending}
              className="mt-2 w-full px-4 py-2 bg-amber-600 text-white text-sm rounded hover:bg-amber-500 disabled:bg-slate-400 disabled:cursor-not-allowed transition-colors"
            >
              {createJiraDraftMutation.isPending ? 'Generating Draft...' : 'Generate JIRA Draft'}
            </button>
            {jiraDraftContent && (
              <pre className="mt-3 p-2 bg-slate-100 border border-slate-200 text-[11px] whitespace-pre-wrap break-all max-h-40 overflow-auto">
                {jiraDraftContent}
              </pre>
            )}
          </div>
        )}
      </div>

      <div className="lg:col-span-2 bg-black rounded-lg overflow-hidden border border-slate-800">
        {showAgentLog ? (
          <div className="flex flex-col h-full">
            <div className="flex items-center justify-between p-2 bg-slate-800 border-b border-slate-700">
              <span className="text-slate-400 text-xs uppercase tracking-wider font-bold px-2">Agent Logs (SSH)</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowAgentLog(false)}
                  className="text-xs px-3 py-1 bg-slate-700 text-slate-300 rounded hover:bg-slate-600"
                >
                  Back to Live Console
                </button>
                <button
                  onClick={() => queryAgentLogMutation.mutate()}
                  disabled={queryAgentLogMutation.isPending}
                  className="text-xs px-3 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-500 disabled:bg-slate-500"
                >
                  Refresh
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-4 font-mono text-xs bg-slate-900">
              <pre className="text-slate-300 whitespace-pre-wrap break-all">
                {agentLogContent}
              </pre>
            </div>
          </div>
        ) : wsUrl ? (
          <LogViewer wsUrl={wsUrl} />
        ) : (
          <div className="flex items-center justify-center h-full text-slate-400">
            {task.status === 'PENDING'
              ? 'Task is pending dispatch...'
              : 'No active run available'}
          </div>
        )}
      </div>
    </div>
  );
}
