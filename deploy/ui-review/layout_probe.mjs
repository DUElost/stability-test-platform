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

const credentials = JSON.parse(fs.readFileSync(credsPath, 'utf8'));

async function inspectPage(page, routePath, waitText) {
  await page.goto(`${baseUrl}${routePath}`, { waitUntil: 'networkidle' });
  await page.getByText(waitText).first().waitFor({ timeout: 10000 });
  await page.evaluate(() => document.fonts.ready);

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

    return {
      route: currentRoute,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      docOverflow,
      tableOverflows,
      iconIssuesCount: iconIssues.length,
      iconIssuesSample: iconIssues.slice(0, 5),
      badgeIssuesCount: badgeIssues.length,
      badgeIssuesSample: badgeIssues.slice(0, 5),
    };
  }, routePath);
}

async function main() {
  const browser = await chromium.launch({
    executablePath: chromiumPath,
    headless: true,
    args: ['--no-sandbox'],
  });

  try {
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);

    await page.goto(`${baseUrl}/devices`);
    await page.getByLabel('用户名').fill('ui-review-admin');
    await page.getByLabel('密码', { exact: true }).fill(credentials.admin);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByRole('heading', { name: '物理设备', exact: true }).waitFor();

    const devicesReport = await inspectPage(page, '/devices', 'USB 开机');
    const hostsReport = await inspectPage(page, '/hosts', '主机集群');

    const report = { baseUrl, checks: [devicesReport, hostsReport] };
    console.log(JSON.stringify(report, null, 2));

    const hasFailures = report.checks.some(
      (c) =>
        c.docOverflow > 1 ||
        c.tableOverflows.length > 0 ||
        c.iconIssuesCount > 0 ||
        c.badgeIssuesCount > 0,
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
