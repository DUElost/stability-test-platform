/**
 * 路由级标题表（#2363）。
 *
 * 失效形态：标题靠「每个页面自己调 `useDocumentTitle('xxx')`」——漏调的那页，
 * 浏览器标签就停留在默认「稳定性测试平台」（`/hosts`、`/issue-tracker`、
 * `/execution/plan-execute` 实测如此），且新增页面还会再漏。这里把**默认标题**
 * 收敛成一份路由表，由 `<RouteTitle/>` 每次路由变化登记一次：
 *
 * - 侧栏里有的路由，名字**直接取自 `Sidebar` 的同一份 `navGroups`**——标签与
 *   用户在导航里看到的词必须同源，否则改一处就漂移；
 * - 其余路由（详情、账户、管理员菜单项、登录/注册）在本表登记一份；
 * - 未命中的路径返回 `null` → 不登记，留给页面自己的 claim（如 404 页）。
 */
import { navGroups } from '@/layouts/navItems';

/** 非导航路由：这些地方不在侧栏/管理员菜单的名字来源里，只能在此登记一份。 */
const EXTRA_TITLES: ReadonlyArray<readonly [string, string]> = [
  ['/runs/:runId/report', 'Run 报告'],
  ['/execution/plan-runs/:runId/logs', 'Run 日志'],
  ['/execution/plan-runs/:runId', 'Plan Run 详情'],
  ['/orchestration/plans/:id', 'Plan 编辑'],
  ['/test-suites/:suiteId', '套件详情'],
  ['/projects/:projectKey', '项目详情'],
  ['/assistant/approvals', 'AI 助手审批'],
  ['/assistant', 'AI 助手'],
  ['/settings/ai-assistant', 'AI 助手设置'],
  ['/account/password', '修改密码'],
  ['/users', '用户管理'],
  ['/notifications', '通知管理'],
  ['/audit', '审计日志'],
  ['/settings', '系统设置'],
  ['/login', '登录'],
  ['/register', '注册'],
];

function segmentCount(pattern: string): number {
  return pattern.split('/').filter(Boolean).length;
}

/** `:param` 段匹配任意一段；段数必须相等（不做前缀吞并）。 */
function patternMatches(pattern: string, pathname: string): boolean {
  const want = pattern.split('/').filter(Boolean);
  const have = pathname.split('/').filter(Boolean);
  if (want.length !== have.length) return false;
  return want.every((seg, index) => seg.startsWith(':') || seg === have[index]);
}

const TITLE_TABLE: ReadonlyArray<readonly [string, string]> = [
  ...navGroups.flatMap((group) =>
    group.items.map((item) => [item.path, item.label] as [string, string]),
  ),
  ...EXTRA_TITLES,
].sort((a, b) => segmentCount(b[0]) - segmentCount(a[0]));
// 排序稳定（ES2020 起语言保证）→ 同深度时导航条目在前即先命中，
// 因此 `/hosts` 永远取侧栏名字，不会被 EXTRA 里的同路径条目抢走。

/** 当前路径的标题；`null` = 本表未覆盖（页面可自行 claim，或维持上一标题）。 */
export function resolveRouteTitle(pathname: string): string | null {
  for (const [pattern, title] of TITLE_TABLE) {
    if (patternMatches(pattern, pathname)) return title;
  }
  return null;
}
