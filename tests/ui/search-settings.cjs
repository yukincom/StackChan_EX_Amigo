// Run with the existing Playwright runtime; no server, secrets or external I/O.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(process.argv[2] || 'server');
  const browser = await chromium.launch({headless: true,
    ...(process.env.TEST_CHROME_PATH ? {executablePath: process.env.TEST_CHROME_PATH} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1000, height: 900}});
    await page.route('**/*', route => route.abort());
    const html = fs.readFileSync(path.join(root, 'templates/admin/index.html'), 'utf8');
    await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
    for (const file of ['utils.js', 'ai.js']) {
      await page.addScriptTag({content: fs.readFileSync(path.join(root, 'static/admin/js', file), 'utf8')});
    }
    await page.evaluate(() => {
      const field = (key, value, type = 'text') => ({key, value, type, label: key});
      const groups = [{group: 'AI', items: [
        {...field('AI_SEARCH_PROVIDER', 'gemini', 'select'), options: ['gemini:Gemini', 'firecrawl:Firecrawl']},
        field('AI_CHAT_MODEL', 'conversation-model'), field('AI_SEARCH_MODEL', 'gemini-test'),
        field('AI_SEARCH_TEMPERATURE', '0.3'), field('AI_SEARCH_RECENT_TURNS', '1'),
        field('FIRECRAWL_API_KEY', '', 'password'), field('SEARCH_KEYWORDS', '調べて'),
        field('GEMINI_API_KEY', '', 'password')]}];
      document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'block');
      const container = document.getElementById('ai-content');
      for (let el = container; el; el = el.parentElement) el.style.display = 'block';
      renderEnvGroups(groups, 'ai-content');
      _setupSearchSettings();
    });
    const display = key => page.locator(`[data-key="${key}"]`).evaluate(el => el.closest('.field').style.display);
    assert.equal(await display('FIRECRAWL_API_KEY'), 'none');
    await page.locator('[data-key="AI_SEARCH_PROVIDER"]').selectOption('firecrawl');
    assert.match(await page.locator('#search-provider-status').innerText(), /Firecrawl/);
    assert.match(await page.locator('#search-provider-help').innerText(), /conversation-model/);
    assert.match(await page.locator('#search-save-status').innerText(), /未保存/);
    assert.equal(await display('AI_SEARCH_MODEL'), 'none');
    assert.equal(await display('FIRECRAWL_API_KEY'), '');
    await page.locator('[data-key="FIRECRAWL_API_KEY"]').fill('fc-fixture');
    await page.evaluate(async () => {
      window.adminFetch = async () => ({json: async () => ({ok: true})});
      window.toast = () => {};
      await saveAiEnv();
    });
    assert.match(await page.locator('#search-save-status').innerText(), /保存済み.*再起動/);
    await page.locator('[data-key="AI_SEARCH_PROVIDER"]').selectOption('gemini');
    assert.equal(await display('AI_SEARCH_MODEL'), '');
    assert.equal(await page.locator('[data-key="AI_SEARCH_MODEL"]').inputValue(), 'gemini-test');
    assert.equal(await page.locator('[data-key="FIRECRAWL_API_KEY"]').inputValue(), 'fc-fixture');
    assert.match(await page.locator('#search-provider-status').innerText(), /Gemini/);
    console.log('Search settings browser checks passed:', root);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
