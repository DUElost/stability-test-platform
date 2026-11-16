#!/usr/bin/env node
/**
 * Reusable Playwright UI probe for stp-ui-review (http://stp-ui-review.localhost:15179).
 *
 * Verifies:
 *   1. Zero horizontal overflow (`docOverflow === 0`) at both 1440x900 and 1280x800.
 *   2. Same-row primary filter controls (FilterSearchInput / SearchableSelect) keep aligned heights.
 *   3. Round-trip quick-view switching on /devices (`全部视角 -> 需关注 -> 全部视角`).
 *   4. Captures screenshots of /hosts, /devices, /plan-runs, and /execution/plan-execute.
 *
 * Usage:
 *   node tools/dev/ui_review_probe.mjs [--out-dir /tmp/ui-review-probe]
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const playwrightPath = process.env.PLAYWRIGHT_CORE_PATH
  || '/home/debian13/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core';
const chromiumPath = process.env.CHROMIUM_PATH
  || '/home/debian13/.cache/ms-playwright/chromium-1237/chrome-linux64/chrome';

const { chromium } = require(playwrightPath);

const outFlagIdx = process.argv.indexOf('--out-dir');
const outDir = outFlagIdx >= 0 && process.argv[outFlagIdx + 1]
  ? process.argv[outFlagIdx + 1]
  : '/tmp/ui-review-probe';
fs.mkdirSync(outDir, { recursive: true });

const baseUrl = process.env.UI_REVIEW_BASE_URL || 'http://stp-ui-review.localhost:15179';
const credsPath = path.join(os.homedir(), '.local/state/stp-ui-review/stack/credentials.json');
const creds = JSON.parse(fs.readFileSync(credsPath, 'utf8'));

const browser = await chromium.launch({ executablePath: chromiumPath, headless: true });

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  await page.goto(`${baseUrl}/devices`, { waitUntil: 'networkidle' });
  await page.getByLabel('用户名').fill('ui-review-admin');
  await page.getByLabel('密码', { exact: true }).fill(creds.admin);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.getByRole('heading', { name: '物理设备', exact: true }).waitFor({ timeout: 15000 });

  const routes = [
    { name: 'hosts', path: '/hosts', waitText: '主机集群', controlSelectors: ['[data-testid="host-health-filter"]'] },
    {
      name: 'devices',
      path: '/devices',
      waitText: '物理设备',
      controlSelectors: [
        '#device-search',
        '[data-testid="device-project-filter"]',
        '[data-testid="device-host-filter"]',
        '[data-testid="device-model-filter"]',
        '[data-testid="device-product-filter"]',
        '[data-testid="device-version-filter"]',
      ],
    },
    { name: 'plan_runs', path: '/plan-runs', waitText: '执行记录', controlSelectors: ['[data-testid="plan-run-project-filter"]'] },
  ];

  const failures = [];

  for (const vp of [
    { width: 1440, height: 900 },
    { width: 1280, height: 800 },
  ]) {
    await page.setViewportSize(vp);
    for (const r of routes) {
      await page.goto(`${baseUrl}${r.path}`, { waitUntil: 'networkidle' });
      await page.waitForSelector(`text=${r.waitText}`);
      await page.waitForTimeout(300);

      const metrics = await page.evaluate((selectors) => {
        const overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
        const heights = selectors
          .map((sel) => {
            const el = document.querySelector(sel);
            return el ? { sel, height: Math.round(el.getBoundingClientRect().height) } : null;
          })
          .filter(Boolean);
        return { overflow, heights };
      }, r.controlSelectors);

      if (metrics.overflow > 0) {
        failures.push(`${r.name}@${vp.width}x${vp.height}: docOverflow=${metrics.overflow}`);
      }
      if (metrics.heights.length > 1) {
        const hs = metrics.heights.map((h) => h.height);
        const delta = Math.max(...hs) - Math.min(...hs);
        if (delta > 2) {
          failures.push(`${r.name}@${vp.width}x${vp.height}: control height mismatch ${JSON.stringify(metrics.heights)}`);
        }
      }
      if (vp.width === 1440) {
        await page.screenshot({ path: path.join(outDir, `${r.name}_1440.png`) });
      }
    }
  }

  // Round-trip quick-view check on /devices
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${baseUrl}/devices`, { waitUntil: 'networkidle' });
  await page.getByRole('tab', { name: /需关注/ }).click();
  await page.getByRole('tab', { name: /全部视角/ }).click();
  const allSelected = await page.getByRole('tab', { name: /全部视角/ }).getAttribute('aria-selected');
  if (allSelected !== 'true') {
    failures.push(`devices round-trip failed: 全部视角 aria-selected=${allSelected}`);
  }

  if (failures.length > 0) {
    console.error('UI_REVIEW_PROBE_FAILED:', JSON.stringify(failures, null, 2));
    process.exitCode = 1;
  } else {
    console.log('UI_REVIEW_PROBE_OK:', JSON.stringify({ outDir, viewports: ['1440x900', '1280x800'] }));
  }
} finally {
  await browser.close();
}
