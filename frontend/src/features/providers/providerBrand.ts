import anthropicMark from '../../assets/providers/anthropic.svg?raw'
import ollamaMark from '../../assets/providers/ollama.svg?raw'
import omnirouteMark from '../../assets/providers/omniroute.svg?raw'
import openaiMark from '../../assets/providers/openai.svg?raw'
import openrouterMark from '../../assets/providers/openrouter.svg?raw'
import type { ProviderName } from '../../api/providers'

export type ProviderBrand = { label: string; accent: string; mark: string }

const FALLBACK_MARK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="4" /></svg>'

const BRANDS: Record<ProviderName, ProviderBrand> = {
  openai: { label: 'OpenAI', accent: '#74d3b0', mark: openaiMark },
  anthropic: { label: 'Anthropic', accent: '#e8a06a', mark: anthropicMark },
  openrouter: { label: 'OpenRouter', accent: '#8fb8f0', mark: openrouterMark },
  omniroute: { label: 'OmniRoute', accent: '#c79cf5', mark: omnirouteMark },
  ollama: { label: 'Ollama', accent: '#e6e2f0', mark: ollamaMark },
}

export function providerBrand(provider: ProviderName): ProviderBrand {
  return BRANDS[provider] ?? { label: String(provider), accent: '#9a94ad', mark: FALLBACK_MARK }
}
