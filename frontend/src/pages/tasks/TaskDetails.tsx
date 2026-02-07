import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { LogViewer } from '../../components/log/LogViewer';
import { api, AgentLogOut } from '../../utils/api';

export default function TaskDetails() {
  const { taskId } = useParams();
  const id = Number(taskId);
  const [agentLogContent, setAgentLogContent] = useState<string>('');
  const [showAgentLog, setShowAgentLog] = useState(false);

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
              <div>
                <label className="text-slate-500 block">Run ID</label>
                <span className="font-mono">{activeRun.id}</span>
              </div>
              <div>
                <label className="text-slate-500 block">Run Status</label>
                <span className="font-medium">{activeRun.status}</span>
              </div>
              <div>
                <label className="text-slate-500 block">Host ID</label>
                <span className="font-mono">{activeRun.host_id}</span>
              </div>
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
