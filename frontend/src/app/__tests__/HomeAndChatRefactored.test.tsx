import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../../api/client';
import { ChatPage } from '../../features/conversations/ChatPage';
import { Home } from '../Home';
import { ThemeProvider } from '../ThemeContext';
import { appRoutes } from '../routes';
import homeSource from '../Home.tsx?raw';
import chatPageSource from '../../features/conversations/ChatPage.tsx?raw';

const READY_SESSION = { status: 'ready', csrfToken: 'csrf-home-test' } as const;

const MODEL = {
  provider: 'openrouter',
  model_id: 'anthropic/model-a',
  display_name: 'Model A',
  context_window: 128000,
  capabilities: [],
  input_modalities: ['text'],
  output_modalities: ['text'],
  pricing: null,
  is_favorite: false,
  refreshed_at: null,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function createHomeClientStub() {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url ?? String(input);
    if (url.includes('/models')) return json({ items: [MODEL] });
    if (url.includes('/v1/conversations')) {
      return json({
        items: [{ conversation_id: 'c-1', title: 'Conversa antiga', state: 'completed' }],
      });
    }
    if (url.includes('/v1/projects/sidebar')) return json({ items: [] });
    if (url.includes('/v1/settings/code-mode')) {
      return json({
        autonomy: 'balanced',
        system_notifications: false,
        monitoring_enabled: true,
      });
    }
    return json({ items: [] });
  });
  return new ApiClient({ fetchImpl, maxAttempts: 1 });
}

describe('Home, ChatPage, and Routes Refactored Shell', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('renders Home with top-left ModelPicker in canvas-header, modeSwitch and sessionContext in Composer, contextual quick actions, and no bottom recent cards', async () => {
    const user = userEvent.setup();
    const client = createHomeClientStub();

    const router = createMemoryRouter(
      [{ path: '/', element: <Home client={client} bootstrap={READY_SESSION} /> }],
      { initialEntries: ['/'] },
    );

    const { container } = render(
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>,
    );

    const header = await screen.findByTestId('canvas-header');
    expect(header.querySelector('.model-picker')).not.toBeNull();
    expect(container.querySelectorAll('.model-picker').length).toBe(1);

    expect(
      await screen.findByRole('heading', { name: 'Por onde começamos?' }),
    ).toBeInTheDocument();

    const quickActions = screen.getByTestId('home-quick-actions');
    const synthChip = within(quickActions).getByRole('button', { name: /Pesquisar e sintetizar/i });
    await user.click(synthChip);
    expect(screen.getByRole('textbox', { name: 'Mensagem' })).toHaveValue('Pesquise e sintetize sobre: ');

    await user.click(screen.getByTestId('home-mode-code'));
    expect(
      await screen.findByRole('heading', {
        name: 'Qual repositório vamos construir ou evoluir?',
      }),
    ).toBeInTheDocument();
    const planChip = within(screen.getByTestId('home-quick-actions')).getByRole('button', {
      name: /Planejar arquitetura/i,
    });
    await user.click(planChip);
    expect(screen.getByTestId('code-permission-mode-pill')).toHaveAttribute('data-permission-mode', 'plan_mode');
    expect(screen.getByTestId('code-permission-mode-pill')).toHaveTextContent(/Modo Plano/i);
    expect(screen.getByTestId('session-context-trigger')).toBeInTheDocument();

    expect(container.querySelector('.home__mode-switch')).toBeNull();
    expect(container.querySelector('.home__code-workspace-bar')).toBeNull();
    expect(container.querySelector('.home__recent')).toBeNull();
    expect(container.querySelector('.home__foot')).toBeNull();
  });

  it('respects ambient === "off" in Home by suppressing AmbientField and glow', async () => {
    window.localStorage.setItem('orin.theme.ambient', 'off');
    const client = createHomeClientStub();

    const router = createMemoryRouter(
      [{ path: '/', element: <Home client={client} bootstrap={READY_SESSION} /> }],
      { initialEntries: ['/'] },
    );

    const { container } = render(
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>,
    );

    await screen.findByRole('heading', { name: 'Por onde começamos?' });
    expect(container.querySelector('.home__glow')).toBeNull();
    expect(container.querySelector('.ambient-field')).toBeNull();
  });

  it('renders ChatPage with single top-left unified ModelPicker in canvas-header and SessionContextPopover inside Composer', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input);
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.1"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      if (url.includes('/models')) {
        return json({
          items: [
            {
              provider: 'openai',
              model_id: 'gpt-5.4',
              display_name: 'GPT-5.4',
              context_window: 272000,
              capabilities: ['tools'],
              input_modalities: ['text'],
              output_modalities: ['text'],
              pricing: null,
              is_favorite: true,
              refreshed_at: null,
            },
          ],
        });
      }
      if (url.includes('/v1/conversations/conv_refactored')) {
        return json({
          conversation_id: 'conv_refactored',
          title: 'Refatorar Shell',
          state: 'completed',
          mode: 'code',
          permission_mode: 'accept_edits',
          provider: 'openai',
          model_id: 'gpt-5.4',
          messages: [],
          turns: [],
          activities: [],
          activity_cursor: 'a.1',
          workspace: {
            kind: 'local',
            path: '/repo/orin',
            folder_name: 'orin',
            scope: 'chat',
            project_name: null,
          },
        });
      }
      return json({ items: [] });
    });

    try {
      const { container } = render(
        <ThemeProvider>
          <MemoryRouter initialEntries={['/chats/conv_refactored']}>
            <Routes>
              <Route path="/chats/:conversationId" element={<ChatPage />} />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>,
      );

      const header = await screen.findByTestId('canvas-header');
      expect(await screen.findByRole('heading', { name: 'Refatorar Shell' })).toBeInTheDocument();
      expect(header.querySelector('.canvas-header__left .model-picker')).not.toBeNull();
      expect(container.querySelectorAll('.model-picker').length).toBe(1);
      expect(screen.getByTestId('session-context-trigger')).toBeInTheDocument();
      expect(screen.getByTestId('orin-code-badge')).toBeInTheDocument();
      expect(screen.getByTestId('orin-code-workspace-chip')).toHaveTextContent('orin');
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('routes /library/skills and /integrations/providers render LibraryHubPage and IntegrationsHubPage inside WorkspaceShell', async () => {
    const router = createMemoryRouter(appRoutes, {
      initialEntries: ['/library/skills'],
    });
    render(
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>,
    );

    expect(await screen.findByTestId('workspace-shell')).toBeInTheDocument();
    expect(await screen.findByTestId('library-hub-page')).toBeInTheDocument();

    const intRouter = createMemoryRouter(appRoutes, {
      initialEntries: ['/integrations/providers'],
    });
    render(
      <ThemeProvider>
        <RouterProvider router={intRouter} />
      </ThemeProvider>,
    );
    expect(await screen.findByTestId('integrations-hub-page')).toBeInTheDocument();
  });

  it('contains zero hardcoded dark-only utility classes (text-white/*, bg-black/*, border-white/*) in Home.tsx and ChatPage.tsx', () => {
    expect(homeSource).not.toMatch(/\b(text-white|bg-black|border-white)\b/);
    expect(chatPageSource).not.toMatch(/\b(text-white|bg-black|border-white)\b/);
  });
});
