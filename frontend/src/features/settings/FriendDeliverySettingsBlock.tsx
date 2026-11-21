import type { OrchestrationSettingItem } from '../../api/orchestrationSettings'
import { OrchestrationSettingsForm } from './OrchestrationSettingsForm'

export function FriendDeliverySettingsBlock({
  items,
  busy = false,
  onSave,
}: {
  items: OrchestrationSettingItem[]
  busy?: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const deliveryItems = items.filter(
    (item) => item.path === 'friend.sessionDelivery' || item.group === 'friend',
  )

  if (deliveryItems.length === 0) {
    return (
      <p className="code-settings__muted">
        Nenhuma chave do Orin Friend disponível nesta instância.
      </p>
    )
  }

  return (
    <div className="orch-settings__group">
      <OrchestrationSettingsForm
        items={deliveryItems}
        busy={busy}
        onSave={onSave}
        hideGroupHeaders
      />
      <p className="code-settings__muted">
        No modo <strong>now</strong> (padrão), o Friend envia a mensagem para o turno em andamento e a entrega acontece no próximo ponto seguro. No modo <strong>queue</strong>, a mensagem aguarda na fila para um novo turno.
      </p>
    </div>
  )
}
