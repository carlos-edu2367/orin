import { useState } from 'react'
import type { FolderInspection, InspectionOutcome, WorkspaceRisk, WorkspaceState } from '../../api/workspace'

export type WorkspaceFolderButtonProps = {
  state: WorkspaceState
  onInspect: (path: string | null) => Promise<InspectionOutcome>
  onAttach: (path: string, acknowledgedRisk: boolean) => Promise<WorkspaceState>
  onDetach: () => Promise<WorkspaceState>
  onChange: (state: WorkspaceState) => void
}

const RISK_SENTENCE: Record<Exclude<WorkspaceRisk, 'none'>, string> = {
  drive_root: 'é um drive inteiro',
  system: 'é uma pasta de sistema',
  home_root: 'é a sua pasta pessoal inteira',
  orin_data: 'é a pasta de dados do próprio Orin',
}

/**
 * Attaching a folder is a small action with a large consequence, so the button
 * stays quiet and the panel does the talking. No folder is refused: a broad
 * choice only costs a second, named click, which is what keeps it deliberate
 * instead of accidental.
 */
export function WorkspaceFolderButton({ state, onInspect, onAttach, onDetach, onChange }: WorkspaceFolderButtonProps) {
  const [open, setOpen] = useState(false)
  const [candidate, setCandidate] = useState<FolderInspection | null>(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const label = state.kind === 'local' ? (state.folderName ?? 'Pasta') : 'Pasta'

  async function inspect(path: string | null) {
    setBusy(true); setError(null); setNotice(null)
    try {
      const outcome = await onInspect(path)
      if (outcome.kind === 'cancelled') return
      if (outcome.kind === 'unavailable') { setNotice('Não foi possível abrir o seletor do sistema. Cole o caminho da pasta abaixo.'); return }
      if (!outcome.isDirectory) { setError(outcome.exists ? 'Esse caminho não é uma pasta.' : 'Essa pasta não existe.'); return }
      if (!outcome.writable) { setError('Sem permissão de escrita nessa pasta.'); return }
      setCandidate(outcome)
    } catch {
      setError('Não foi possível inspecionar a pasta.')
    } finally {
      setBusy(false)
    }
  }

  async function attach(inspection: FolderInspection) {
    setBusy(true); setError(null)
    try {
      onChange(await onAttach(inspection.path, inspection.risk !== 'none'))
      setCandidate(null); setOpen(false)
    } catch {
      setError('Não foi possível usar essa pasta.')
    } finally {
      setBusy(false)
    }
  }

  async function detach() {
    setBusy(true); setError(null)
    try {
      onChange(await onDetach())
      setOpen(false)
    } catch {
      setError('Não foi possível remover a pasta.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="workspace-folder">
      <button
        type="button"
        className={`workspace-folder__button${state.kind === 'local' ? ' is-attached' : ''}`}
        onClick={() => setOpen((value) => !value)}
        title={state.path ?? undefined}
        aria-expanded={open}
      >
        <span aria-hidden="true">🗀</span> {label}
      </button>

      {open && (
        <div className="workspace-folder__panel" role="dialog" aria-label="Pasta de trabalho">
          {candidate ? (
            <>
              <p className="workspace-folder__path">{candidate.path}</p>
              <p className="workspace-folder__meta">{candidate.entryCount}{candidate.entriesTruncated ? '+' : ''} itens no primeiro nível</p>
              {state.scope === 'project' && state.projectName && (
                <p className="workspace-folder__meta">Vale para todos os chats do projeto {state.projectName}.</p>
              )}
              {candidate.risk === 'none' ? (
                <button type="button" disabled={busy} onClick={() => void attach(candidate)}>Usar esta pasta</button>
              ) : (
                <>
                  <p className="workspace-folder__risk">
                    Essa pasta {RISK_SENTENCE[candidate.risk]}. O agente vai poder criar, editar e apagar arquivos em {candidate.path}, com shell real, sem pedir permissão a cada passo.
                  </p>
                  <button type="button" className="workspace-folder__risk-action" disabled={busy} onClick={() => void attach(candidate)}>
                    Trabalhar em {candidate.path} mesmo assim
                  </button>
                </>
              )}
              <button type="button" disabled={busy} onClick={() => setCandidate(null)}>Cancelar</button>
            </>
          ) : (
            <>
              {state.kind === 'local' ? (
                <p className="workspace-folder__path">{state.path}</p>
              ) : (
                <p className="workspace-folder__meta">Sem pasta local. O agente trabalha na pasta gerenciada pelo Orin.</p>
              )}
              <button type="button" disabled={busy} onClick={() => void inspect(null)}>Escolher pasta…</button>
              <label className="workspace-folder__field">
                Caminho da pasta
                <input value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="D:\projetos\site" />
              </label>
              <button type="button" disabled={busy || !typed.trim()} onClick={() => void inspect(typed.trim())}>Usar</button>
              {state.kind === 'local' && <button type="button" disabled={busy} onClick={() => void detach()}>Remover</button>}
            </>
          )}
          {notice && <p className="workspace-folder__notice" role="status">{notice}</p>}
          {error && <p className="workspace-folder__error" role="alert">{error}</p>}
        </div>
      )}
    </div>
  )
}
