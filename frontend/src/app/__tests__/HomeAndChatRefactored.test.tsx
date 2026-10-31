import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../../api/client';
import { ChatPage } from '../../features/conversations/ChatPage';
import { formatHomeGreeting, Home } from '../Home';
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

  it('renders Home (ref-01) with topbar Início + voice CTA + online mascot, 3D hero mascot, greeting, single h1, lavender quick chips, and Continue de onde parou section', async () => {
    window.localStorage.setItem('orin.user.displayName', 'Carlos');
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
    expect(within(header).getByTestId('home-topbar-title')).toHaveTextContent('Início');
    expect(header.querySelector('.model-picker')).not.toBeNull();
    expect(container.querySelectorAll('.model-picker').length).toBe(1);

    const voiceCta = within(header).getByTestId('home-voice-cta');
    expect(voiceCta).toHaveTextContent('Conversar por voz');

    const topbarMascot = header.querySelector('[data-testid="orin-mascot"]');
    expect(topbarMascot).not.toBeNull();
    expect(topbarMascot).toHaveAttribute('data-variant', 'flat');
    expect(topbarMascot).toHaveAttribute('data-status-dot', 'none');
    expect(header.querySelector('[data-testid="orin-activity-center-trigger"]')).not.toBeNull();

    const heroMascotWrap = screen.getByTestId('home-hero-mascot');
    const heroMascot = within(heroMascotWrap).getByTestId('orin-mascot');
    expect(heroMascot).toHaveAttribute('data-variant', 'soft3d');
    expect(heroMascot.querySelector('svg')).toHaveAttribute('width', '88');

    const greeting = screen.getByTestId('home-greeting');
    expect(greeting.textContent).toMatch(/^Bom dia, Carlos\.|^Boa tarde, Carlos\.|^Boa noite, Carlos\.$/);

    expect(
      await screen.findByRole('heading', { level: 1, name: 'O que vamos fazer hoje?' }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);

    const messageInput = screen.getByRole('textbox', { name: 'Mensagem' });
    expect(messageInput).toHaveAttribute('placeholder', 'Peça algo ou continue uma ideia...');

    const quickActions = screen.getByTestId('home-quick-actions');
    const synthChip = within(quickActions).getByRole('button', {
      name: 'Pesquisar um assunto (Pesquisar e sintetizar)',
    });
    const analyzeChip = within(quickActions).getByRole('button', {
      name: 'Analisar um arquivo (Analisar arquivos ou imagens)',
    });
    const projectChip = within(quickActions).getByRole('button', {
      name: 'Criar um projeto (Usar uma Skill da Biblioteca)',
    });
    expect(synthChip).toHaveTextContent('Pesquisar um assunto');
    expect(analyzeChip).toHaveTextContent('Analisar um arquivo');
    expect(projectChip).toHaveTextContent('Criar um projeto');
    expect(synthChip.querySelector('svg')).not.toBeNull();
    expect(analyzeChip.querySelector('svg')).not.toBeNull();
    expect(projectChip.querySelector('svg')).not.toBeNull();

    await user.click(synthChip);
    expect(messageInput).toHaveValue('Pesquise e sintetize sobre: ');

    const continueSection = await screen.findByTestId('home-continue-section');
    expect(within(continueSection).getByText('Continue de onde parou')).toBeInTheDocument();
    expect(within(continueSection).getByText(/Ver atividade/i)).toBeInTheDocument();
    const recentCard = within(continueSection).getByRole('button', { name: /Conversa antiga/i });
    expect(recentCard).toHaveClass('home__continue-card');
    expect(recentCard).toHaveTextContent('›');

    await user.click(screen.getByTestId('home-mode-code'));
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Qual repositório vamos construir ou evoluir?',
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);

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

  it('formatHomeGreeting handles time ranges and adversarial (Red-Team) inputs safely', () => {
    expect(formatHomeGreeting(5, 'Carlos')).toBe('Bom dia, Carlos.');
    expect(formatHomeGreeting(11, 'Carlos')).toBe('Bom dia, Carlos.');
    expect(formatHomeGreeting(12, 'Carlos')).toBe('Boa tarde, Carlos.');
    expect(formatHomeGreeting(17, 'Carlos')).toBe('Boa tarde, Carlos.');
    expect(formatHomeGreeting(18, 'Carlos')).toBe('Boa noite, Carlos.');
    expect(formatHomeGreeting(0, 'Carlos')).toBe('Boa noite, Carlos.');
    expect(formatHomeGreeting(4, 'Carlos')).toBe('Boa noite, Carlos.');

    // Red-Team: empty, whitespace, control chars, > 60 chars, invalid hours
    expect(formatHomeGreeting(14, '')).toBe('Boa tarde.');
    expect(formatHomeGreeting(14, '   \n\t  ')).toBe('Boa tarde.');
    expect(formatHomeGreeting(14, null)).toBe('Boa tarde.');
    expect(formatHomeGreeting(9, 'A'.repeat(100))).toBe(`Bom dia, ${'A'.repeat(60)}.`);
    expect(formatHomeGreeting(-5, 'Carlos')).toBe('Boa noite, Carlos.');
    expect(formatHomeGreeting(25, 'Carlos')).toBe('Boa noite, Carlos.');
    expect(formatHomeGreeting(Number.NaN, 'Carlos')).toBe('Boa noite, Carlos.');
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

    await screen.findByRole('heading', { name: 'O que vamos fazer hoje?' });
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
      expect(header.querySelector('.model-picker__provider-text')).not.toBeNull();
      expect(header.querySelector('.model-picker__model-text')).not.toBeNull();
      expect(header.querySelector('.context-indicator__label')).not.toBeNull();
      expect(container.querySelector('.workspace-navigation__mobile-trigger-label')).not.toBeNull();
      const filesBtn = screen.getByTestId('workspace-files-drawer-toggle');
      expect(filesBtn.querySelector('.chat__action-icon')).not.toBeNull();
      expect(filesBtn.querySelector('.chat__action-label')).toHaveTextContent('Arquivos');
      const overviewBtn = screen.getByRole('button', { name: /Visão geral/i });
      expect(overviewBtn.querySelector('.chat__action-icon')).not.toBeNull();
      expect(overviewBtn.querySelector('.chat__action-label')).toHaveTextContent('Visão geral');
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
