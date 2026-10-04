import { expect, test, type Locator, type Page } from '@playwright/test'

const metrics = {
  jev_threshold: 0.6, enabled_checks: {},
  totals: { requests: 0, allowed: 0, redacted: 0, blocked: 0, flagged: 0 },
  timeline: [], blocks_by_check: {}, latency_ms_by_check: {},
  overhead_ms: { p50: 0, p95: 0 }, budget_by_caller: {}, tokens_total: 0, cost_total: 0,
  request_latency_ms: Object.fromEntries(
    ['pre_checks', 'upstream', 'post_checks', 'total', 'ttft'].map((k) => [k, { p50: 0, p95: 0 }]),
  ),
}

test.beforeEach(async ({ page }) => {
  await page.route('http://localhost:8000/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    await route.fulfill({ json: path === '/api/metrics' ? metrics : path === '/api/health'
      ? { status: 'ok', policy_version: 'test', jev: 'down', fallback: 'down' }
      : { total: 0, items: [] } })
  })
})

function windowMinutes(url: string) {
  const since = new URL(url).searchParams.get('since')
  expect(since).not.toBeNull()
  return Math.round((Date.now() - Date.parse(since!)) / 60_000)
}

test('presets and custom window apply to Overview and Audit without the old toolbar', async ({ page }) => {
  await page.goto('/')
  for (const [label, minutes] of [['15m', 15], ['1h', 60], ['24h', 1440], ['7d', 10080]] as const) {
    const changed = page.waitForRequest((r) => r.url().includes('/api/metrics?') && windowMinutes(r.url()) === minutes)
    const button = page.getByRole('button', { name: label, exact: true })
    await button.click()
    await changed
    await expect(button).toHaveAttribute('aria-pressed', 'true')
    await expect(button).toHaveCSS('background-color', 'rgb(224, 123, 57)')
  }
  await page.getByLabel('Custom time window').fill('45m')
  const custom = page.waitForRequest((r) => r.url().includes('/api/metrics?') && windowMinutes(r.url()) === 45)
  await page.getByRole('button', { name: 'Apply', exact: true }).click()
  await custom
  const audit = page.waitForRequest((r) => r.url().includes('/api/audit?') && windowMinutes(r.url()) === 45)
  await page.locator('nav a[href="#audit"]').click()
  await audit
  await expect(page.getByRole('combobox')).toHaveCount(0)
  await expect(page.getByRole('checkbox')).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Export CSV', exact: true })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Export JSON', exact: true })).toHaveCount(0)
  await expect(page.getByText('caller=', { exact: true })).toHaveCount(0)
  await expect(page.getByText('check=', { exact: true })).toHaveCount(0)
  await expect(page.locator('nav a')).toHaveText(['Overview', 'Live', 'Audit', 'Policy', 'Playground'])
  await page.locator('nav a[href="#overview"]').click()
  await expect(page.getByText('Last 45m', { exact: true })).toBeVisible()
})

test('invalid custom windows preserve the applied selection', async ({ page }) => {
  await page.goto('/')
  for (const invalid of ['', '0m', '-2h', '2', '1.5h', '999999999999999999d']) {
    await page.getByLabel('Custom time window').fill(invalid)
    await page.getByRole('button', { name: 'Apply', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('positive whole number')
    await expect(page.getByRole('button', { name: '24h', exact: true })).toHaveAttribute('aria-pressed', 'true')
  }
})

test('late old-window response cannot overwrite the new metrics', async ({ page }) => {
  let release = () => {}
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route('http://localhost:8000/api/metrics?**', async (route) => {
    const old = windowMinutes(route.request().url()) === 1440
    if (old) await gate
    await route.fulfill({ json: { ...metrics, jev_threshold: old ? 0.1 : 0.9 } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '1h', exact: true }).click()
  await expect(page.getByText('jev threshold 0.9', { exact: true })).toBeVisible()
  release()
  await page.waitForTimeout(150)
  await expect(page.getByText('jev threshold 0.9', { exact: true })).toBeVisible()
  await expect(page.getByText('jev threshold 0.1', { exact: true })).toHaveCount(0)
})

async function resize(page: Page, handle: Locator, axis: 'x' | 'y', delta: number) {
  await handle.hover()
  const box = await handle.boundingBox()
  expect(box).not.toBeNull()
  const before = Number(await handle.getAttribute('aria-valuenow'))
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2)
  await page.mouse.down()
  await page.mouse.move(box!.x + box!.width / 2 + (axis === 'x' ? delta : 0), box!.y + box!.height / 2 + (axis === 'y' ? delta : 0), { steps: 8 })
  await page.mouse.up()
  await expect(handle).not.toHaveAttribute('aria-valuenow', String(before))
  const after = Number(await handle.getAttribute('aria-valuenow'))
  expect(after).not.toBe(before)
  await handle.focus()
  await page.keyboard.press(axis === 'x' ? 'ArrowLeft' : 'ArrowUp')
  await expect(handle).not.toHaveAttribute('aria-valuenow', String(after))
  await page.keyboard.press('Home')
  expect(Number(await handle.getAttribute('aria-valuenow'))).toBe(Number(await handle.getAttribute('aria-valuemin')))
  await page.keyboard.press('End')
  expect(Number(await handle.getAttribute('aria-valuenow'))).toBe(Number(await handle.getAttribute('aria-valuemax')))
  await handle.dblclick()
  await expect(handle).toHaveAttribute('aria-valuenow', String(before))
}

test('all panel dividers work with pointer and keyboard', async ({ page }) => {
  await page.goto('/#audit')
  await expect(page.getByRole('separator', { name: 'Resize navigation' })).toHaveCount(0)
  await expect(page.getByRole('complementary')).toHaveCount(0)
  await expect(page.getByText('NORMAL', { exact: true })).toHaveCount(0)
  await expect(page.locator('header')).not.toContainText('aegis@localhost')
  await expect(page.locator('footer')).not.toContainText('aegis://')
  await expect(page.locator('header')).not.toContainText(/\d{2}:\d{2}:\d{2}/)
  await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link')).toHaveCount(5)
  await expect(page.locator('nav a[href="#audit"]')).toHaveAttribute('aria-current', 'page')
  for (const link of await page.locator('nav a').all()) {
    const label = await link.textContent()
    expect(label).not.toContain('[')
    expect(label).not.toContain(']')
  }
  await expect(page.getByRole('heading', { name: 'Audit log (0)' })).toBeVisible()
  await resize(page, page.getByRole('separator', { name: 'Resize audit trace' }), 'x', -100)
  await page.locator('nav a[href="#playground"]').click()
  await expect(page.getByRole('heading', { name: 'Inspect every interaction.' })).toBeVisible()
  await resize(page, page.getByRole('separator', { name: 'Resize playground trace' }), 'x', -100)
  await resize(page, page.getByRole('separator', { name: 'Resize decision trace' }), 'y', -80)
  await resize(page, page.getByRole('separator', { name: 'Resize prompt' }), 'y', -80)
  await expect(page.getByLabel('Custom time window')).toHaveCount(0)
})

test('prompt stays multiline and usable after resizing and viewport changes', async ({ page }) => {
  await page.goto('/#playground')
  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true })
  await prompt.fill('First line')
  await prompt.press('Shift+Enter')
  await prompt.press('End')
  await prompt.press('a')
  await expect(prompt).toHaveValue('First line\na')
  await resize(page, page.getByRole('separator', { name: 'Resize prompt' }), 'y', -80)
  await expect(prompt).toHaveValue('First line\na')
  await page.setViewportSize({ width: 900, height: 650 })
  await expect(prompt).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(overflow).toBe(false)
})

test('multiline prompt sends with shortcut and button and retains safe history', async ({ page }) => {
  await page.route('http://localhost:8000/v1/chat/completions', async (route) => {
    await route.fulfill({ json: {
      choices: [{ message: { role: 'assistant', content: 'Received the prompt.' } }],
      control: { request_id: 'test-prompt', decisions: [{ checkpoint: 'output', action: 'allow', results: [], blocked_by: null }] },
    } })
  })
  await page.goto('/#playground')
  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true })
  await prompt.fill('First line\nSecond line')
  const first = page.waitForRequest('http://localhost:8000/v1/chat/completions')
  await prompt.press('Enter')
  expect((await first).postDataJSON().messages).toEqual([{ role: 'user', content: 'First line\nSecond line' }])
  await expect(prompt).toBeEnabled()
  await expect(prompt).toHaveValue('')
  await prompt.fill('Draft follow-up')
  await page.locator('nav a[href="#audit"]').click()
  await page.locator('nav a[href="#playground"]').click()
  await expect(prompt).toHaveValue('Draft follow-up')
  await expect(page.getByText('assistant> Received the prompt.')).toBeVisible()
  await prompt.fill('Follow-up')
  const second = page.waitForRequest('http://localhost:8000/v1/chat/completions')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  expect((await second).postDataJSON().messages).toEqual([
    { role: 'user', content: 'First line\nSecond line' },
    { role: 'assistant', content: 'Received the prompt.' },
    { role: 'user', content: 'Follow-up' },
  ])
  await expect(page.getByText('checkpoint: output', { exact: false })).toBeVisible()
})

test('pending replies survive navigation and blocked turns stay out of history', async ({ page }) => {
  let release = () => {}
  const pending = new Promise<void>((resolve) => { release = resolve })
  let calls = 0
  await page.route('http://localhost:8000/v1/chat/completions', async (route) => {
    calls += 1
    if (calls === 1) await pending
    await route.fulfill({ json: {
      choices: [{ message: { role: 'assistant', content: calls === 1 ? 'Blocked test prompt.' : 'Safe reply.' } }],
      control: { request_id: `pending-${calls}`, decisions: [{ checkpoint: 'input', action: calls === 1 ? 'block' : 'allow', blocked_by: calls === 1 ? 'signatures' : null, results: [] }] },
    } })
  })
  await page.goto('/#playground')
  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true })
  await prompt.fill('Blocked prompt')
  const sent = page.waitForRequest('http://localhost:8000/v1/chat/completions')
  await prompt.press('Enter')
  await sent
  await page.locator('nav a[href="#audit"]').click()
  release()
  await page.locator('nav a[href="#playground"]').click()
  await expect(page.getByText('Blocked test prompt.', { exact: true })).toBeVisible()
  await expect(prompt).toBeEnabled()
  await prompt.fill('Safe follow-up')
  const followUp = page.waitForRequest('http://localhost:8000/v1/chat/completions')
  await prompt.press('Enter')
  expect((await followUp).postDataJSON().messages).toEqual([{ role: 'user', content: 'Safe follow-up' }])
  await expect(page.getByText('Blocked test prompt.', { exact: true })).toBeVisible()
})

test('overview explains independent timings and daily usage', async ({ page }) => {
  await page.route('http://localhost:8000/api/metrics?**', async (route) => {
    await route.fulfill({ json: { ...metrics,
      enabled_checks: { jev: { input: 'block' } },
      latency_ms_by_check: { jev: { p50: 32, p95: 84 } },
      budget_by_caller: { analyst: { tokens_today: 1200, tokens_limit: 0, cost_today: 0.0123, cost_limit: 0 } },
      request_latency_ms: { ...metrics.request_latency_ms, total: { p50: 400, p95: 900 } },
    } })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Response time', exact: true })).toBeVisible()
  await expect(page.getByText('400.0 ms', { exact: true })).toBeVisible()
  await expect(page.getByText('900.0 ms', { exact: true })).toBeVisible()
  await expect(page.getByText('Budget limits are unavailable', { exact: false })).toBeVisible()
  await expect(page.getByText('$0.0123', { exact: true })).toBeVisible()
  await expect(page.getByText('∞', { exact: false })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: /OWASP/ })).toHaveCount(0)
  await page.locator('.overview-workspace').evaluate((element) => { element.scrollTop = 0 })
  await page.screenshot({ path: 'test-results/overview-usability.png' })
  await page.locator('.overview-workspace').evaluate((element) => { element.scrollTop = element.scrollHeight })
  await page.screenshot({ path: 'test-results/overview-metrics.png' })
})

test('audit separates interleaved requests and highlights summary cost', async ({ page }) => {
  const base = { ts: '2026-10-04T00:00:00Z', caller_id: 'analyst', model: 'demo', checkpoint: 'input', action: 'allow', reason: 'Allowed', score: 0, latency_ms: 1, decided_by: 'rules', tokens: 0, cost: 0, policy_version: 'test', conversation_id: null, step: null, tools: null, overhead_ms: null, upstream_latency_ms: null, blocked_by: null }
  const items = [
    { ...base, id: 6, request_id: 'request-a', check: 'permissions', cost: 0.2 },
    { ...base, id: 5, request_id: 'request-b', check: 'signatures', action: 'block', score: 1 },
    { ...base, id: 4, request_id: 'request-a', check: 'jev', checkpoint: 'output', decided_by: 'fallback', score: 0.25 },
    { ...base, id: 3, request_id: 'request-a', check: 'turn_summary', checkpoint: 'output', tokens: 50, cost: 0.012345 },
    { ...base, id: 2, request_id: 'request-a', check: 'turn_summary' },
    { ...base, id: 1, request_id: null, checkpoint: null, check: 'policy_loaded' },
  ]
  await page.route('http://localhost:8000/api/audit?**', async (route) => route.fulfill({ json: { total: items.length, items } }))
  await page.goto('/#audit')
  await expect(page.locator('.audit-execution')).toHaveCount(3)
  const first = page.locator('.audit-execution').first()
  await expect(first.locator('tr')).toHaveCount(3)
  await expect(first).toContainText('✓ Passed')
  await expect(first.locator('.risk-score')).toContainText('0.25')
  await expect(first.locator('.rule-outcome')).not.toContainText('0.00')
  await first.getByText('permissions', { exact: true }).click()
  await expect(page.locator('.audit-cost strong')).toHaveText('$0.012345')
  await expect(page.locator('.audit-cost')).toContainText('50 tokens')
  await expect(page.locator('.audit-cost strong')).toHaveCSS('color', 'rgb(224, 123, 57)')
  await page.screenshot({ path: 'test-results/audit-usability.png' })
})
