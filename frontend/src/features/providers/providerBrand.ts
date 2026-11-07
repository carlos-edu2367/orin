import anthropicLogo from '../../assets/providers/anthropic.svg'
import anthropicMark from '../../assets/providers/anthropic.svg?raw'
import nvidiaLogo from '../../assets/providers/nvidia.svg'
import nvidiaMark from '../../assets/providers/nvidia.svg?raw'
import ollamaLogo from '../../assets/providers/ollama.svg'
import ollamaMark from '../../assets/providers/ollama.svg?raw'
import omnirouteLogo from '../../assets/providers/omniroute.svg'
import omnirouteMark from '../../assets/providers/omniroute.svg?raw'
import openaiLogo from '../../assets/providers/openai.svg'
import openaiMark from '../../assets/providers/openai.svg?raw'
import opencodeLogo from '../../assets/providers/opencode.svg'
import opencodeMark from '../../assets/providers/opencode.svg?raw'
import openrouterLogo from '../../assets/providers/openrouter.svg'
import openrouterMark from '../../assets/providers/openrouter.svg?raw'
import { PROVIDER_NAMES, type ProviderName } from '../../api/providers'

export const PROVIDER_ORDER: readonly ProviderName[] = PROVIDER_NAMES

export type ProviderBrand = {
  id: ProviderName
  name: string
  label: string
  accent: string
  mark: string
  logoUrl: string
  keyPlaceholder: string


  tagline?: string
  keyHint?: string
  docsLabel?: string
  docsUrl?: string
  supportsBaseUrl?: boolean
  keyOptional?: boolean
}

const FALLBACK_MARK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="4" /></svg>'

export const BRANDS: Record<ProviderName, ProviderBrand> = {
  openai: {
    id: 'openai',
    name: 'OpenAI',
    label: 'OpenAI',
    accent: '#74d3b0',
    mark: openaiMark,
    logoUrl: openaiLogo,
    keyPlaceholder: 'sk-...',
    docsUrl: 'https://platform.openai.com/api-keys',
    supportsBaseUrl: false,
    keyOptional: false,
  },
  'openai-codex': {
    id: 'openai-codex',
    name: 'Codex (Assinatura)',
    label: 'Codex (Assinatura)',
    tagline: 'Use sua assinatura ChatGPT Plus, Pro, Business, Edu ou Enterprise via Codex CLI local.',
    accent: '#10a37f',
    keyHint: 'Sem chave de API (login via codex login)',
    keyPlaceholder: 'Sem chave de API (login via codex login)',
    docsLabel: 'Documentação do Codex CLI',
    docsUrl: 'https://developers.openai.com/codex/cli',
    logoUrl: openaiLogo,
    mark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 10l2.5 2L7 14"/><path d="M11.5 14H15"/></svg>',
    supportsBaseUrl: false,
    keyOptional: true,
  },
  'google-antigravity': {
    id: 'google-antigravity',
    name: 'Google Antigravity (Assinatura)',
    label: 'Google Antigravity (Assinatura)',
    tagline: 'Use sua assinatura Google via Antigravity CLI (agy) local.',
    accent: '#4285f4',
    keyHint: 'Sem chave de API (login via agy)',
    keyPlaceholder: 'Sem chave de API (login via agy)',
    docsLabel: 'FAQ oficial do Antigravity',
    docsUrl: 'https://antigravity.google/docs/faq/',
    logoUrl: '',
    mark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l6.5 16h-2.8l-1.5-3.8H9.8L8.3 19H5.5L12 3z"/><path d="M10.7 12.6h2.6"/><circle cx="12" cy="19.5" r="1"/></svg>',
    supportsBaseUrl: false,
    keyOptional: true,
  },
  anthropic: {
    id: 'anthropic',
    name: 'Anthropic',
    label: 'Anthropic',
    accent: '#e8a06a',
    mark: anthropicMark,
    logoUrl: anthropicLogo,
    keyPlaceholder: 'sk-ant-...',
    docsUrl: 'https://console.anthropic.com/settings/keys',
    supportsBaseUrl: true,
    keyOptional: true,
  },
  openrouter: {
    id: 'openrouter',
    name: 'OpenRouter',
    label: 'OpenRouter',
    accent: '#8fb8f0',
    mark: openrouterMark,
    logoUrl: openrouterLogo,
    keyPlaceholder: 'sk-or-...',
    docsUrl: 'https://openrouter.ai/keys',
    supportsBaseUrl: false,
    keyOptional: false,
  },
  nvidia: {
    id: 'nvidia',
    name: 'NVIDIA NIM',
    label: 'NVIDIA NIM',
    accent: '#76B900',
    mark: nvidiaMark,
    logoUrl: nvidiaLogo,
    keyPlaceholder: 'nvapi-...',
    docsUrl: 'https://build.nvidia.com',
    supportsBaseUrl: true,
    keyOptional: true,
  },
  opencode: {
    id: 'opencode',
    name: 'OpenCode',
    label: 'OpenCode',
    accent: '#62d8f3',
    mark: opencodeMark,
    logoUrl: opencodeLogo,
    keyPlaceholder: 'oc-...',
    docsUrl: 'https://opencode.ai',
    supportsBaseUrl: true,
    keyOptional: true,
  },
  omniroute: {
    id: 'omniroute',
    name: 'OmniRoute',
    label: 'OmniRoute',
    accent: '#c79cf5',
    mark: omnirouteMark,
    logoUrl: omnirouteLogo,
    keyPlaceholder: 'Opcional',
    supportsBaseUrl: true,
    keyOptional: true,
  },
  ollama: {
    id: 'ollama',
    name: 'Ollama',
    label: 'Ollama',
    accent: '#e6e2f0',
    mark: ollamaMark,
    logoUrl: ollamaLogo,
    keyPlaceholder: 'Opcional no modo local',
    docsUrl: 'https://ollama.com/settings/keys',
    supportsBaseUrl: true,
    keyOptional: true,
  },
}

export function providerBrand(provider: ProviderName): ProviderBrand {
  return (
    BRANDS[provider] ?? {
      id: provider,
      name: String(provider),
      label: String(provider),
      accent: '#9a94ad',
      mark: FALLBACK_MARK,
      logoUrl: '',
      keyPlaceholder: 'Inserir uma nova chave',
    }
  )
}
