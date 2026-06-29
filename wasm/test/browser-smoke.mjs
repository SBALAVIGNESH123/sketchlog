import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.goto('http://127.0.0.1:8765/test/browser.html');
  const result = await page.evaluate(() => window.smoke);
  assert.equal(result.count, 3);
  assert.equal(result.serializedTotal, '3');
  assert.ok(result.p99 > 29 && result.p99 < 31);
  console.log('WASM browser smoke test passed');
} finally {
  await browser.close();
}
