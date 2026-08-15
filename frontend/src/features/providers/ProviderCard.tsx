import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import type { ProviderName } from '../../api/providers'
import { providerBrand } from './providerBrand'

export type ProviderCardState = { status: 'configured' | 'unconfigured' | 'unavailable'; detail: string }

const STATUS_LABEL: Record<ProviderCardState['status'], string> = {
  configured: 'Configurado', unconfigured: 'Não configurado', unavailable: 'Indisponível',
}

export function ProviderCard({ provider, state, index, current }: { provider: ProviderName; state: ProviderCardState; index: number; current: boolean }) {
  const brand = providerBrand(provider)
  return <Link
    to={`/settings/providers/${provider}`}
    className={`provider-card is-${state.status}`}
    style={{ '--card-index': index, '--card-accent': brand.accent } as CSSProperties}
    aria-current={current ? 'true' : undefined}
  >
    <span className="provider-card__mark" aria-hidden="true" dangerouslySetInnerHTML={{ __html: brand.mark }} />
    <strong className="provider-card__name">{brand.label}</strong>
    <span className="provider-card__status">{state.detail || STATUS_LABEL[state.status]}</span>
  </Link>
}
