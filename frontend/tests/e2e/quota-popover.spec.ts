import { mkdirSync } from 'node:fs'
import { env } from 'node:process'
import { expect, test, type Page } from '@playwright/test'
import { CONVERSATION_ID, mockChat, setTheme } from '../support/w7Chat'

/**
 * The quota popover opens as a compact vertical list: nothing overlaps and the panel never scrolls sideways.
 * Set `QUOTA_SHOTS_DIR` to also save screenshots of the open panel (used by the manual visual check).
 */

const QUOTA_REPORT = {
  subscriptions: [
    {
      provider: 'anthropic-cli',
      windows: [
        { kind: 'seven_day', used_percent: 38, resets_at: '2026-11-24T17:59:59Z', status: 'allowed', scope_model: null, scope_models: [] },
        { kind: 'weekly_scoped', used_percent: 3, resets_at: '2026-11-24T17:59:59Z', status: 'allowed', scope_model: 'Fable', scope_models: [] },
      ],
      confidence: 'measured',
      observed_at: new Date(Date.now() - 2 * 60_000).toISOString(),
      age_seconds: 120,
      source: 'claude_usage_command',
      orin_usage: { five_hour: { tokens: 42500, calls: 7 }, seven_day: { tokens: 310000, calls: 48 } },
    },
    {
      provider: 'openai-codex',
      windows: [
        { kind: 'five_hour', used_percent: 0, resets_at: '2026-11-23T17:35:19Z', status: 'allowed', scope_model: null, scope_models: [] },
        { kind: 'seven_day', used_percent: 4, resets_at: '2026-11-26T16:59:08Z', status: 'allowed', scope_model: null, scope_models: [] },
      ],
      confidence: 'measured',
      observed_at: new Date(Date.now() - 60_000).toISOString(),
      age_seconds: 60,
      source: 'codex_app_server',
      orin_usage: { five_hour: { tokens: 0, calls: 0 }, seven_day: { tokens: 18000, calls: 3 } },
    },
    {
      provider: 'google-antigravity',
      windows: [
        { kind: 'five_hour', used_percent: 0.17, resets_at: '2026-11-23T17:20:26Z', status: 'allowed', scope_model: 'Gemini', scope_models: ['Gemini 4 Neon', 'Gemini 4 Carbon', 'Gemini 4 Argon', 'Gemini 3.8 Flash'] },
        { kind: 'seven_day', used_percent: 0.05, resets_at: '2026-11-30T12:20:26Z', status: 'allowed', scope_model: 'Gemini', scope_models: ['Gemini 4 Neon', 'Gemini 4 Carbon', 'Gemini 4 Argon', 'Gemini 3.8 Flash'] },
        { kind: 'five_hour', used_percent: 0, resets_at: '2026-11-23T17:21:19Z', status: 'allowed', scope_model: 'Claude e GPT', scope_models: ['Claude Opus 5.5', 'Claude Sonnet 5.5', 'GPT-OSS 120B'] },
        { kind: 'seven_day', used_percent: 9, resets_at: '2026-11-25T18:03:09Z', status: 'allowed', scope_model: 'Claude e GPT', scope_models: ['Claude Opus 5.5', 'Claude Sonnet 5.5', 'GPT-OSS 120B'] },
      ],
      confidence: 'measured',
      observed_at: new Date(Date.now() - 30_000).toISOString(),
      age_seconds: 30,
      source: 'agy_usage_command',
      orin_usage: { five_hour: { tokens: 5000, calls: 2 }, seven_day: { tokens: 22000, calls: 9 } },
    },
  ],
}

async function openQuotaPanel(page: Page) {
  await mockChat(
    page,
    ({ method, path }) => (method === 'GET' && path === '/v1/quota' ? { body: QUOTA_REPORT } : undefined),
    { provider: 'anthropic-cli', model_id: 'claude-sonnet-5-5' },
  )
  await page.goto(`/chats/${CONVERSATION_ID}`)
  const ring = page.getByTestId('quota-ring-trigger')
  await expect(ring).toContainText('38%')
  await ring.click()
  const panel = page.getByTestId('quota-popover-panel')
  await expect(panel).toBeVisible()
  await expect(panel.getByTestId('quota-card-google-antigravity')).toContainText('Semanal · Claude e GPT')
  return panel
}

for (const viewport of [
  { name: '1280x800', width: 1280, height: 800 },
  { name: '900x800', width: 900, height: 800 },
]) {
  test(`quota popover is a vertical list without overlap or horizontal scroll at ${viewport.name}`, async ({ page }) => {
    await setTheme(page, 'dark')
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    const panel = await openQuotaPanel(page)

    const geometry = await panel.evaluate((element) => {
      const box = element.getBoundingClientRect()
      const cards = Array.from(element.querySelectorAll<HTMLElement>('.quota-card')).map((card) => {
        const rect = card.getBoundingClientRect()
        return { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right }
      })
      const labels = Array.from(element.querySelectorAll<HTMLElement>('.quota-card__window-label')).map((label) => ({
        text: label.textContent ?? '',
        clipped: label.scrollWidth > label.clientWidth + 1,
      }))
      const style = getComputedStyle(element)
      return {
        width: box.width,
        left: box.left,
        right: box.right,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
        overflowX: style.overflowX,
        overflowY: style.overflowY,
        maxHeight: style.maxHeight,
        cards,
        labels,
        viewportWidth: window.innerWidth,
      }
    })

    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth)
    expect(geometry.overflowX).toBe('hidden')
    expect(geometry.width).toBeGreaterThanOrEqual(400)
    expect(geometry.width).toBeLessThanOrEqual(444)
    expect(geometry.left).toBeGreaterThanOrEqual(0)
    expect(geometry.right).toBeLessThanOrEqual(geometry.viewportWidth)
    expect(geometry.cards).toHaveLength(3)
    for (let index = 1; index < geometry.cards.length; index += 1) {
      // Stacked: each block starts below the previous one and spans the same column.
      expect(geometry.cards[index].top).toBeGreaterThanOrEqual(geometry.cards[index - 1].bottom)
      expect(geometry.cards[index].left).toBe(geometry.cards[0].left)
    }
    expect(geometry.labels.filter((label) => label.clipped)).toEqual([])

    const dir = env.QUOTA_SHOTS_DIR
    if (dir) {
      mkdirSync(dir, { recursive: true })
      await page.screenshot({ path: `${dir}/quota-popover-${viewport.name}.png` })
      await panel.screenshot({ path: `${dir}/quota-popover-${viewport.name}-panel.png` })
      // The Antigravity block sits below the fold of a 560 px panel: scroll to it and capture that too.
      await panel.evaluate((element) => { element.scrollTop = element.scrollHeight })
      await panel.screenshot({ path: `${dir}/quota-popover-${viewport.name}-scrolled.png` })
      await expect(panel.getByTestId('quota-card-google-antigravity')).toBeInViewport()
    }
  })
}

test('the panel scrolls vertically only when it is taller than the viewport', async ({ page }) => {
  await setTheme(page, 'dark')
  await page.setViewportSize({ width: 900, height: 420 })
  const panel = await openQuotaPanel(page)
  const metrics = await panel.evaluate((element) => ({
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
    scrollWidth: element.scrollWidth,
    clientWidth: element.clientWidth,
    overflowY: getComputedStyle(element).overflowY,
  }))
  expect(metrics.overflowY).toBe('auto')
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight)
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth)
})
