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

import { fireEvent, waitFor } from '@testing-library/react';

describe('Oversized prompt automatic .txt conversion in Home and ChatPage', () => {
  it('automatically converts prompts > 16000 chars into prompt.txt and starts conversation in Home without error', async () => {
    const uploadedFiles: Array<{ name: string; size: number }> = [];
    const createdPayloads: Array<{ message?: string; attachments?: string[] }> = [];

    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input);
      const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      if (url.includes('/models')) return json({ items: [MODEL] });
      if (url.includes('/v1/uploads') && method === 'POST') {
        const body = init?.body as FormData | undefined;
        const file = body?.get('file') as File | null;
        if (file) {
          uploadedFiles.push({ name: file.name, size: file.size });
        }
        return json({
          upload_id: 'up_auto_home_txt',
          original_name: file?.name ?? 'prompt.txt',
          media_type: 'text/plain',
          kind: 'text',
          bytes: file?.size ?? 18000,
        }, 201);
      }
      if (url.includes('/v1/conversations') && method === 'POST') {
        const rawBody = typeof init?.body === 'string' ? JSON.parse(init.body) : {};
        createdPayloads.push(rawBody);
        if (typeof rawBody.message === 'string' && rawBody.message.length > 16000) {
          return json({ error: { code: 'validation_error', message: 'message too long' } }, 422);
        }
        return json({
          conversation_id: 'chat_large_home',
          title: 'Projeto Ashy',
          turn_id: 'turn_1',
          message_id: 'msg_1',
          state: 'queued',
        }, 201);
      }
      if (url.includes('/v1/conversations')) return json({ items: [] });
      if (url.includes('/v1/projects/sidebar')) return json({ items: [] });
      if (url.includes('/v1/settings/code-mode')) {
        return json({ autonomy: 'balanced', system_notifications: false, monitoring_enabled: true });
      }
      return json({ items: [] });
    });

    const client = new ApiClient({ fetchImpl, maxAttempts: 1 });
    const router = createMemoryRouter(
      [
        { path: '/', element: <Home client={client} bootstrap={READY_SESSION} /> },
        { path: '/chats/:conversationId', element: <div data-testid="navigated-chat">Chat opened</div> },
      ],
      { initialEntries: ['/'] },
    );

    render(
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>,
    );

    const messageInput = await screen.findByRole('textbox', { name: 'Mensagem' });
    const largePrompt = `# PROJETO ASHY — Arquitetura de Fine-Tuning\n${'Linha detalhada do prompt para validar conversão automática em txt.\n'.repeat(300)}`;
    expect(largePrompt.length).toBeGreaterThan(16000);

    fireEvent.change(messageInput, { target: { value: largePrompt } });
    const sendBtn = screen.getByRole('button', { name: 'Enviar mensagem' });
    await waitFor(() => expect(sendBtn).not.toBeDisabled());
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(screen.getByTestId('navigated-chat')).toBeInTheDocument();
    });
    expect(uploadedFiles).toHaveLength(1);
    expect(uploadedFiles[0].name).toBe('prompt.txt');
    expect(createdPayloads).toHaveLength(1);
    expect(createdPayloads[0].attachments).toContain('up_auto_home_txt');
    expect(String(createdPayloads[0].message ?? '').length).toBeLessThanOrEqual(16000);
  });

  it('automatically converts prompts > 16000 chars into prompt.txt in ChatPage without opening input-limit-dialog', async () => {
    const originalFetch = globalThis.fetch;
    const uploadedFiles: Array<{ name: string; size: number }> = [];
    const sentMessagePayloads: Array<{ message?: string; attachments?: string[] }> = [];

    globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input);
      const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      if (url.includes('/events?')) {
        return new Response('event: heartbeat\ndata: {"cursor":"a.1"}\n\n', {
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      if (url.includes('/models')) return json({ items: [MODEL] });
      if (url.includes('/v1/uploads') && method === 'POST') {
        const body = init?.body as FormData | undefined;
        const file = body?.get('file') as File | null;
        if (file) {
          uploadedFiles.push({ name: file.name, size: file.size });
        }
        return json({
          upload_id: 'up_auto_chat_txt',
          original_name: file?.name ?? 'prompt.txt',
          media_type: 'text/plain',
          kind: 'text',
          bytes: file?.size ?? 18000,
        }, 201);
      }
      if (url.includes('/v1/conversations/conv_auto_txt/messages') && method === 'POST') {
        sentMessagePayloads.push(typeof init?.body === 'string' ? JSON.parse(init.body) : {});
        return json({
          conversation_id: 'conv_auto_txt',
          title: 'Chat',
          turn_id: 'turn_2',
          message_id: 'msg_2',
          state: 'queued',
        }, 202);
      }
      if (url.includes('/v1/conversations/conv_auto_txt')) {
        return json({
          conversation_id: 'conv_auto_txt',
          title: 'Chat',
          state: 'completed',
          mode: 'code',
          permission_mode: 'accept_edits',
          provider: 'openrouter',
          model_id: 'anthropic/model-a',
          messages: [],
          turns: [],
          activities: [],
          activity_cursor: 'a.1',
        });
      }
      return json({ items: [] });
    });

    try {
      render(
        <ThemeProvider>
          <MemoryRouter initialEntries={['/chats/conv_auto_txt']}>
            <Routes>
              <Route path="/chats/:conversationId" element={<ChatPage />} />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>,
      );

      const messageInput = await screen.findByRole('textbox', { name: 'Mensagem' });
      const largePrompt = `# PROJETO ASHY\n${'Conteúdo extenso para enviar automaticamente como txt no ChatPage.\n'.repeat(300)}`;
      expect(largePrompt.length).toBeGreaterThan(16000);

      fireEvent.change(messageInput, { target: { value: largePrompt } });
      const sendBtn = screen.getByRole('button', { name: 'Enviar mensagem' });
      await waitFor(() => expect(sendBtn).not.toBeDisabled());
      fireEvent.click(sendBtn);

      await waitFor(() => {
        expect(sentMessagePayloads).toHaveLength(1);
      });
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(uploadedFiles).toHaveLength(1);
      expect(uploadedFiles[0].name).toBe('prompt.txt');
      expect(sentMessagePayloads[0].attachments).toContain('up_auto_chat_txt');
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('cleans up the auto-uploaded prompt.txt if conversation creation fails in Home', async () => {
    const deletedUploads: string[] = [];
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : (input as Request).url ?? String(input);
      const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      if (url.includes('/models')) return json({ items: [MODEL] });
      if (url.includes('/v1/uploads/') && method === 'DELETE') {
        deletedUploads.push(url.split('/').at(-1) ?? '');
        return new Response(null, { status: 204 });
      }
      if (url.includes('/v1/uploads') && method === 'POST') {
        return json({
          upload_id: 'up_orphan_1',
          original_name: 'prompt.txt',
          media_type: 'text/plain',
          kind: 'text',
          bytes: 18000,
        }, 201);
      }
      if (url.includes('/v1/conversations') && method === 'POST') {
        return json({ error: { code: 'internal_error', message: 'boom' } }, 500);
      }
      if (url.includes('/v1/conversations')) return json({ items: [] });
      if (url.includes('/v1/projects/sidebar')) return json({ items: [] });
      if (url.includes('/v1/settings/code-mode')) {
        return json({ autonomy: 'balanced', system_notifications: false, monitoring_enabled: true });
      }
      return json({ items: [] });
    });

    const client = new ApiClient({ fetchImpl, maxAttempts: 1 });
    const router = createMemoryRouter(
      [{ path: '/', element: <Home client={client} bootstrap={READY_SESSION} /> }],
      { initialEntries: ['/'] },
    );

    render(
      <ThemeProvider>
        <RouterProvider router={router} />
      </ThemeProvider>,
    );

    const messageInput = await screen.findByRole('textbox', { name: 'Mensagem' });
    const largePrompt = 'A'.repeat(16500);
    fireEvent.change(messageInput, { target: { value: largePrompt } });
    const sendBtn = screen.getByRole('button', { name: 'Enviar mensagem' });
    await waitFor(() => expect(sendBtn).not.toBeDisabled());
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(deletedUploads).toContain('up_orphan_1');
    });
    expect(await screen.findByRole('alert')).toHaveTextContent(/O backend não conseguiu iniciar a conversa/i);
  });
});
