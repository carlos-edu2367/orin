import { useRef, useState } from 'react'
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
  const [actionBusy, setActionBusy] = useState(false)
  const [manualInspecting, setManualInspecting] = useState(false)
  const [dialogBusy, setDialogBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inspectionRequest = useRef(0)

  const label = state.kind === 'local' ? (state.folderName || state.path || 'Pasta') : 'Pasta'

  async function inspect(path: string | null) {
    const requestId = ++inspectionRequest.current
    const nativeDialog = path === null
    if (nativeDialog) setDialogBusy(true)
    else setManualInspecting(true)
    setError(null); setNotice(null)
    try {
      const outcome = await onInspect(path)
      if (requestId !== inspectionRequest.current) return
      if (outcome.kind === 'cancelled') return
      if (outcome.kind === 'unavailable') { setNotice('Não foi possível abrir o seletor do sistema. Cole o caminho da pasta abaixo.'); return }
      if (!outcome.isDirectory) { setError(outcome.exists ? 'Esse caminho não é uma pasta.' : 'Essa pasta não existe.'); return }
      if (!outcome.writable) { setError('Sem permissão de escrita nessa pasta.'); return }
      setCandidate(outcome)
    } catch {
      if (requestId === inspectionRequest.current) setError('Não foi possível inspecionar a pasta.')
    } finally {
      if (nativeDialog) setDialogBusy(false)
      else setManualInspecting(false)
    }
  }

  async function attach(inspection: FolderInspection) {
    setActionBusy(true); setError(null)
    try {
      onChange(await onAttach(inspection.path, inspection.risk !== 'none'))
      setCandidate(null); setOpen(false)
    } catch {
      setError('Não foi possível usar essa pasta.')
    } finally {
      setActionBusy(false)
    }
  }

  async function detach() {
    setActionBusy(true); setError(null)
    try {
      onChange(await onDetach())
      setOpen(false)
    } catch {
      setError('Não foi possível remover a pasta.')
    } finally {
      setActionBusy(false)
    }
  }

  function toggleOpen() {
    setOpen((value) => {
      if (value) {
        // A native picker can outlive the panel. Its eventual response must
        // not reopen a candidate after the user has chosen another path.
        inspectionRequest.current += 1
        setDialogBusy(false)
        setManualInspecting(false)
        setCandidate(null)
      }
      return !value
    })
  }

  return (
    <div className="workspace-folder">
      <button
        type="button"
        className={`workspace-folder__button${state.kind === 'local' ? ' is-attached' : ''}`}
        onClick={toggleOpen}
        title={state.path ?? undefined}
        aria-label={state.kind === 'local' ? `Diretório do workspace: ${label}` : 'Adicionar pasta ao workspace'}
        aria-expanded={open}
      >
        <span aria-hidden="true">▰</span> {state.kind === 'local' ? label : 'Adicionar diretório'}
      </button>

      {open && (
        <div className="workspace-folder__panel" role="dialog" aria-label="Pasta de trabalho">
          {candidate ? (
            <>
              <h3>Confirmar diretório</h3>
              <p className="workspace-folder__path">{candidate.path}</p>
              <p className="workspace-folder__meta">{candidate.entryCount}{candidate.entriesTruncated ? '+' : ''} itens no primeiro nível</p>
              {state.scope === 'project' && state.projectName && (
                <p className="workspace-folder__meta">Vale para todos os chats do projeto {state.projectName}.</p>
              )}
              {candidate.risk === 'none' ? (
                <button type="button" disabled={actionBusy} onClick={() => void attach(candidate)}>Usar esta pasta</button>
              ) : (
                <>
                  <p className="workspace-folder__risk">
                    Essa pasta {RISK_SENTENCE[candidate.risk]}. O agente vai poder criar, editar e apagar arquivos em {candidate.path}, com shell real, sem pedir permissão a cada passo.
                  </p>
                  <button type="button" className="workspace-folder__risk-action" disabled={actionBusy} onClick={() => void attach(candidate)}>
                    Trabalhar em {candidate.path} mesmo assim
                  </button>
                </>
              )}
              <button type="button" disabled={actionBusy} onClick={() => setCandidate(null)}>Cancelar</button>
            </>
          ) : (
            <>
              <div>
                <h3>Workspace local</h3>
                <p className="workspace-folder__meta">Escolha um diretório para o agente trabalhar neste {state.scope === 'project' ? 'projeto' : 'chat'}.</p>
              </div>
              {state.kind === 'local' ? (
                <p className="workspace-folder__path">{state.path}</p>
              ) : (
                <p className="workspace-folder__meta">Sem pasta local. O agente trabalha na pasta gerenciada pelo Orin.</p>
              )}
              <button type="button" disabled={actionBusy || dialogBusy} onClick={() => void inspect(null)}>Selecionar diretório…</button>
              <label className="workspace-folder__field">
                Caminho da pasta
                <input value={typed} disabled={actionBusy || manualInspecting} onChange={(event) => setTyped(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); if (typed.trim()) void inspect(typed.trim()) } }} placeholder="C:\projetos\meu-app" />
              </label>
              <button type="button" disabled={actionBusy || manualInspecting || !typed.trim()} onClick={() => void inspect(typed.trim())}>Adicionar diretório</button>
              {state.kind === 'local' && <button type="button" disabled={actionBusy} onClick={() => void detach()}>Remover</button>}
            </>
          )}
          {dialogBusy && <p className="workspace-folder__notice" role="status">O seletor do Windows está aberto. Se ele não aparecer, cole o caminho abaixo.</p>}
          {notice && <p className="workspace-folder__notice" role="status">{notice}</p>}
          {error && <p className="workspace-folder__error" role="alert">{error}</p>}
        </div>
      )}
    </div>
  )
}
