import { expect, test, type Locator, type Page } from '@playwright/test'

const metrics = {
  active_profile: 'balanced', enabled_checks: {},
  totals: { requests: 0, allowed: 0, redacted: 0, blocked: 0, flagged: 0 },
  timeline: [], blocks_by_check: {}, latency_ms_by_check: {},
  overhead_ms: { p50: 0, p95: 0 }, budget_by_caller: {}, tokens_total: 0, cost_total: 0,
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

test('presets and custom window apply to Overview, Audit and exports', async ({ page }) => {
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
  await page.locator('input').first().fill('demo')
  for (const name of ['[e]xport csv', '[j]son']) {
    const href = await page.getByRole('link', { name, exact: true }).getAttribute('href')
    expect(windowMinutes(href!)).toBe(45)
    expect(new URL(href!).searchParams.get('caller_id')).toBe('demo')
  }
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
    await route.fulfill({ json: { ...metrics, active_profile: old ? 'old-window' : 'new-window' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '1h', exact: true }).click()
  await expect(page.getByText('profile [new-window]', { exact: true })).toBeVisible()
  release()
  await page.waitForTimeout(150)
  await expect(page.getByText('profile [new-window]', { exact: true })).toBeVisible()
  await expect(page.getByText('profile [old-window]', { exact: true })).toHaveCount(0)
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
  await resize(page, page.getByRole('separator', { name: 'Resize navigation' }), 'x', 80)
  await resize(page, page.getByRole('separator', { name: 'Resize audit trace' }), 'x', -100)
  await page.locator('nav a[href="#playground"]').click()
  await resize(page, page.getByRole('separator', { name: 'Resize playground trace' }), 'x', -100)
  await resize(page, page.getByRole('separator', { name: 'Resize decision trace' }), 'y', -80)
  await resize(page, page.getByRole('separator', { name: 'Resize prompt' }), 'y', -80)
  await expect(page.getByLabel('Custom time window')).toHaveCount(0)
})

test('prompt stays multiline and usable after resizing and viewport changes', async ({ page }) => {
  await page.goto('/#playground')
  const prompt = page.getByRole('textbox', { name: 'Prompt', exact: true })
  await prompt.fill('First line')
  await prompt.press('Enter')
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
  await prompt.press('Control+Enter')
  expect((await first).postDataJSON().messages).toEqual([{ role: 'user', content: 'First line\nSecond line' }])
  await expect(prompt).toBeEnabled()
  await expect(prompt).toHaveValue('')
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
