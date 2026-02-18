import { useState, useEffect } from 'react';
import { api } from '@/utils/api';
import { Loader2, Shield } from 'lucide-react';

interface AuditLogEntry {
  id: number;
  user_id?: number;
  username?: string;
  action: string;
  resource_type: string;
  resource_id?: number;
  details?: Record<string, any>;
  ip_address?: string;
  timestamp: string;
}

export default function AuditLogPage() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const pageSize = 50;

  const [filters, setFilters] = useState({
    resource_type: '',
    action: '',
  });

  const loadLogs = async () => {
    setLoading(true);
    try {
      const params: any = {};
      if (filters.resource_type) params.resource_type = filters.resource_type;
      if (filters.action) params.action = filters.action;
      const res = await api.audit.list(page * pageSize, pageSize, params);
      setLogs(res.data.items);
      setTotal(res.data.total);
    } catch {
      // silently fail for non-admin users
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadLogs(); }, [page, filters]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">操作日志</h2>
          <p className="text-sm text-gray-400">查看系统操作审计记录（仅管理员）</p>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <select
          value={filters.resource_type}
          onChange={(e) => { setFilters({ ...filters, resource_type: e.target.value }); setPage(0); }}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm"
        >
          <option value="">全部资源</option>
          <option value="workflow">工作流</option>
          <option value="tool">工具</option>
          <option value="notification_channel">通知渠道</option>
          <option value="notification_rule">告警规则</option>
          <option value="schedule">定时任务</option>
          <option value="template">任务模板</option>
        </select>
        <select
          value={filters.action}
          onChange={(e) => { setFilters({ ...filters, action: e.target.value }); setPage(0); }}
          className="px-3 py-2 border border-gray-200 rounded-lg text-sm"
        >
          <option value="">全部操作</option>
          <option value="create">创建</option>
          <option value="update">更新</option>
          <option value="delete">删除</option>
          <option value="start">启动</option>
          <option value="cancel">取消</option>
        </select>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      ) : logs.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <Shield className="w-12 h-12 mx-auto mb-3 text-gray-300" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">暂无审计记录</h3>
          <p className="text-sm text-gray-400">操作日志将在此处记录</p>
        </div>
      ) : (
        <>
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50/50">
                  <th className="text-left px-4 py-3 font-medium text-gray-500">时间</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">用户</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">操作</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">资源</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-500">IP</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="border-b border-gray-50 hover:bg-gray-50/50">
                    <td className="px-4 py-3 text-xs text-gray-500">
                      {new Date(log.timestamp).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-gray-700">{log.username || '-'}</td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700">
                        {log.action}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-600">
                      {log.resource_type}{log.resource_id ? ` #${log.resource_id}` : ''}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400 font-mono">{log.ip_address || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-gray-500">
            <span>共 {total} 条记录</span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="px-3 py-1 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
              >
                上一页
              </button>
              <span className="px-3 py-1">第 {page + 1} 页</span>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={(page + 1) * pageSize >= total}
                className="px-3 py-1 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
              >
                下一页
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
