import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const playwrightPath =
  process.env.STP_PLAYWRIGHT_MODULE ||
  '/home/debian13/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core';
const chromiumPath =
  process.env.STP_CHROMIUM_PATH ||
  '/home/debian13/.cache/ms-playwright/chromium-1237/chrome-linux64/chrome';

const { chromium } = require(playwrightPath);

const baseUrl = (process.env.STP_UI_REVIEW_URL || 'http://stp-ui-review.localhost:15179').replace(/\/$/, '');
const credsPath =
  process.env.STP_UI_REVIEW_CREDS ||
  path.join(os.homedir(), '.local/state/stp-ui-review/stack/credentials.json');
const screenshotDir = process.env.STP_UI_REVIEW_SCREENSHOT_DIR || '';

const credentials = JSON.parse(fs.readFileSync(credsPath, 'utf8'));

const SITE_ROUTES = [
  { path: '/', waitText: '仪表盘' },
  { path: '/projects', waitText: '项目登记簿' },
  { path: '/projects/UI-REVIEW-LONG-PROJECT-2026', waitText: 'UI-Review' },
  { path: '/orchestration/plans', waitText: 'Plan 编排' },
  { path: '/execution/plan-execute', waitText: '执行测试计划' },
  { path: '/execution/plan-runs', waitText: 'Plan 执行记录' },
  { path: '/execution/plan-runs/1', waitText: '#1' },
  { path: '/results', waitText: '测试结果' },
  { path: '/issue-tracker', waitText: '问题追踪' },
  { path: '/hosts', waitText: '主机集群' },
  { path: '/devices', waitText: 'USB 开机' },
  { path: '/script-management', waitText: '脚本库' },
  { path: '/test-suites', waitText: '用例套件' },
  { path: '/storage', waitText: '文件服务器' },
  { path: '/wifi', waitText: 'WiFi 资源池' },
  { path: '/schedules', waitText: '定时任务' },
];

async function inspectPage(page, routePath, waitText, vpLabel) {
  await page.goto(`${baseUrl}${routePath}`, { waitUntil: 'networkidle' });
  await page.getByText(waitText).first().waitFor({ timeout: 10000 });
  await page.evaluate(() => document.fonts.ready);

  if (screenshotDir) {
    fs.mkdirSync(screenshotDir, { recursive: true });
    const safeName = routePath === '/' ? 'dashboard' : routePath.slice(1).replace(/\//g, '_');
    await page.screenshot({ path: path.join(screenshotDir, `${vpLabel}_${safeName}.png`) });
  }

  return page.evaluate((currentRoute) => {
    const docOverflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;

    const tableOverflows = [];
    for (const table of document.querySelectorAll('table')) {
      const container = table.parentElement;
      if (!container) continue;
      const diff = container.scrollWidth - container.clientWidth;
      if (diff > 1) {
        tableOverflows.push({
          scrollWidth: container.scrollWidth,
          clientWidth: container.clientWidth,
          overflowPx: diff,
          columnCount: table.querySelectorAll('thead th').length,
        });
      }
    }

    const iconIssues = [];
    const badgeIssues = [];
    for (const badge of document.querySelectorAll('[data-kind="device-ui"]')) {
      const badgeRect = badge.getBoundingClientRect();
      if (badgeRect.width === 0 || badgeRect.height === 0) continue;

      if (badge.scrollWidth > badge.clientWidth + 1 || badgeRect.height > 30) {
        badgeIssues.push({
          status: badge.getAttribute('data-status'),
          width: Math.round(badgeRect.width * 10) / 10,
          height: Math.round(badgeRect.height * 10) / 10,
          scrollWidth: badge.scrollWidth,
          clientWidth: badge.clientWidth,
        });
      }

      for (const svg of badge.querySelectorAll('svg')) {
        const rect = svg.getBoundingClientRect();
        if (Math.abs(rect.width - rect.height) > 1.0 || rect.width < 9) {
          iconIssues.push({
            status: badge.getAttribute('data-status'),
            width: Math.round(rect.width * 100) / 100,
            height: Math.round(rect.height * 100) / 100,
          });
        }
      }
    }

    const wrappedButtons = [];
    for (const btn of document.querySelectorAll('button, [role="tab"]')) {
      const rect = btn.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const text = (btn.innerText || '').trim();
      if (!text || text.length > 20) continue;
      if (rect.width < 180 && rect.height > 44 && !btn.closest('.recharts-wrapper')) {
        wrappedButtons.push({
          text: text.replace(/\s+/g, ' '),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
      }
    }

    return {
      route: currentRoute,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      docOverflow,
      tableOverflows,
      iconIssuesCount: iconIssues.length,
      iconIssuesSample: iconIssues.slice(0, 5),
      badgeIssuesCount: badgeIssues.length,
      badgeIssuesSample: badgeIssues.slice(0, 5),
      wrappedButtonsCount: wrappedButtons.length,
      wrappedButtonsSample: wrappedButtons.slice(0, 5),
    };
  }, routePath);
}

async function main() {
  const browser = await chromium.launch({
    executablePath: chromiumPath,
    headless: true,
    args: ['--no-sandbox', '--no-proxy-server'],
  });

  try {
    const viewports = [
      { width: 1920, height: 1080 },
      { width: 1440, height: 900 },
    ];
    const checks = [];

    const context = await browser.newContext({ viewport: viewports[0] });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);

    await page.goto(`${baseUrl}/devices`);
    await page.getByLabel('用户名').fill('ui-review-admin');
    await page.getByLabel('密码', { exact: true }).fill(credentials.admin);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByRole('heading', { name: '物理设备', exact: true }).waitFor();

    for (const vp of viewports) {
      await page.setViewportSize(vp);
      const vpLabel = `${vp.width}x${vp.height}`;
      for (const route of SITE_ROUTES) {
        checks.push(await inspectPage(page, route.path, route.waitText, vpLabel));
      }
    }
    await context.close();

    const report = { baseUrl, checks };
    console.log(JSON.stringify(report, null, 2));

    // Hard gate on both 1920x1080 and 1440x900 desktop layout invariants
    const hasFailures = report.checks.some(
      (c) =>
        c.docOverflow > 1 ||
        c.tableOverflows.length > 0 ||
        c.iconIssuesCount > 0 ||
        c.badgeIssuesCount > 0 ||
        c.wrappedButtonsCount > 0,
    );
    if (hasFailures) {
      process.exitCode = 1;
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
