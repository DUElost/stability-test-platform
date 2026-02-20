// -*- coding: utf-8 -*-
import { useState, useEffect, useRef } from 'react';
import { api, Task, TaskRun } from '@/utils/api';
import { useWebSocket, WebSocketMessage } from '@/hooks/useWebSocket';
import { FileSearch, RefreshCw, Play, Pause, Download, Search, X, Server, Smartphone } from 'lucide-react';
import { CleanCard } from '@/components/ui/clean-card';

interface LogEntry {
  timestamp: string;
  level: string;
  device: string;
  message: string;
  run_id?: number;
}

export default function LogsPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [taskRuns, setTaskRuns] = useState<TaskRun[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [filter, setFilter] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);
  const [levelFilter, setLevelFilter] = useState<string>('all');

  const logsEndRef = useRef<HTMLDivElement>(null);

  // WebSocket 连接
  const groupId = selectedTask?.group_id;
  const wsUrl = groupId ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/logs/group/${groupId}` : '';

  const { isConnected } = useWebSocket(
    wsUrl,
    {
      enabled: !!groupId,
      onMessage: (msg: WebSocketMessage<any>) => {
        if (msg.type === 'LOG') {
          const payload = msg.payload;
          setLogs(prev => [...prev, {
            timestamp: payload.timestamp,
            level: payload.level || 'INFO',
            device: payload.device,
            message: payload.message,
            run_id: payload.run_id,
          }]);
        }
      },
    }
  );

  // 加载任务列表
  const loadTasks = async () => {
    try {
      const response = await api.tasks.list(0, 200);
      const runningTasks = response.data.items.filter((t: Task) =>
        ['RUNNING', 'QUEUED', 'PENDING'].includes(t.status)
      );
      setTasks(runningTasks.slice(0, 20));
    } catch (error) {
      console.error('加载任务失败:', error);
    }
  };

  // 加载任务运行记录
  const loadTaskRuns = async (taskId: number) => {
    try {
      const response = await api.tasks.getRuns(taskId, 0, 200);
      setTaskRuns(response.data.items);
    } catch (error) {
      console.error('加载运行记录失败:', error);
    }
  };

  // 选择任务
  const handleSelectTask = async (task: Task) => {
    setSelectedTask(task);
    setLogs([]);
    await loadTaskRuns(task.id);
  };

  // 清空日志
  const clearLogs = () => {
    setLogs([]);
  };

  // 下载日志
  const downloadLogs = () => {
    const content = logs.map(l =>
      `[${l.timestamp}] [${l.level}] [${l.device}] ${l.message}`
    ).join('\n');
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `logs_${selectedTask?.name}_${new Date().toISOString()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // 过滤日志
  const filteredLogs = logs.filter(log => {
    if (levelFilter !== 'all' && log.level !== levelFilter) return false;
    if (filter && !log.message.toLowerCase().includes(filter.toLowerCase())) return false;
    return true;
  });

  // 自动滚动
  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  // 初始加载
  useEffect(() => {
    loadTasks();
    const interval = setInterval(loadTasks, 30000);
    return () => clearInterval(interval);
  }, []);

  // 计算任务整体进度
  const overallProgress = () => {
    if (taskRuns.length === 0) return 0;
    const total = taskRuns.reduce((sum, r) => sum + (r.progress || 0), 0);
    return Math.round(total / taskRuns.length);
  };

  return (
    <div className="h-full flex flex-col">
      {/* 页面头部 */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-blue-50 rounded-lg">
            <FileSearch className="w-6 h-6 text-blue-600" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">日志监控</h1>
            <p className="text-sm text-gray-500">
              {isConnected ? '实时连接中' : '未连接'}
              {groupId && ` | 任务组: ${groupId}`}
            </p>
          </div>
        </div>
        <button
          onClick={loadTasks}
          className="flex items-center gap-2 px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          <RefreshCw className="w-4 h-4" />
          刷新
        </button>
      </div>

      <div className="flex-1 flex gap-4 min-h-0">
        {/* 左侧：任务列表 */}
        <div className="w-80 flex-shrink-0 flex flex-col">
          <CleanCard className="flex-1 flex flex-col overflow-hidden">
            <div className="p-4 border-b border-gray-100">
              <h3 className="font-medium text-gray-900">运行中的任务</h3>
            </div>
            <div className="flex-1 overflow-y-auto">
              {tasks.length === 0 ? (
                <div className="p-4 text-center text-gray-400 text-sm">
                  暂无运行中的任务
                </div>
              ) : (
                <div className="divide-y divide-gray-100">
                  {tasks.map(task => (
                    <button
                      key={task.id}
                      onClick={() => handleSelectTask(task)}
                      className={`w-full p-4 text-left hover:bg-gray-50 transition-colors ${
                        selectedTask?.id === task.id ? 'bg-blue-50' : ''
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-medium text-gray-900 truncate">{task.name}</span>
                        <span className={`px-2 py-0.5 text-xs rounded ${
                          task.status === 'RUNNING' ? 'bg-green-100 text-green-700' :
                          task.status === 'QUEUED' ? 'bg-yellow-100 text-yellow-700' :
                          'bg-gray-100 text-gray-700'
                        }`}>
                          {task.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 text-xs text-gray-400">
                        <span>{task.type}</span>
                        {task.is_distributed && (
                          <span className="flex items-center gap-1">
                            <Server className="w-3 h-3" />
                            分布式
                          </span>
                        )}
                      </div>
                      {task.group_id && (
                        <div className="text-xs text-gray-400 mt-1">
                          Group: {task.group_id}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </CleanCard>
        </div>

        {/* 中间：运行记录和进度 */}
        <div className="w-64 flex-shrink-0 flex flex-col">
          {selectedTask ? (
            <CleanCard className="flex-1 flex flex-col overflow-hidden">
              <div className="p-4 border-b border-gray-100">
                <h3 className="font-medium text-gray-900">执行节点</h3>
                <div className="mt-2">
                  <div className="flex items-center justify-between text-sm mb-1">
                    <span className="text-gray-500">整体进度</span>
                    <span className="font-medium">{overallProgress()}%</span>
                  </div>
                  <div className="w-full bg-gray-100 rounded-full h-2">
                    <div
                      className="bg-blue-600 h-2 rounded-full transition-all"
                      style={{ width: `${overallProgress()}%` }}
                    />
                  </div>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                <div className="divide-y divide-gray-100">
                  {taskRuns.map(run => (
                    <div key={run.id} className="p-3">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-1">
                          <Smartphone className="w-4 h-4 text-gray-400" />
                          <span className="text-sm font-medium text-gray-900">
                            节点 {run.id}
                          </span>
                        </div>
                        <span className={`px-2 py-0.5 text-xs rounded ${
                          run.status === 'RUNNING' ? 'bg-green-100 text-green-700' :
                          run.status === 'FINISHED' ? 'bg-blue-100 text-blue-700' :
                          run.status === 'FAILED' ? 'bg-red-100 text-red-700' :
                          'bg-gray-100 text-gray-700'
                        }`}>
                          {run.status}
                        </span>
                      </div>
                      <div className="mt-2">
                        <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
                          <span>{run.progress_message || '执行中'}</span>
                          <span>{run.progress || 0}%</span>
                        </div>
                        <div className="w-full bg-gray-100 rounded-full h-1.5">
                          <div
                            className={`h-1.5 rounded-full ${
                              run.status === 'FAILED' ? 'bg-red-500' :
                              run.status === 'FINISHED' ? 'bg-green-500' :
                              'bg-blue-500'
                            }`}
                            style={{ width: `${run.progress || 0}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </CleanCard>
          ) : (
            <CleanCard className="flex items-center justify-center text-gray-400">
              选择任务查看日志
            </CleanCard>
          )}
        </div>

        {/* 右侧：日志窗口 */}
        <div className="flex-1 flex flex-col min-w-0">
          <CleanCard className="flex-1 flex flex-col overflow-hidden">
            <div className="p-3 border-b border-gray-100 flex items-center gap-3">
              <div className="flex-1 relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  placeholder="搜索日志..."
                  value={filter}
                  onChange={e => setFilter(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <select
                value={levelFilter}
                onChange={e => setLevelFilter(e.target.value)}
                className="px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              >
                <option value="all">全部</option>
                <option value="INFO">INFO</option>
                <option value="WARN">WARN</option>
                <option value="ERROR">ERROR</option>
              </select>
              <button
                onClick={() => setAutoScroll(!autoScroll)}
                className={`p-2 rounded-lg ${autoScroll ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-600'}`}
                title={autoScroll ? '自动滚动已开启' : '自动滚动已关闭'}
              >
                {autoScroll ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
              </button>
              <button
                onClick={clearLogs}
                className="p-2 rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200"
                title="清空日志"
              >
                <X className="w-4 h-4" />
              </button>
              <button
                onClick={downloadLogs}
                disabled={logs.length === 0}
                className="p-2 rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 disabled:opacity-50"
                title="下载日志"
              >
                <Download className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto bg-gray-900 p-3 font-mono text-xs">
              {logs.length === 0 ? (
                <div className="text-gray-500 text-center py-8">
                  {selectedTask ? '等待日志...' : '请选择任务'}
                </div>
              ) : (
                <>
                  {filteredLogs.map((log, index) => (
                    <div
                      key={index}
                      className={`py-0.5 ${
                        log.level === 'ERROR' ? 'text-red-400' :
                        log.level === 'WARN' ? 'text-yellow-400' :
                        'text-gray-300'
                      }`}
                    >
                      <span className="text-gray-500">[{log.timestamp}]</span>{' '}
                      <span className="text-blue-400">[{log.device}]</span>{' '}
                      <span className={`${
                        log.level === 'ERROR' ? 'text-red-400' :
                        log.level === 'WARN' ? 'text-yellow-400' :
                        'text-green-400'
                      }`}>[{log.level}]</span>{' '}
                      {log.message}
                    </div>
                  ))}
                  <div ref={logsEndRef} />
                </>
              )}
            </div>

            <div className="p-2 border-t border-gray-100 flex items-center justify-between text-xs text-gray-500 bg-gray-50">
              <span>共 {filteredLogs.length} 条日志</span>
              <span>
                {isConnected ? (
                  <span className="flex items-center gap-1 text-green-600">
                    <span className="w-2 h-2 bg-green-500 rounded-full"></span>
                    已连接
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-gray-400">
                    <span className="w-2 h-2 bg-gray-400 rounded-full"></span>
                    未连接
                  </span>
                )}
              </span>
            </div>
          </CleanCard>
        </div>
      </div>
    </div>
  );
}
