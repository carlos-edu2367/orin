import { useLocation } from 'react-router-dom'
import { PROVIDER_NAMES, type ProviderName } from '../../api/providers'
import { useSession } from '../../app/useSession'
import { ProviderCard, type ProviderCardState } from './ProviderCard'

export type ProviderCardStates = Record<ProviderName, ProviderCardState>

export function ProviderGrid({ states }: { states: ProviderCardStates }) {
  const pathname = useLocation().pathname
  const { capabilities } = useSession()
  const current = pathname.startsWith('/settings/providers/') ? pathname.split('/').at(-1) : null
  return <div className="provider-grid" aria-label="Providers">
    {PROVIDER_NAMES.filter((provider) => provider !== 'omniroute' || capabilities.omniroute).map((provider, index) => <ProviderCard key={provider} provider={provider} state={states[provider]} index={index} current={current === provider} />)}
  </div>
}
