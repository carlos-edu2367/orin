import { env as processEnv } from 'node:process'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { mockSettings } from '../support/w7Settings'

function screenshotPath(testInfo: TestInfo, filename: string): string {
  const customDir = processEnv.ORIN_VISUAL_ARTIFACT_DIR
  return customDir ? `${customDir}/${filename}` : testInfo.outputPath(filename)
}

async function installCsrfMeta(page: Page): Promise<void> {
  await page.route('**/*', async (route) => {
    if (route.request().resourceType() !== 'document') {
      await route.fallback()
      return
    }
    const response = await route.fetch()
    const body = (await response.text()).replace(
      '<head>',
      '<head><meta name="csrf-token" content="csrf-visual-verify">',
    )
    await route.fulfill({ response, body })
  })
}

test.describe('Visual & Functional E2E Verification — All Visual Cases (1, 3, 4, 6)', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await installCsrfMeta(page)
    await mockSettings(page)
  })

  test('Case 1: Orin Code starts with CodeWorkbenchPanel collapsed and expands/collapses via Bancada button', async ({ page }, testInfo) => {
    await page.route('**/v1/projects/sidebar', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
    })
    await page.route('**/v1/conversations', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              conversation_id: 'conv-code-1',
              title: 'Educly Git Status',
              state: 'completed',
              mode: 'code',
              updated_at: '2026-10-31T10:00:00.000Z',
            },
          ],
        }),
      })
    })
    await page.route('**/v1/conversations/conv-code-1**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/workspace-files')) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation_id: 'conv-code-1',
          title: 'Educly Git Status',
          state: 'completed',
          provider: 'anthropic',
          model_id: 'claude-sonnet-4-6',
          mode: 'code',
          permission_mode: 'accept_edits',
          project_id: null,
          workspace: {
            mode: 'local_folder',
            path: 'C:/Users/carlos/Documents/trabalho/educly',
            folder_name: 'educly',
            status: 'available',
          },
          turns: [
            {
              turn_id: 'turn-1',
              state: 'completed',
              created_at: '2026-10-31T10:00:00.000Z',
              started_at: '2026-10-31T10:00:01.000Z',
              finished_at: '2026-10-31T10:00:05.000Z',
              scheduled_by_schedule_id: null,
              code_mode: 'code',
            },
          ],
          messages: [
            {
              message_id: 'msg-u1',
              turn_id: 'turn-1',
              role: 'user',
              content: 'de um git pull da main.',
              status: 'completed',
              retryable: false,
              attachments: [],
              command: null,
            },
            {
              message_id: 'msg-a1',
              turn_id: 'turn-1',
              role: 'assistant',
              content: 'Repositório educly atualizado na branch main com sucesso.',
              status: 'completed',
              retryable: false,
              attachments: [],
              command: null,
            },
          ],
          activities: [
            {
              event_id: 'ev-diff-1',
              cursor: '1',
              turn_id: 'turn-1',
              agent_id: 'agent:main',
              event_type: 'tool.completed',
              summary: 'Editou src/App.tsx',
              occurred_at: '2026-10-31T10:00:03.000Z',
              payload: {
                tool_name: 'edit_file',
                tool_kind: 'write',
                status: 'completed',
                path: 'src/App.tsx',
                added_lines: 4,
                removed_lines: 1,
                diff: '@@ -1,3 +1,6 @@\n+import React from "react"\n export function App() {}',
              },
            },
          ],
          has_older_activities: false,
          oldest_activity_id: 1,
          activity_cursor: '1',
        }),
      })
    })

    await page.goto('/chats/conv-code-1')
    const main = page.locator('main.chat')
    await expect(main).toBeVisible()
    await expect(page.getByText('Repositório educly atualizado na branch main com sucesso.')).toBeVisible()
    await expect(main).not.toHaveClass(/chat--code-split/)
    await expect(page.getByTestId('code-workbench-panel')).toHaveCount(0)

    const toggleBtn = page.getByTestId('code-workbench-toggle')
    await expect(toggleBtn).toBeVisible()
    await expect(toggleBtn).toHaveAttribute('aria-expanded', 'false')

    await page.screenshot({ path: screenshotPath(testInfo, 'verify-case1-code-collapsed.png') })

    // Click to expand workbench
    await toggleBtn.click()
    await expect(main).toHaveClass(/chat--code-split/)
    const workbench = page.getByTestId('code-workbench-panel')
    await expect(workbench).toBeVisible()
    await expect(toggleBtn).toHaveAttribute('aria-expanded', 'true')

    await page.screenshot({ path: screenshotPath(testInfo, 'verify-case1-code-expanded.png') })

    // Click close button inside workbench
    await page.getByTestId('code-workbench-close').click()
    await expect(page.getByTestId('code-workbench-panel')).toHaveCount(0)
    await expect(main).not.toHaveClass(/chat--code-split/)
  })

  test('Case 3: Anthropic provider drawer has zero horizontal overflow and all controls fit inside drawer bounds', async ({ page }, testInfo) => {
    await page.route('**/v1/providers/anthropic', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          provider: 'anthropic',
          enabled: true,
          mode: 'local',
          base_url: 'http://127.0.0.1:18447',
          has_api_key: false,
          masked_key: null,
          available_for_agents: true,
          status: 'connected',
          keys: [],
          cooldown_seconds: 60,
        }),
      })
    })
    await page.route('**/v1/providers/anthropic/models', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            { model_id: 'claude-sonnet-4-6', display_name: 'Claude Sonnet 4.6', is_favorite: true },
          ],
        }),
      })
    })
    await page.route('**/v1/providers/anthropic/keys', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], cooldown_seconds: 60 }),
      })
    })

    await page.goto('/integrations/providers/anthropic')
    const drawer = page.locator('.slide-over-drawer')
    await expect(drawer).toBeVisible()
    await expect(drawer.getByRole('heading', { name: 'Anthropic' })).toBeVisible()

    // Check zero horizontal overflow inside .slide-over-drawer and .settings-drawer
    const overflowMetrics = await drawer.evaluate((el) => {
      const settingsDrawer = el.querySelector('.settings-drawer') as HTMLElement | null
      return {
        drawerClientWidth: el.clientWidth,
        drawerScrollWidth: el.scrollWidth,
        settingsClientWidth: settingsDrawer?.clientWidth ?? 0,
        settingsScrollWidth: settingsDrawer?.scrollWidth ?? 0,
      }
    })
    expect(overflowMetrics.drawerScrollWidth).toBeLessThanOrEqual(overflowMetrics.drawerClientWidth + 1)
    expect(overflowMetrics.settingsScrollWidth).toBeLessThanOrEqual(overflowMetrics.settingsClientWidth + 1)

    // Verify radio mode chips and action buttons are completely within the drawer's horizontal bounds
    const drawerBox = await drawer.boundingBox()
    expect(drawerBox).not.toBeNull()
    for (const label of ['Assinatura Claude Code (CLI Local)', 'Cloud (Chave de API)']) {
      const radio = drawer.getByRole('radio', { name: label }).first()
      await expect(radio).toBeVisible()
      const box = await radio.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.x).toBeGreaterThanOrEqual(drawerBox!.x - 1)
      expect(box!.x + box!.width).toBeLessThanOrEqual(drawerBox!.x + drawerBox!.width + 1)
    }
    for (const label of ['Testar conexão', 'Salvar', 'Revogar acesso', 'Adicionar chave']) {
      const btn = drawer.getByRole('button', { name: label }).first()
      await expect(btn).toBeVisible()
      const box = await btn.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.x).toBeGreaterThanOrEqual(drawerBox!.x - 1)
      expect(box!.x + box!.width).toBeLessThanOrEqual(drawerBox!.x + drawerBox!.width + 1)
    }

    await page.screenshot({ path: screenshotPath(testInfo, 'verify-case3-anthropic-drawer.png') })
  })

  test('Case 4 & Case 6: Topbar mascot conditional green dot + inline approvals popover, and Light Mode mascot contrast on Home', async ({ page }, testInfo) => {
    let permissionResolved = false

    await page.route('**/v1/projects/sidebar', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
    })

    await page.route('**/v1/conversations', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              conversation_id: 'conv-pending-1',
              title: 'Clonar repositório Educly',
              state: permissionResolved ? 'completed' : 'running',
              mode: 'code',
              updated_at: '2026-10-31T10:30:00.000Z',
            },
          ],
        }),
      })
    })

    await page.route('**/v1/conversations/conv-pending-1', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation_id: 'conv-pending-1',
          title: 'Clonar repositório Educly',
          state: permissionResolved ? 'completed' : 'running',
          provider: 'anthropic',
          model_id: 'claude-sonnet-4-6',
          mode: 'code',
          permission_mode: 'ask_permissions',
          project_id: null,
          workspace: {
            mode: 'local_folder',
            path: 'C:/Users/carlos/Documents/trabalho/educly',
            folder_name: 'educly',
            status: 'available',
          },
          turns: [
            {
              turn_id: 'turn-p1',
              state: permissionResolved ? 'completed' : 'running',
              created_at: '2026-10-31T10:30:00.000Z',
              started_at: '2026-10-31T10:30:01.000Z',
              finished_at: permissionResolved ? '2026-10-31T10:30:10.000Z' : null,
              scheduled_by_schedule_id: null,
              code_mode: 'code',
            },
          ],
          messages: [],
          activities: permissionResolved
            ? []
            : [
                {
                  event_id: 'ev-perm-1',
                  cursor: '1',
                  turn_id: 'turn-p1',
                  agent_id: 'agent:main',
                  event_type: 'code_mode.decision_required',
                  summary: 'Aguardando permissão: Bash(git clone https://github.com/ContraEstatisticas/educlyapp-1.00.0.01 .)',
                  occurred_at: '2026-10-31T10:30:02.000Z',
                  payload: {
                    stage: 'waiting_decision',
                    state: 'waiting_user',
                    tool_name: 'Bash',
                    permission_request_id: 'req-git-clone-1',
                    suggested_rule: 'Bash(git clone:*)',
                    rule_suggestion: 'Bash(git clone:*)',
                    permission_mode: 'ask_permissions',
                    command: 'git clone https://github.com/ContraEstatisticas/educlyapp-1.00.0.01 .',
                    permission_request: {
                      request_id: 'req-git-clone-1',
                      status: 'pending',
                      tool_name: 'Bash',
                      command: 'git clone https://github.com/ContraEstatisticas/educlyapp-1.00.0.01 .',
                      reason: 'Clonar o repositório na pasta do projeto',
                      suggested_rule: 'Bash(git clone:*)',
                      rule_suggestion: 'Bash(git clone:*)',
                      permission_mode: 'ask_permissions',
                      turn_id: 'turn-p1',
                    },
                  },
                },
              ],
          has_older_activities: false,
          oldest_activity_id: 1,
          activity_cursor: '1',
        }),
      })
    })

    await page.route('**/v1/conversations/conv-pending-1/turns/turn-p1/tool-permission', async (route) => {
      permissionResolved = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation_id: 'conv-pending-1',
          turn_id: 'turn-p1',
          request_id: 'req-git-clone-1',
          decision: 'allow_once',
          status: 'allowed',
        }),
      })
    })

    await page.goto('/')
    // Wait for OrinActivityCenter to detect the pending permission and show the green status dot
    const trigger = page.getByTestId('orin-activity-center-trigger')
    await expect(trigger).toBeVisible()
    await expect(trigger.getByTestId('orin-mascot-status-dot')).toBeVisible()
    await expect(page.getByTestId('orin-activity-center-badge')).toHaveText('1')

    // Click the topbar mascot to open the Activity Center popover
    await trigger.click()
    const popover = page.getByTestId('orin-activity-center-popover')
    await expect(popover).toBeVisible()
    await expect(popover).toContainText('Clonar repositório Educly')
    await expect(popover).toContainText('git clone https://github.com/ContraEstatisticas/educlyapp-1.00.0.01 .')

    await page.screenshot({ path: screenshotPath(testInfo, 'verify-case4-activity-center-popover.png') })

    // Approve inline via "Permitir"
    await page.getByTestId('activity-center-allow-once-req-git-clone-1').click()

    // Because conv-pending-1 transitioned from running -> completed in the background,
    // it now appears under "Novidades (1)"; wait for "Marcar tudo como lido" and click it
    const markAllBtn = popover.getByRole('button', { name: 'Marcar tudo como lido' })
    await expect(markAllBtn).toBeVisible()
    await markAllBtn.click()
    await expect(trigger.getByTestId('orin-mascot-status-dot')).toHaveCount(0)

    // Close popover and verify idle state has NO green dot
    await page.keyboard.press('Escape')
    await expect(trigger.getByTestId('orin-mascot-status-dot')).toHaveCount(0)
    await page.screenshot({ path: screenshotPath(testInfo, 'verify-case4-mascot-idle-no-dot.png') })

    // Switch to Light Mode on Home to verify Case 6 (Mascot contrast in Light Mode)
    await page.evaluate(() => {
      window.localStorage.setItem('orin.theme.mode', 'light')
      document.documentElement.setAttribute('data-theme', 'light')
      document.documentElement.setAttribute('data-theme-preference', 'light')
    })
    await page.waitForTimeout(300)

    const heroMascot = page.getByTestId('home-hero-mascot').locator('.orin-mascot')
    await expect(heroMascot).toBeVisible()
    const mascotTokens = await heroMascot.evaluate((el) => {
      const style = window.getComputedStyle(el)
      return {
        soft: style.getPropertyValue('--orin-accent-soft').trim(),
        body: style.getPropertyValue('--orin-accent').trim(),
        strong: style.getPropertyValue('--orin-accent-strong').trim(),
        feature: style.getPropertyValue('--orin-mascot-feature').trim(),
      }
    })
    expect(mascotTokens.soft.toUpperCase()).toBe('#E4DCFF')
    expect(mascotTokens.body.toUpperCase()).toBe('#B4A4EF')
    expect(mascotTokens.strong.toUpperCase()).toBe('#8870DF')
    expect(mascotTokens.feature.toUpperCase()).toBe('#282333')

    await page.screenshot({ path: screenshotPath(testInfo, 'verify-case6-home-light-mascot.png') })

    // Also verify Mobile viewport (390x844) in Light Mode
    await page.setViewportSize({ width: 390, height: 844 })
    await page.waitForTimeout(200)
    await page.screenshot({ path: screenshotPath(testInfo, 'verify-case6-home-light-mobile.png') })
  })
})
