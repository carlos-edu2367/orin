import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { FriendMemoryEntry, FriendPersonaState } from '../../../api/conversations'
import { FriendSoulDrawer, PERSONALITY_PRESETS } from '../FriendSoulDrawer'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

const SAMPLE_PERSONA: FriendPersonaState = {
  traits: {
    warmth: 0.72,
    humor: 0.58,
    sarcasm: 0.25,
    formality: 0.3,
    verbosity: 0.4,
    candor: 0.75,
    energy: 0.6,
  },
  affinity_score: 68.5,
  bond_stage: 'close_friend',
  style_notes: ['Prefere respostas diretas'],
  current_mood: 'focado',
  soul_markdown_path: 'C:/Users/User/.orin/friend/SOUL.md',
  updated_at: '2026-10-27T15:00:00Z',
  display_name: 'Orin',
  user_nickname: 'Carlos',
  orb_theme: 'aurora',
}

const SAMPLE_MEMORIES: FriendMemoryEntry[] = [
  {
    id: 'mem-1',
    kind: 'inside_joke',
    content: 'Nunca confie num regex escrito às 3h da manhã',
    importance: 0.9,
    emotion_tag: 'humor',
    created_at: '2026-10-25T10:00:00Z',
  },
]

describe('FriendSoulDrawer — Visão "Seu Friend" (ref-06.png)', () => {
  it('renderiza cabeçalho h1 "Seu Friend", botão "Conversar por voz", 3 abas horizontais e preserva exatamente um único h2 no card de preview', () => {
    const onStartVoice = vi.fn()
    const onClose = vi.fn()

    render(
      <FriendSoulDrawer
        isOpen={true}
        persona={SAMPLE_PERSONA}
        memories={SAMPLE_MEMORIES}
        onClose={onClose}
        onStartVoice={onStartVoice}
        onUpdatePersona={vi.fn()}
        onSyncSoul={vi.fn()}
        onDeleteMemory={vi.fn()}
      />,
    )

    // Cabeçalho principal h1 + subtítulo
    const h1 = screen.getByRole('heading', { level: 1 })
    expect(h1.textContent).toBe('Seu Friend')
    expect(screen.getByText('Personalize como o Orin conversa e te acompanha.')).toBeTruthy()

    // Regra estrita: exatamente UM <h2> em todo o componente, sendo o nome do Friend no preview card
    const allH2 = screen.getAllByRole('heading', { level: 2 })
    expect(allH2).toHaveLength(1)
    expect(allH2[0]).toBe(screen.getByTestId('friend-preview-name'))
    expect(allH2[0].textContent).toBe('Orin')

    // Botão Conversar por voz no topo direito
    const startVoiceBtn = screen.getByTestId('friend-start-voice-button')
    expect(startVoiceBtn.textContent).toContain('Conversar por voz')
    fireEvent.click(startVoiceBtn)
    expect(onStartVoice).toHaveBeenCalledTimes(1)

    // 3 abas horizontais com Personalidade ativa por padrão
    const tablist = screen.getByTestId('friend-personalize-tabs')
    expect(tablist.getAttribute('role')).toBe('tablist')

    const tabAppearance = screen.getByTestId('friend-tab-appearance')
    const tabVoice = screen.getByTestId('friend-tab-voice')
    const tabPersonality = screen.getByTestId('friend-tab-personality')

    expect(tabAppearance.getAttribute('role')).toBe('tab')
    expect(tabAppearance.textContent).toContain('Aparência')
    expect(tabAppearance.getAttribute('aria-selected')).toBe('false')

    expect(tabVoice.getAttribute('role')).toBe('tab')
    expect(tabVoice.textContent).toContain('Voz')
    expect(tabVoice.getAttribute('aria-selected')).toBe('false')

    expect(tabPersonality.getAttribute('role')).toBe('tab')
    expect(tabPersonality.textContent).toContain('Personalidade')
    expect(tabPersonality.getAttribute('aria-selected')).toBe('true')
    expect((screen.getByTestId('friend-more-settings') as HTMLDetailsElement).open).toBe(false)

    // Alternar abas atualiza aria-selected e abre Mais ajustes sem desmontar nem duplicar testids
    fireEvent.click(tabAppearance)
    expect(tabAppearance.getAttribute('aria-selected')).toBe('true')
    expect(tabPersonality.getAttribute('aria-selected')).toBe('false')
    expect((screen.getByTestId('friend-more-settings') as HTMLDetailsElement).open).toBe(true)

    fireEvent.click(tabVoice)
    expect(tabVoice.getAttribute('aria-selected')).toBe('true')
    expect(screen.getAllByTestId('friend-trait-slider-warmth')).toHaveLength(1)
    expect(screen.getAllByTestId('friend-theme-ocean')).toHaveLength(1)
  })

  it('exibe na coluna esquerda o card de identidade com OrinMascot soft3d (160px), LiveOrb, subtítulo "Seu parceiro de trabalho." e card de vínculo', () => {
    render(
      <FriendSoulDrawer
        isOpen={true}
        persona={SAMPLE_PERSONA}
        memories={SAMPLE_MEMORIES}
        onClose={vi.fn()}
        onUpdatePersona={vi.fn()}
        onSyncSoul={vi.fn()}
        onDeleteMemory={vi.fn()}
      />,
    )

    const previewCard = screen.getByTestId('friend-preview-card')
    const mascotsInPreview = within(previewCard).getAllByTestId('orin-mascot')
    expect(mascotsInPreview.some((el) => el.getAttribute('data-variant') === 'soft3d')).toBe(true)

    expect(within(previewCard).getByTestId('friend-preview-name').textContent).toBe('Orin')
    expect(within(previewCard).getByText('Seu parceiro de trabalho.')).toBeTruthy()
    expect(within(previewCard).getByTestId('friend-listen-preview-button').textContent).toContain('Ouvir uma prévia')
    expect(within(previewCard).getByTestId('friend-bond-card')).toBeTruthy()
    expect(within(previewCard).getByTestId('friend-bond-stage').textContent).toBe('Amigo próximo')
    expect(within(previewCard).getByTestId('friend-affinity-score').textContent).toContain('69%')
  })

  it('exibe na coluna direita os campos de Identidade com hints e os 5 sliders bipolares visíveis fora do <details>, além do acordeão "Mais ajustes"', () => {
    render(
      <FriendSoulDrawer
        isOpen={true}
        persona={SAMPLE_PERSONA}
        memories={SAMPLE_MEMORIES}
        onClose={vi.fn()}
        onUpdatePersona={vi.fn()}
        onSyncSoul={vi.fn()}
        onDeleteMemory={vi.fn()}
      />,
    )

    // Campos de Identidade e dicas
    expect(screen.getByText('Nome do Friend')).toBeTruthy()
    expect(screen.getByText('Como você quer chamar seu Friend?')).toBeTruthy()
    expect(screen.getByText('Como te chamar')).toBeTruthy()
    expect(screen.getByText('Qual nome o Orin deve usar para se referir a você?')).toBeTruthy()

    // Seção Personalidade e subtítulo
    expect(screen.getByText('Ajuste o estilo de conversa do Orin para combinar com o seu jeito de trabalhar.')).toBeTruthy()

    const moreSettings = screen.getByTestId('friend-more-settings')
    expect(moreSettings.tagName.toLowerCase()).toBe('details')
    expect(within(moreSettings).getByText('Mais ajustes')).toBeTruthy()
    expect(within(moreSettings).getByText('Preferências avançadas de linguagem, foco e contexto.')).toBeTruthy()

    // Os 5 sliders bipolares principais estão FORA de <details data-testid="friend-more-settings">
    const primarySliders = [
      {
        testId: 'friend-trait-slider-warmth',
        label: 'Calor humano',
        low: 'Mais reservado',
        high: 'Mais caloroso',
        min: '0.3',
        max: '0.95',
      },
      {
        testId: 'friend-trait-slider-humor',
        label: 'Bom humor',
        low: 'Mais sério',
        high: 'Mais bem-humorado',
        min: '0.1',
        max: '0.9',
      },
      {
        testId: 'friend-trait-slider-formality',
        label: 'Formalidade',
        low: 'Mais casual',
        high: 'Mais formal',
        min: '0.05',
        max: '0.85',
      },
      {
        testId: 'friend-trait-slider-candor',
        label: 'Diretividade',
        low: 'Mais exploratório',
        high: 'Mais direto',
        min: '0.5',
        max: '0.95',
      },
      {
        testId: 'friend-trait-slider-energy',
        label: 'Energia',
        low: 'Mais tranquilo',
        high: 'Mais energético',
        min: '0.2',
        max: '0.9',
      },
    ]

    for (const item of primarySliders) {
      const allMatches = screen.getAllByTestId(item.testId)
      expect(allMatches).toHaveLength(1)
      const slider = allMatches[0] as HTMLInputElement
      expect(moreSettings.contains(slider)).toBe(false)
      expect(slider.min).toBe(item.min)
      expect(slider.max).toBe(item.max)
      expect(screen.getByText(item.label)).toBeTruthy()
      expect(screen.getByText(item.low)).toBeTruthy()
      expect(screen.getByText(item.high)).toBeTruthy()
    }

    // Os 2 sliders complementares (sarcasm e verbosity) estão DENTRO de <details data-testid="friend-more-settings">
    expect(moreSettings.contains(screen.getByTestId('friend-trait-slider-sarcasm'))).toBe(true)
    expect(moreSettings.contains(screen.getByTestId('friend-trait-slider-verbosity'))).toBe(true)
    expect(moreSettings.contains(screen.getByTestId('friend-preset-animado'))).toBe(true)
    expect(moreSettings.contains(screen.getByTestId('friend-theme-ocean'))).toBe(true)
    expect(moreSettings.contains(screen.getByTestId('friend-mood-input'))).toBe(true)
    expect(moreSettings.contains(screen.getByTestId('friend-style-notes-textarea'))).toBe(true)
    expect(moreSettings.contains(screen.getByTestId('friend-memory-item-mem-1'))).toBe(true)
    expect(moreSettings.contains(screen.getByTestId('friend-soul-path'))).toBe(true)
  })

  it('Red-Team: bloqueia cliques simultâneos em "▷ Ouvir uma prévia" e cancela síntese/timer ao fechar (isOpen=false) ou iniciar conversa por voz', () => {
    vi.useFakeTimers()
    const speakMock = vi.fn()
    const cancelMock = vi.fn()

    class FakeUtterance {
      text: string
      lang = ''
      onend: (() => void) | null = null
      onerror: (() => void) | null = null
      constructor(text: string) {
        this.text = text
      }
    }

    vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance)
    Object.defineProperty(window, 'SpeechSynthesisUtterance', {
      value: FakeUtterance,
      configurable: true,
      writable: true,
    })
    Object.defineProperty(window, 'speechSynthesis', {
      value: {
        speak: speakMock,
        cancel: cancelMock,
      },
      configurable: true,
      writable: true,
    })

    const onStartVoice = vi.fn()
    const { rerender } = render(
      <FriendSoulDrawer
        isOpen={true}
        persona={SAMPLE_PERSONA}
        memories={[]}
        onClose={vi.fn()}
        onStartVoice={onStartVoice}
        onUpdatePersona={vi.fn()}
        onSyncSoul={vi.fn()}
        onDeleteMemory={vi.fn()}
      />,
    )

    const previewBtn = screen.getByTestId('friend-listen-preview-button') as HTMLButtonElement
    expect(previewBtn.disabled).toBe(false)
    expect(previewBtn.getAttribute('data-busy')).toBe('false')

    // Clique duplo rápido: deve sintetizar apenas 1 vez e travar o botão imediatamente
    fireEvent.click(previewBtn)
    fireEvent.click(previewBtn)

    expect(previewBtn.disabled).toBe(true)
    expect(previewBtn.getAttribute('data-busy')).toBe('true')
    expect(previewBtn.textContent).toContain('Ouvindo prévia…')
    expect(speakMock).toHaveBeenCalledTimes(1)
    const utterance = speakMock.mock.calls[0][0] as FakeUtterance
    expect(utterance.lang).toBe('pt-BR')
    expect(utterance.text).toContain('Orin')

    // Após o timer seguro (600ms), libera o botão mesmo sem evento onend no JSDOM
    act(() => {
      vi.advanceTimersByTime(650)
    })
    expect(previewBtn.disabled).toBe(false)
    expect(previewBtn.getAttribute('data-busy')).toBe('false')

    // Inicia nova prévia e fecha o drawer (isOpen = false) no meio: deve cancelar imediatamente
    cancelMock.mockClear()
    fireEvent.click(previewBtn)
    expect(previewBtn.disabled).toBe(true)

    rerender(
      <FriendSoulDrawer
        isOpen={false}
        persona={SAMPLE_PERSONA}
        memories={[]}
        onClose={vi.fn()}
        onStartVoice={onStartVoice}
        onUpdatePersona={vi.fn()}
        onSyncSoul={vi.fn()}
        onDeleteMemory={vi.fn()}
      />,
    )
    expect(cancelMock).toHaveBeenCalled()

    // Reabre: o botão não pode estar preso em estado ocupado
    rerender(
      <FriendSoulDrawer
        isOpen={true}
        persona={SAMPLE_PERSONA}
        memories={[]}
        onClose={vi.fn()}
        onStartVoice={onStartVoice}
        onUpdatePersona={vi.fn()}
        onSyncSoul={vi.fn()}
        onDeleteMemory={vi.fn()}
      />,
    )
    const reopenedPreviewBtn = screen.getByTestId('friend-listen-preview-button') as HTMLButtonElement
    expect(reopenedPreviewBtn.disabled).toBe(false)
    expect(reopenedPreviewBtn.getAttribute('data-busy')).toBe('false')

    // Inicia prévia e clica em "Conversar por voz": cancela prévia e chama onStartVoice
    cancelMock.mockClear()
    fireEvent.click(reopenedPreviewBtn)
    expect(reopenedPreviewBtn.disabled).toBe(true)
    fireEvent.click(screen.getByTestId('friend-start-voice-button'))
    expect(cancelMock).toHaveBeenCalled()
    expect(onStartVoice).toHaveBeenCalledTimes(1)
  })

  it('Red-Team: "↺ Restaurar padrão" restaura DEFAULT_TRAITS, tema aurora, humor sereno e recupera nome válido quando o campo estava em branco', async () => {
    const onUpdatePersona = vi.fn().mockResolvedValue(SAMPLE_PERSONA)

    render(
      <FriendSoulDrawer
        isOpen={true}
        persona={SAMPLE_PERSONA}
        memories={[]}
        onClose={vi.fn()}
        onUpdatePersona={onUpdatePersona}
        onSyncSoul={vi.fn()}
        onDeleteMemory={vi.fn()}
      />,
    )

    // Limpa o nome (inválido), altera tema e preset, e tenta salvar (gera erro)
    fireEvent.change(screen.getByTestId('friend-name-input'), { target: { value: '<script>' } })
    fireEvent.click(screen.getByTestId('friend-theme-ocean'))
    fireEvent.click(screen.getByTestId('friend-preset-animado'))
    fireEvent.click(screen.getByTestId('save-friend-persona-button'))
    expect(screen.getByTestId('friend-save-status').textContent).toContain('Evite os caracteres')

    // Clica em "↺ Restaurar padrão"
    const restoreBtn = screen.getByTestId('friend-restore-defaults-button')
    expect(restoreBtn.textContent).toContain('Restaurar padrão')
    fireEvent.click(restoreBtn)

    // Nome voltou a ser válido ('Orin'), traços voltaram para DEFAULT_TRAITS e botão Salvar está habilitado
    expect((screen.getByTestId('friend-name-input') as HTMLInputElement).value).toBe('Orin')
    expect((screen.getByTestId('friend-mood-input') as HTMLInputElement).value).toBe('sereno')
    expect((screen.getByTestId('friend-style-notes-textarea') as HTMLTextAreaElement).value).toBe('')
    expect(screen.getByTestId('friend-theme-aurora').getAttribute('aria-checked')).toBe('true')
    expect((screen.getByTestId('save-friend-persona-button') as HTMLButtonElement).disabled).toBe(false)

    // Salva e verifica que os traits enviados são exatamente DEFAULT_TRAITS
    fireEvent.click(screen.getByTestId('save-friend-persona-button'))
    await waitFor(() => expect(onUpdatePersona).toHaveBeenCalledTimes(1))
    expect(onUpdatePersona.mock.calls[0][0]).toEqual({
      traits: PERSONALITY_PRESETS[0].traits,
      style_notes: [],
      current_mood: 'sereno',
      display_name: 'Orin',
      user_nickname: 'Carlos',
      orb_theme: 'aurora',
    })
  })
})
