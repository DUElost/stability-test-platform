import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { LogViewer } from '../../components/log/LogViewer';
import { XTerminal, type XTerminalHandle } from '../../components/log/XTerminal';
import { PipelineStepTree, type StepUpdateMessage } from '../../components/pipeline/PipelineStepTree';
import { api, AgentLogOut, JiraDraft } from '../../utils/api';
import { useWebSocket } from '../../hooks/useWebSocket';

// ---------- Types ----------

interface StepLogLine {
  ts: string;
  level: string;
  msg: string;
}

const MAX_LINES_PER_STEP = 5000;

// ---------- Component ----------

export default function TaskDetails() {
  const { taskId } = useParams();
  const id = Number(taskId);
  const [agentLogContent, setAgentLogContent] = useState<string>('');
  const [showAgentLog, setShowAgentLog] = useState(false);
  const [jiraDraftContent, setJiraDraftContent] = useState<string>('');

  // Pipeline state
  const [selectedStepId, setSelectedStepId] = useState<number | null>(null);
  const [selectedStepName, setSelectedStepName] = useState<string | null>(null);
  const [stepUpdates, setStepUpdates] = useState<StepUpdateMessage[]>([]);
  const stepLogBuffers = useRef<Map<string, StepLogLine[]>>(new Map());
  const xtermRef = useRef<XTerminalHandle>(null);
  const manualSelection = useRef(false);

  const { data: task } = useQuery({
    queryKey: ['tasks', id],
    queryFn: () => api.tasks.get(id).then(res => res.data),
    enabled: !!id,
  });

  const { data: runs } = useQuery({
    queryKey: ['tasks', id, 'runs'],
    queryFn: () => api.tasks.getRuns(id, 0, 200).then(res => res.data.items),
    enabled: !!id,
    refetchInterval: task?.status === 'RUNNING' ? 5000 : false,
  });

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

  // Fetch RunSteps for pipeline tasks
  const { data: runSteps } = useQuery({
    queryKey: ['runs', activeRun?.id, 'steps'],
    queryFn: () => api.tasks.getRunSteps(activeRun!.id).then(res => res.data),
    enabled: !!activeRun?.id,
    refetchInterval: task?.status === 'RUNNING' ? 5000 : false,
  });

  const isPipeline = (runSteps?.length ?? 0) > 0;

  // WebSocket for log streaming
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = activeRun
    ? `${wsProtocol}//${window.location.host}/ws/logs/${activeRun.id}?token=${localStorage.getItem('access_token') || 'dev-token-12345'}`
    : '';

  const { lastMessage } = useWebSocket(wsUrl, {
    enabled: !!activeRun && isPipeline,
  });

  // Handle incoming WS messages: STEP_LOG and STEP_UPDATE (12.2, 12.3, 12.4)
  useEffect(() => {
    if (!lastMessage) return;

    if (lastMessage.type === 'STEP_LOG') {
      const { step_id, msg, level, ts } = lastMessage.payload as any;
      if (step_id === undefined || step_id === null) return;

      // Resolve to a stable step key (prefer step name for stages format)
      let stepKey = '';
      if (typeof step_id === 'number') {
        const matched = runSteps?.find(s => s.id === step_id);
        stepKey = matched?.name || String(step_id);
      } else {
        stepKey = String(step_id);
      }
      if (!stepKey) return;

      // Buffer the log line
      const buffer = stepLogBuffers.current.get(stepKey) || [];
      buffer.push({ ts, level, msg });
      if (buffer.length > MAX_LINES_PER_STEP) {
        buffer.splice(0, buffer.length - MAX_LINES_PER_STEP);
      }
      stepLogBuffers.current.set(stepKey, buffer);

      // If this step is currently selected, write to XTerminal
      if (stepKey === selectedStepName && xtermRef.current) {
        xtermRef.current.writeLine(msg, level);
      }
    }

    if (lastMessage.type === 'STEP_UPDATE') {
      const payload = lastMessage.payload as any;
      const update: StepUpdateMessage = {
        type: 'STEP_UPDATE',
        step_id: payload.step_id,
        status: payload.status,
        started_at: payload.started_at,
        finished_at: payload.finished_at,
        exit_code: payload.exit_code,
        error_message: payload.error_message,
      };
      setStepUpdates(prev => [...prev, update]);

      // Auto-follow: when a new step starts RUNNING and no manual selection, switch to it (12.7)
      if (payload.status === 'RUNNING' && !manualSelection.current) {
        setSelectedStepId(payload.step_id);
      }
    }
  }, [lastMessage, selectedStepId]);

  // When selected step changes, load buffered logs into XTerminal (12.6)
  useEffect(() => {
    if (!selectedStepId || !runSteps?.length) return;
    const step = runSteps.find(s => s.id === selectedStepId);
    setSelectedStepName(step?.name || null);
  }, [selectedStepId, runSteps]);

  useEffect(() => {
    if (!selectedStepName || !xtermRef.current) return;
    const buffer = stepLogBuffers.current.get(selectedStepName) || [];
    xtermRef.current.clear();
    if (buffer.length > 0) {
      xtermRef.current.writeLines(buffer.map(l => ({ msg: l.msg, level: l.level })));
    }
  }, [selectedStepName]);

  const handleStepSelect = useCallback((stepId: number) => {
    manualSelection.current = true;
    setSelectedStepId(stepId);
  }, []);

  // Get the selected step name for download filename
  const selectedStep = runSteps?.find(s => s.id === selectedStepId);

  // Agent log mutation
  const queryAgentLogMutation = useMutation({
    mutationFn: async () => {
      if (!activeRun?.host_id) throw new Error('No host_id available');
      const response = await api.tasks.queryAgentLogs({
        host_id: activeRun.host_id,
        log_path: '/tmp/agent.log',
        lines: 200,
      });
      return response.data;
    },
    onSuccess: (data: AgentLogOut) => {
      setAgentLogContent(data.error ? `Error: ${data.error}` : data.content || 'No log content');
      setShowAgentLog(true);
    },
    onError: (error: Error) => {
      setAgentLogContent(`Error: ${error.message}`);
      setShowAgentLog(true);
    },
  });

  const createJiraDraftMutation = useMutation({
    mutationFn: async () => {
      if (!activeRun?.id) throw new Error('No run_id available');
      const response = await api.tasks.createRunJiraDraft(activeRun.id);
      return response.data;
    },
    onSuccess: (data: JiraDraft) => {
      setJiraDraftContent(
        `Project: ${data.project_key}\nComponent: ${data.component || '-'}\n` +
        `Fix Version: ${data.fix_version || '-'}\nAssignee: ${data.assignee || '-'}\n` +
        `Summary: ${data.summary}\nPriority: ${data.priority}\n` +
        `Labels: ${(data.labels || []).join(', ')}\n\n${data.description}`
      );
    },
    onError: (error: Error) => {
      setJiraDraftContent(`Error: ${error.message}`);
    },
  });

  if (!task) return <div>Loading...</div>;

  // ---------- Render ----------

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[calc(100vh-8rem)]">
      {/* Left panel: Task info OR Step tree for pipeline tasks */}
      <div className="lg:col-span-1 bg-white rounded-lg shadow-sm border border-slate-200 overflow-y-auto">
        {isPipeline ? (
          /* Pipeline layout: step tree */
          <div className="flex flex-col h-full">
            <div className="p-4 border-b border-slate-200">
              <h2 className="text-lg font-semibold">{task.name}</h2>
              <div className="flex items-center gap-2 mt-1">
                <span className={`text-xs font-semibold px-2 py-0.5 rounded ${
                  task.status === 'RUNNING' ? 'bg-green-100 text-green-700' :
                  task.status === 'COMPLETED' ? 'bg-indigo-100 text-indigo-700' :
                  task.status === 'FAILED' ? 'bg-red-100 text-red-700' :
                  'bg-slate-100 text-slate-600'
                }`}>
                  {task.status}
                </span>
                {activeRun && (
                  <span className="text-xs text-slate-500">Run #{activeRun.id}</span>
                )}
              </div>
            </div>

            {/* Pipeline step tree */}
            <div className="flex-1 overflow-y-auto bg-slate-900">
              <PipelineStepTree
                steps={runSteps || []}
                selectedStepId={selectedStepId}
                onStepSelect={handleStepSelect}
                stepUpdates={stepUpdates}
              />
            </div>

            {/* Compact debug tools */}
            {activeRun?.host_id && (
              <div className="p-3 border-t border-slate-200 space-y-1.5">
                <button
                  onClick={() => queryAgentLogMutation.mutate()}
                  disabled={queryAgentLogMutation.isPending}
                  className="w-full px-3 py-1.5 bg-slate-800 text-white text-xs rounded hover:bg-slate-700 disabled:bg-slate-400 transition-colors"
                >
                  {queryAgentLogMutation.isPending ? 'Querying...' : 'Agent Logs'}
                </button>
                <button
                  onClick={() => window.open(api.tasks.getRunReportExportUrl(activeRun.id, 'markdown'), '_blank')}
                  className="w-full px-3 py-1.5 bg-indigo-600 text-white text-xs rounded hover:bg-indigo-500 transition-colors"
                >
                  Export Report
                </button>
              </div>
            )}
          </div>
        ) : (
          /* Legacy layout: task metadata panel */
          <div className="p-6">
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
        )}
      </div>

      {/* Right panel: Log viewer */}
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
        ) : isPipeline && activeRun ? (
          /* Pipeline layout: XTerminal for selected step */
          <XTerminal
            ref={xtermRef}
            poolKey={`run_${activeRun.id}_step_${selectedStepId || 'none'}`}
            runId={activeRun.id}
            stepName={selectedStep?.name}
            height="100%"
          />
        ) : wsUrl ? (
          /* Legacy layout: LogViewer */
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
