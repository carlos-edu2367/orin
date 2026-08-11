export type OmniRouteFreeProviderGuide = {
  id: string
  name: string
  access: 'Sem autenticação' | 'OAuth'
  summary: string
  steps: readonly string[]
  source: string
  lastVerifiedAt: string
}

// Kept in one reviewable place: these are documented OmniRoute integration
// paths, not quota promises. The gateway remains the source of current models.
export const OMNIROUTE_FREE_PROVIDER_GUIDES: readonly OmniRouteFreeProviderGuide[] = [
  {
    id: 'opencode-free', name: 'OpenCode Free', access: 'Sem autenticação',
    summary: 'Use os modelos gratuitos que aparecerem no catálogo do OmniRoute.',
    steps: ['Ative o provider sem autenticação no dashboard do OmniRoute.', 'Atualize o catálogo no Orin e escolha um modelo oc/... disponível.'],
    source: 'https://github.com/diegosouzapw/OmniRoute/blob/main/README.md', lastVerifiedAt: '2026-08-10',
  },
  {
    id: 'qoder', name: 'Qoder', access: 'OAuth',
    summary: 'Conecte a sua própria conta através do OAuth oficial do OmniRoute.',
    steps: ['No dashboard do OmniRoute, escolha Connect Qoder.', 'Conclua o login oficial; nunca forneça sua senha ao Orin.', 'Atualize o catálogo aqui.'],
    source: 'https://github.com/diegosouzapw/OmniRoute/blob/main/docs/guides/USER_GUIDE.md', lastVerifiedAt: '2026-08-10',
  },
  {
    id: 'kiro', name: 'Kiro', access: 'OAuth',
    summary: 'Conecte uma AWS Builder ID, Google ou GitHub pela página oficial.',
    steps: ['No dashboard do OmniRoute, escolha Connect Kiro.', 'Conclua OAuth com a sua própria conta.', 'Confira disponibilidade e limites no provider antes de selecionar um modelo.'],
    source: 'https://github.com/diegosouzapw/OmniRoute/blob/main/docs/guides/USER_GUIDE.md', lastVerifiedAt: '2026-08-10',
  },
]
