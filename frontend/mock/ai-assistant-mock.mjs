/**
 * AI 助手前端开发用 mock 中间件（vite 插件）。
 *
 * 用途：后端 `/api/v1/ai-assistant/*`（ADR-0031 阶段二）尚未实现期间，
 * 以 `STP_AI_UI_MOCK=1 npm run dev` 启动时拦截这组路径返回演示数据，
 * 让助手页/设置页可以独立开发与视觉验收。其余路径照常走 proxy 到控制面。
 * 后端落地后直接删掉本文件与 vite.config.ts 中的挂载行即可。
 */

const now = '2026-08-28T10:00:00Z';

const CONFIG = {
  base_url: 'https://open.bigmodel.cn/api/paas/v4',
  model: 'glm-4-plus',
  api_key_masked: 'sk-***8f2a',
  enabled: true,
  temperature: 0.2,
  max_turns: 8,
  request_timeout_seconds: 120,
  t1_require_confirm: false,
  auto_approve_tools: [],
  updated_at: now,
};

const SESSIONS = [
  { id: 7, title: '平台巡检与门禁', created_at: now, updated_at: now },
  { id: 6, title: 'node-14 磁盘水位排查', created_at: '2026-08-27T09:00:00Z', updated_at: '2026-08-27T09:30:00Z' },
];

const MESSAGES = [
  { id: 101, session_id: 7, role: 'user', content: '平台现在整体健康吗？最近有没有需要关注的告警？', tool_calls: [], tool_call_id: null, status: 'completed', meta: {}, created_at: now },
  {
    id: 102, session_id: 7, role: 'assistant', tool_calls: [], tool_call_id: null, status: 'completed', meta: { usage: { prompt_tokens: 812, completion_tokens: 214 }, latency_ms: 1830 },
    content: [
      '控制面整体**健康**，当前无 S/A 级告警：',
      '',
      '- 数据库连接正常，SAQ 队列就绪（10 并发，无积压）',
      '- 20 台 Agent host：18 台 ONLINE，1 台 **DEGRADED**（node-14，磁盘水位 82%），1 台 OFFLINE（计划内维护）',
      '- 过去 24h：3 轮去重扫描全部成功，最近一轮 merge 报表已归档',
    ].join('\n'),
    created_at: now,
  },
  { id: 103, session_id: 7, role: 'user', content: '好。给「运维群」发条测试通知确认通道还通着，顺便把 agent 测试套件也跑一遍', tool_calls: [], tool_call_id: null, status: 'completed', meta: {}, created_at: now },
  {
    id: 104, session_id: 7, role: 'assistant', tool_calls: [], tool_call_id: null, status: 'completed',
    meta: { usage: { prompt_tokens: 1204, completion_tokens: 96 }, latency_ms: 1410, proposed_action_id: 12 },
    content: '通知通道测试属于 **T2 运维动作**，需要你审批后才会执行：',
    created_at: now,
  },
  {
    id: 105, session_id: 7, role: 'assistant', tool_calls: [{ id: 'call_2', name: 'run_agent_tests', arguments: {} }], tool_call_id: null, status: 'completed',
    meta: { usage: { prompt_tokens: 1588, completion_tokens: 132 }, latency_ms: 1980, proposed_action_id: 9 },
    content: 'agent 测试套件属 **T1 非破坏性**操作，已自动开始执行，日志在下方卡片实时可见：',
    created_at: now,
  },
];

const ACTION_PROPOSED = {
  id: 12,
  session_id: 7,
  tool_name: 'test_notification_channel',
  params: { channel_id: 2, channel_name: '运维群' },
  status: 'proposed',
  console_run_id: null,
  result_summary: null,
  requested_by: 'stp-admin',
  decided_by: null,
  created_at: now,
  decided_at: null,
};

const ACTION_RUNNING = {
  id: 9,
  session_id: 7,
  tool_name: 'run_agent_tests',
  params: {},
  status: 'running',
  console_run_id: 'run-20260828-ai-009',
  result_summary: null,
  requested_by: 'stp-admin',
  decided_by: 'stp-admin',
  created_at: '2026-08-28T09:58:00Z',
  decided_at: '2026-08-28T09:58:05Z',
};

const LOG_LINES = [
  { seq: 1, ts: '2026-08-28T09:58:06Z', stream: 'stdout', line: '============================= test session starts ==============================' },
  { seq: 2, ts: '2026-08-28T09:58:06Z', stream: 'stdout', line: 'platform linux -- Python 3.11.9, pytest-8.4.2, pluggy-1.5.0' },
  { seq: 3, ts: '2026-08-28T09:58:06Z', stream: 'stdout', line: 'rootdir: /home/debian13/stability-test-platform' },
  { seq: 4, ts: '2026-08-28T09:58:07Z', stream: 'stdout', line: 'collected 612 items' },
  { seq: 5, ts: '2026-08-28T09:58:09Z', stream: 'stdout', line: '' },
  { seq: 6, ts: '2026-08-28T09:58:09Z', stream: 'stdout', line: 'backend/agent/tests/test_agent_secret_guards.py ......                        [  1%]' },
  { seq: 7, ts: '2026-08-28T09:58:11Z', stream: 'stderr', line: 'WARNING: STP_AEE_LOCAL_ROOT not set in test env — using tmp_path fixture' },
  { seq: 8, ts: '2026-08-28T09:58:14Z', stream: 'stdout', line: 'backend/agent/tests/test_scan_runner.py ..........................            [  5%]' },
  { seq: 9, ts: '2026-08-28T09:58:19Z', stream: 'stdout', line: 'backend/agent/tests/test_upload_manager.py .....................               [  8%]' },
  { seq: 10, ts: '2026-08-28T09:58:24Z', stream: 'stdout', line: 'backend/agent/tests/test_event_uploader.py ...........................         [ 12%]' },
];

const ok = (data) => ({
  status: 200,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ data, error: null }),
});

/** @returns {import('vite').Plugin} */
export function aiAssistantMockPlugin() {
  return {
    name: 'stp-ai-assistant-mock',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0];
        if (!url.startsWith('/api/v1/ai-assistant/')) return next();
        const method = (req.method ?? 'GET').toUpperCase();
        const respond = (data) => {
          res.statusCode = 200;
          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify({ data, error: null }));
        };

        if (url === '/api/v1/ai-assistant/config' && method === 'GET') return respond(CONFIG);
        if (url === '/api/v1/ai-assistant/config' && method === 'PUT') return respond(CONFIG);
        if (url === '/api/v1/ai-assistant/config/test-connection' && method === 'POST') {
          return respond({ ok: true, latency_ms: 236, model: CONFIG.model, error: null });
        }
        if (url === '/api/v1/ai-assistant/sessions' && method === 'GET') return respond(SESSIONS);
        if (url === '/api/v1/ai-assistant/sessions' && method === 'POST') {
          return respond({ id: Date.now(), title: '未命名会话', created_at: now, updated_at: now });
        }
        if (/^\/api\/v1\/ai-assistant\/sessions\/\d+$/.test(url) && method === 'DELETE') return respond(null);
        if (/^\/api\/v1\/ai-assistant\/sessions\/\d+\/messages$/.test(url) && method === 'GET') return respond(MESSAGES);
        if (/^\/api\/v1\/ai-assistant\/sessions\/\d+\/messages$/.test(url) && method === 'POST') {
          return respond({ id: Date.now(), session_id: 7, role: 'assistant', content: '', tool_calls: [], tool_call_id: null, status: 'pending', meta: {}, created_at: now });
        }
        if (/^\/api\/v1\/ai-assistant\/actions\/\d+\/log$/.test(url)) return respond(LOG_LINES);
        if (/^\/api\/v1\/ai-assistant\/actions\/9$/.test(url)) return respond(ACTION_RUNNING);
        if (/^\/api\/v1\/ai-assistant\/actions\/\d+$/.test(url)) return respond(ACTION_PROPOSED);
        if (/^\/api\/v1\/ai-assistant\/actions\/\d+\/(approve|reject|cancel)$/.test(url)) {
          return respond({ ...ACTION_PROPOSED, status: 'running', decided_by: 'stp-admin' });
        }
        respond(null);
      });
    },
  };
}
