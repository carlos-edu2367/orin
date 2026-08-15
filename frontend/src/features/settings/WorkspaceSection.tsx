import { SettingsSection } from './SettingsSection'

export function WorkspaceSection() {
  return <SettingsSection eyebrow="SISTEMA / WORKSPACE">
    <section className="settings-card" aria-labelledby="workspace-settings-title">
      <h2 id="workspace-settings-title">Workspaces locais</h2>
      <p>O Orin mantém workspaces vinculados ao chat ou ao projeto. A escolha e a validação de uma pasta acontecem no contexto da conversa para preservar isolamento e confirmação de risco.</p>
      <p className="settings-card__muted">Nenhuma raiz global é configurada nesta tela.</p>
    </section>
  </SettingsSection>
}
