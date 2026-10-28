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
