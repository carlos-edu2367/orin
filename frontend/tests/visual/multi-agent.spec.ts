import { expect, test } from '@playwright/test'

/**
 * Capture the richest conversation the local database holds: the one with
 * subagents, agent-to-agent messages and several tool families. This is the view
 * that has to stay readable, so it is the one worth looking at.
 */

const BASE = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.AGENTOS_BASE_URL ?? 'http://127.0.0.1:8000'

test('the multi-agent conversation stays readable', async ({ page }) => {
  const response = await page.request.get(`${BASE}/v1/conversations`)
  const list = await response.json() as { items: Array<{ conversation_id: string; title: string }> }
  const target = list.items.find((item) => /subagente/i.test(item.title)) ?? list.items[0]
  test.skip(!target, 'no conversation available in the local database')

  await page.goto(`${BASE}/chats/${target.conversation_id}`)
  await expect(page.locator('.bubble').first()).toBeVisible()
  await expect(page.locator('.agent-birth')).toBeVisible()
  await expect(page.locator('.agent-exchange')).toHaveCount(2)
  await page.waitForTimeout(900)
  await page.screenshot({ path: 'tests/visual/output/multi-agent.png', fullPage: true })

  await page.getByRole('button', { name: 'Visão geral' }).click()
  await expect(page.getByRole('complementary', { name: 'Visão geral da execução' })).toBeVisible()
  // Assert the shape, not a specific name: whichever conversation the local
  // database holds, the overview must show Main plus the subagent it created.
  await expect(page.locator('.overview__agent')).toHaveCount(2)
  await expect(page.locator('.overview__agent').first()).toContainText('Main')
  await page.waitForTimeout(1400)
  await page.screenshot({ path: 'tests/visual/output/multi-agent-overview.png' })
})
