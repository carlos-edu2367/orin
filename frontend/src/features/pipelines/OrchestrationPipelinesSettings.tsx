import type { OrchestrationSettingItem } from '../../api/orchestrationSettings'
import { OrchestrationSettingsForm } from '../settings/OrchestrationSettingsForm'
import '../settings/orchestrationSettings.css'
import './pipelines.css'

export const PIPELINE_SETTING_PATHS = {
  switches: ['pipelines.enabled', 'pipelines.agentMayStart'],
  budget: ['orchestration.budget.subagent.maxTokens', 'orchestration.budget.subagent.maxCostUsd'],
  goal: ['goal.defaultMaxTokens'],
} as const

const ALL_PATHS: readonly string[] = [...PIPELINE_SETTING_PATHS.switches, ...PIPELINE_SETTING_PATHS.budget, ...PIPELINE_SETTING_PATHS.goal]

export function isPipelineSettingPath(path: string): boolean {
  return ALL_PATHS.includes(path)
}

function pick(items: readonly OrchestrationSettingItem[], paths: readonly string[]): OrchestrationSettingItem[] {
  return paths.map((path) => items.find((item) => item.path === path)).filter((item): item is OrchestrationSettingItem => Boolean(item))
}

/**
 * The "Pipelines" block of Configurações > Orquestração (0.13 WS-C11): the five keys of Package C, each with the layer its
 * effective value comes from. Everything saves to the person's own layer; a project can only lower the budget ceilings
 * and switch pipelines off.
 */
export function OrchestrationPipelinesSettings({
  items,
  busy = false,
  onSave,
}: {
  items: OrchestrationSettingItem[]
  busy?: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const switches = pick(items, PIPELINE_SETTING_PATHS.switches)
  const budget = pick(items, PIPELINE_SETTING_PATHS.budget)
  const goal = pick(items, PIPELINE_SETTING_PATHS.goal)
  if (switches.length + budget.length + goal.length === 0) {
    return <p className="code-settings__muted">Nenhuma chave de pipelines disponível nesta instância.</p>
  }
  const agentMayStart = switches.find((item) => item.path === 'pipelines.agentMayStart')
  const enabled = switches.find((item) => item.path === 'pipelines.enabled')

  return (
    <div className="orch-settings__block" data-testid="orchestration-pipelines-block">
      {switches.length > 0 && (
        <div className="pipeline-settings__sub">
          <OrchestrationSettingsForm items={switches} busy={busy} onSave={onSave} hideGroupHeaders />
          {enabled?.value === false && (
            <p className="pipeline-note pipeline-note--warn" role="note">Com pipelines desligados, o painel, os comandos /pipeline e a ferramenta RunPipeline ficam sem efeito. O /goal continua disponível.</p>
          )}
          <p className="pipeline-note pipeline-note--warn" role="note" data-testid="agent-may-start-warning">
            Gasto: com &quot;Agente pode iniciar pipeline&quot; ligado, o agente pode começar pipelines já aprovados sem você digitar um comando, e um pipeline gasta
            assinatura de vários agentes ao mesmo tempo. Cada início ainda passa pelo pedido de permissão{agentMayStart?.value === true ? ' (está ligado agora)' : ''}.
          </p>
        </div>
      )}
      {budget.length > 0 && (
        <div className="pipeline-settings__sub">
          <h4 className="orch-settings__group-title">Teto padrão por subagente</h4>
          <p className="code-settings__muted">Vale para qualquer subagente, também fora de pipelines. Deixe vazio para não limitar. Um projeto só pode reduzir estes tetos.</p>
          <OrchestrationSettingsForm items={budget} busy={busy} onSave={onSave} hideGroupHeaders />
        </div>
      )}
      {goal.length > 0 && (
        <div className="pipeline-settings__sub">
          <h4 className="orch-settings__group-title">/goal</h4>
          <p className="code-settings__muted">O padrão do teto de tokens quando o comando não traz --max-tokens (de 1.000 a 5.000.000 no comando). Só vale na sua camada de usuário.</p>
          <OrchestrationSettingsForm items={goal} busy={busy} onSave={onSave} hideGroupHeaders />
        </div>
      )}
    </div>
  )
}
