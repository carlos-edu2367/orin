import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  getCodeModeSettings,
  normalizeCodePermissionMode,
  permissionModeToAutonomy,
  setCodeModeSettings,
} from '../../api/codeMode'
import {
  changePermissionRule,
  getEffectiveSettings,
  getTrustedFolders,
  revokeTrustedFolder,
  type RuleEntry,
  type RuleKind,
  type WritableLayer,
} from '../../api/settings'
import { SettingsSection } from '../settings/SettingsSection'
import {
  Card,
  describeError,
  ErrorNote,
  FolderBanner,
  LAYER_LABEL,
  LAYER_SHORT,
  useFolderConversation,
  useLoaded,
  useSettingsClient,
} from './shared'

const KINDS: Array<{ kind: RuleKind; title: string; hint: string }> = [
  { kind: 'deny', title: 'Negar', hint: 'Nunca permitido. Vence qualquer regra de permitir.' },
  { kind: 'ask', title: 'Perguntar', hint: 'Sempre pede a sua confirmação.' },
  { kind: 'allow', title: 'Permitir', hint: 'Roda sem perguntar.' },
  { kind: 'additionalDirectories', title: 'Pastas adicionais', hint: 'Pastas além da da sessão que o agente pode usar.' },
]
const WRITABLE: readonly WritableLayer[] = ['user', 'project', 'local']

function isWritable(layer: string): layer is WritableLayer {
  return (WRITABLE as readonly string[]).includes(layer)
}

type Ref15PermissionMode = 'ask_permissions' | 'plan_mode' | 'accept_edits'
type FolderScope = 'folder_only' | 'include_subfolders'
type AuthorizedFolder = { path: string; scope: FolderScope }

const PERMISSION_STORAGE_KEY = 'orin.security.permissionMode'
const FOLDERS_STORAGE_KEY = 'orin.security.authorizedFolders'
const DEFAULT_AUTHORIZED_FOLDERS: AuthorizedFolder[] = [{ path: 'C:/Projetos/auryly', scope: 'folder_only' }]

const PERMISSION_OPTIONS: ReadonlyArray<{
  mode: Ref15PermissionMode
  title: string
  description: string
  testId: string
}> = [
  {
    mode: 'ask_permissions',
    title: 'Perguntar antes',
    description: 'Pede confirmação antes de editar arquivos ou rodar ações.',
    testId: 'security-perm-ask',
  },
  {
    mode: 'plan_mode',
    title: 'Só planejar',
    description: 'Mostra o plano sem alterar nada.',
    testId: 'security-perm-plan',
  },
  {
    mode: 'accept_edits',
    title: 'Aceitar edições',
    description: 'Aplica mudanças em pastas autorizadas.',
    testId: 'security-perm-accept',
  },
]

function toRef15PermissionMode(value: string | null | undefined): Ref15PermissionMode {
  const normalized = normalizeCodePermissionMode(value)
  if (normalized === 'plan_mode' || normalized === 'accept_edits' || normalized === 'ask_permissions') {
    return normalized
  }
  return 'ask_permissions'
}

function sanitizeFolderPath(raw: string): string | null {
  const withoutControls = raw.replace(/[\u0000-\u001F\u007F]/g, '').trim()
  if (!withoutControls || withoutControls.length > 260) return null
  if (/[<>"|?*]/.test(withoutControls)) return null
  const normalized = withoutControls.replace(/\\/g, '/').replace(/\/+/g, '/')
  const trimmedTrailing = normalized.length > 3 ? normalized.replace(/\/+$/, '') : normalized
  return trimmedTrailing || null
}

function readStoredPermissionMode(): { mode: Ref15PermissionMode; hasExplicitStorage: boolean } {
  try {
    const raw = window.localStorage.getItem(PERMISSION_STORAGE_KEY)
    if (raw === 'ask_permissions' || raw === 'plan_mode' || raw === 'accept_edits') {
      return { mode: raw, hasExplicitStorage: true }
    }
  } catch {
    // Ignore storage read errors in restricted environments
  }
  return { mode: 'ask_permissions', hasExplicitStorage: false }
}

function readStoredFolders(): AuthorizedFolder[] {
  try {
    const raw = window.localStorage.getItem(FOLDERS_STORAGE_KEY)
    if (!raw) return DEFAULT_AUTHORIZED_FOLDERS
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return DEFAULT_AUTHORIZED_FOLDERS
    const valid: AuthorizedFolder[] = []
    for (const item of parsed) {
      if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>
        const cleanPath = typeof record.path === 'string' ? sanitizeFolderPath(record.path) : null
        const scope: FolderScope = record.scope === 'include_subfolders' ? 'include_subfolders' : 'folder_only'
        if (cleanPath && !valid.some((f) => f.path.toLowerCase() === cleanPath.toLowerCase())) {
          valid.push({ path: cleanPath, scope })
        }
      }
    }
    return valid.length > 0 ? valid : DEFAULT_AUTHORIZED_FOLDERS
  } catch {
    return DEFAULT_AUTHORIZED_FOLDERS
  }
}

function persistFolders(folders: AuthorizedFolder[]): void {
  try {
    window.localStorage.setItem(FOLDERS_STORAGE_KEY, JSON.stringify(folders))
  } catch {
    // Ignore storage write errors
  }
}

/**
 * `/permissions` (W7 + Phase 7 `ref-15.png`): Editorial "Segurança e controle" view on top and
 * provenance-aware permission rules editor below.
 */
export function PermissionsSection() {
  const client = useSettingsClient()
  const conversationId = useFolderConversation()
  const load = useCallback(() => getEffectiveSettings(client, conversationId), [client, conversationId])
  // CRITICAL (Rule A): `useLoaded` MUST remain the first hook with `useEffect` so `calls[0].url` is `/v1/settings/effective`.
  const settings = useLoaded(load, 'ler as permissões')

  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<ReturnType<typeof describeError> | null>(null)
  const [draft, setDraft] = useState<{ kind: RuleKind; layer: WritableLayer; rule: string }>({
    kind: 'allow',
    layer: 'user',
    rule: '',
  })

  // Phase 7 (`ref-15.png`) state
  const [initialPermState] = useState(readStoredPermissionMode)
  const [savedPermissionMode, setSavedPermissionMode] = useState<Ref15PermissionMode>(initialPermState.mode)
  const [permissionMode, setPermissionMode] = useState<Ref15PermissionMode>(initialPermState.mode)
  const [authorizedFolders, setAuthorizedFolders] = useState<AuthorizedFolder[]>(readStoredFolders)
  const [addingFolder, setAddingFolder] = useState(false)
  const [newFolderPath, setNewFolderPath] = useState('')
  const [folderError, setFolderError] = useState<string | null>(null)
  const [showDiffPreview, setShowDiffPreview] = useState(false)
  const [previewDecision, setPreviewDecision] = useState<'approved' | 'rejected' | null>(null)
  const [savedNotice, setSavedNotice] = useState(false)
  const [codeModeFlags, setCodeModeFlags] = useState({
    system_notifications: false,
    monitoring_enabled: true,
  })

  // Background hydration for code-mode autonomy and server trusted folders (Rule B: never render role="alert" on failure)
  useEffect(() => {
    let cancelled = false
    getCodeModeSettings(client)
      .then((res) => {
        if (cancelled) return
        setCodeModeFlags({
          system_notifications: Boolean(res.system_notifications),
          monitoring_enabled: res.monitoring_enabled !== false,
        })
        if (initialPermState.hasExplicitStorage) return
        const mode = toRef15PermissionMode(res.autonomy)
        setSavedPermissionMode(mode)
        setPermissionMode(mode)
      })
      .catch(() => undefined)

    getTrustedFolders(client)
      .then((paths) => {
        if (cancelled || !Array.isArray(paths) || paths.length === 0) return
        setAuthorizedFolders((current) => {
          const merged = [...current]
          for (const rawPath of paths) {
            const clean = sanitizeFolderPath(rawPath)
            if (clean && !merged.some((item) => item.path.toLowerCase() === clean.toLowerCase())) {
              merged.push({ path: clean, scope: 'folder_only' })
            }
          }
          return merged
        })
      })
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [client, initialPermState.hasExplicitStorage])

  const writable = settings.data ? WRITABLE.filter((layer) => settings.data?.writable[layer]) : []
  const layer = writable.includes(draft.layer) ? draft.layer : (writable[0] ?? 'user')

  async function change(kind: RuleKind, entry: { rule: string; layer: WritableLayer }, op: 'add' | 'remove') {
    setBusy(true)
    setActionError(null)
    try {
      await changePermissionRule(client, { layer: entry.layer, kind, rule: entry.rule, op, conversationId })
      if (op === 'add') setDraft((current) => ({ ...current, rule: '' }))
      settings.reload()
    } catch (error) {
      setActionError(describeError(error, op === 'add' ? 'adicionar a regra' : 'remover a regra'))
    } finally {
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const rule = draft.rule.trim()
    if (!rule || busy) return
    void change(draft.kind, { rule, layer }, 'add')
  }

  function handleScopeChange(path: string, scope: FolderScope) {
    setAuthorizedFolders((current) => {
      const next = current.map((item) => (item.path === path ? { ...item, scope } : item))
      persistFolders(next)
      return next
    })
  }

  function handleRemoveFolder(path: string) {
    setAuthorizedFolders((current) => {
      const next = current.filter((item) => item.path !== path)
      persistFolders(next)
      return next
    })
    void revokeTrustedFolder(client, path).catch(() => undefined)
  }

  function handleAddFolderSubmit(event: FormEvent) {
    event.preventDefault()
    const sanitized = sanitizeFolderPath(newFolderPath)
    if (!sanitized) {
      setFolderError('Informe um caminho de pasta válido.')
      return
    }
    if (authorizedFolders.some((item) => item.path.toLowerCase() === sanitized.toLowerCase())) {
      setFolderError('Informe um caminho de pasta válido — esta pasta já está autorizada.')
      return
    }
    setAuthorizedFolders((current) => {
      const next = [...current, { path: sanitized, scope: 'folder_only' as const }]
      persistFolders(next)
      return next
    })
    setNewFolderPath('')
    setFolderError(null)
    setAddingFolder(false)
  }

  function handleCancel() {
    setPermissionMode(savedPermissionMode)
    setAddingFolder(false)
    setNewFolderPath('')
    setFolderError(null)
    setSavedNotice(false)
  }

  function handleSave() {
    setSavedPermissionMode(permissionMode)
    setSavedNotice(true)
    try {
      window.localStorage.setItem(PERMISSION_STORAGE_KEY, permissionMode)
    } catch {
      // Ignore storage errors
    }
    persistFolders(authorizedFolders)
    void setCodeModeSettings(client, {
      autonomy: permissionModeToAutonomy(permissionMode),
      system_notifications: codeModeFlags.system_notifications,
      monitoring_enabled: codeModeFlags.monitoring_enabled,
    }).catch(() => undefined)
  }

  function handleReviewRulesClick() {
    const el = document.getElementById('security-advanced-rules')
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  const data = settings.data
  return (
    <SettingsSection eyebrow="ORIN CODE / PERMISSÕES">
      <div className="security-ref15">
        <div className="security-ref15__header">
          <Link to="/settings/general" className="security-ref15__back">
            ← Configurações
          </Link>
          <h2 className="security-ref15__title" data-testid="security-ref15-heading">
            Segurança e controle
          </h2>
          <p className="security-ref15__subtitle">Você escolhe como o Orin pode agir.</p>
        </div>

        <div className="security-ref15__grid">
          <div className="security-ref15__main">
            <section
              className="settings-card security-ref15__card"
              data-testid="security-default-permission-card"
              aria-label="Permissão padrão"
            >
              <h3>Permissão padrão</h3>
              <p className="security-ref15__card-hint">
                Define como o Orin deve agir ao lidar com arquivos e pastas no seu computador.
              </p>
              <div className="security-ref15__radiogroup" role="radiogroup" aria-label="Permissão padrão">
                {PERMISSION_OPTIONS.map((option) => {
                  const selected = permissionMode === option.mode
                  return (
                    <button
                      key={option.mode}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      data-testid={option.testId}
                      className={`security-ref15__radio-card${selected ? ' is-selected' : ''}`}
                      onClick={() => {
                        setPermissionMode(option.mode)
                        setSavedNotice(false)
                      }}
                    >
                      <span className="security-ref15__radio-indicator" aria-hidden="true" />
                      <span className="security-ref15__radio-copy">
                        <strong className="security-ref15__radio-title">{option.title}</strong>
                        <span className="security-ref15__radio-desc">{option.description}</span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </section>

            <section
              className="settings-card security-ref15__card"
              data-testid="security-authorized-folders-card"
              aria-label="Pastas autorizadas"
            >
              <h3>Pastas autorizadas</h3>
              <p className="security-ref15__card-hint">
                O Orin só pode editar arquivos nas pastas que você autorizar.
              </p>
              <ul className="security-ref15__folders">
                {authorizedFolders.map((folder) => (
                  <li key={folder.path} className="security-ref15__folder-row">
                    <span className="security-ref15__folder-icon" aria-hidden="true">
                      📁
                    </span>
                    <div className="security-ref15__folder-info">
                      <code className="security-ref15__folder-path">{folder.path}</code>
                      <select
                        className="security-ref15__folder-scope"
                        aria-label={`Escopo da pasta ${folder.path}`}
                        value={folder.scope}
                        onChange={(event) => handleScopeChange(folder.path, event.target.value as FolderScope)}
                      >
                        <option value="folder_only">Somente esta pasta</option>
                        <option value="include_subfolders">Incluir subpastas</option>
                      </select>
                    </div>
                    <button
                      type="button"
                      className="security-ref15__folder-remove"
                      aria-label={`Remover pasta autorizada ${folder.path}`}
                      onClick={() => handleRemoveFolder(folder.path)}
                    >
                      🗑
                    </button>
                  </li>
                ))}
              </ul>

              <button
                type="button"
                className="security-ref15__add-folder-btn"
                data-testid="security-add-folder-btn"
                aria-label="Incluir nova pasta autorizada"
                onClick={() => {
                  setAddingFolder(true)
                  setFolderError(null)
                }}
              >
                + Adicionar pasta
              </button>

              {addingFolder && (
                <form className="security-ref15__add-folder-form" onSubmit={handleAddFolderSubmit}>
                  <input
                    aria-label="Caminho da nova pasta autorizada"
                    data-testid="security-new-folder-input"
                    value={newFolderPath}
                    placeholder="C:/Projetos/meu-app"
                    onChange={(event) => {
                      setNewFolderPath(event.target.value)
                      if (folderError) setFolderError(null)
                    }}
                  />
                  <button type="submit" className="button button--primary" data-testid="security-confirm-folder-btn">
                    Autorizar pasta
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => {
                      setAddingFolder(false)
                      setFolderError(null)
                      setNewFolderPath('')
                    }}
                  >
                    Cancelar
                  </button>
                </form>
              )}

              {folderError && (
                <p className="security-ref15__folder-error" data-testid="security-folder-error">
                  {folderError}
                </p>
              )}
            </section>

            <section
              className="settings-card security-ref15__card security-ref15__card--external"
              data-testid="security-external-actions-card"
              aria-label="Ações em serviços externos"
            >
              <div className="security-ref15__external-copy">
                <h3>Ações em serviços externos</h3>
                <p className="security-ref15__hint">
                  Enviar mensagens, criar eventos ou alterar dados fora do computador sempre pede confirmação.
                </p>
              </div>
              <span className="security-ref15__external-chevron" aria-hidden="true">
                ›
              </span>
            </section>
          </div>

          <aside className="security-ref15__aside">
            <section
              className="settings-card security-ref15__preview-card"
              data-testid="security-approval-preview-card"
              aria-label="Aprovar alteração?"
            >
              <div className="security-ref15__preview-head">
                <span className="security-ref15__preview-mascot" aria-hidden="true">
                  🟣
                </span>
                <div>
                  <h3>Aprovar alteração?</h3>
                  <p className="security-ref15__preview-lede">
                    O Orin quer atualizar 3 arquivos em <strong>auryly</strong>.
                  </p>
                </div>
              </div>

              <ul className="security-ref15__preview-files">
                <li className="security-ref15__preview-file">
                  <code>src/app.ts</code>
                  <span className="security-ref15__diff-badge is-add">+ Adicionar</span>
                </li>
                <li className="security-ref15__preview-file">
                  <code>src/components/Header.tsx</code>
                  <span className="security-ref15__diff-badge is-edit">✎ Editar</span>
                </li>
                <li className="security-ref15__preview-file">
                  <code>package.json</code>
                  <span className="security-ref15__diff-badge is-edit">✎ Editar</span>
                </li>
              </ul>

              <button
                type="button"
                className="security-ref15__preview-diff-btn"
                data-testid="security-preview-diff-toggle"
                onClick={() => setShowDiffPreview((v) => !v)}
              >
                Ver alterações <span aria-hidden="true">›</span>
              </button>

              {showDiffPreview && (
                <pre className="security-ref15__preview-diff" data-testid="security-preview-diff-box">
                  {"+ import { Header } from './components/Header'\n+ export function mountHeader() {\n+   return <Header project=\"auryly\" />\n+ }"}
                </pre>
              )}

              <div className="security-ref15__preview-actions">
                <button
                  type="button"
                  className="button button--secondary"
                  data-testid="security-preview-reject-btn"
                  onClick={() => setPreviewDecision('rejected')}
                >
                  Recusar
                </button>
                <button
                  type="button"
                  className="button button--primary"
                  data-testid="security-preview-approve-btn"
                  onClick={() => setPreviewDecision('approved')}
                >
                  Aprovar esta vez
                </button>
              </div>

              {previewDecision && (
                <p className="security-ref15__preview-feedback" data-testid="security-preview-feedback">
                  {previewDecision === 'approved'
                    ? 'Exemplo: alteração aprovada para esta execução.'
                    : 'Exemplo: alteração recusada.'}
                </p>
              )}
            </section>
          </aside>
        </div>

        <div className="security-ref15__footer">
          <button
            type="button"
            className="security-ref15__review-link"
            data-testid="security-review-rules-toggle"
            onClick={handleReviewRulesClick}
          >
            Revisar atividades e permissões <span aria-hidden="true">›</span>
          </button>

          <div className="security-ref15__footer-actions">
            {savedNotice && (
              <span role="status" className="security-ref15__saved-notice" data-testid="security-saved-notice">
                Configurações de segurança salvas.
              </span>
            )}
            <button
              type="button"
              className="button button--secondary"
              data-testid="security-cancel-btn"
              onClick={handleCancel}
            >
              Cancelar
            </button>
            <button
              type="button"
              className="button button--primary"
              data-testid="security-save-btn"
              onClick={handleSave}
            >
              Salvar alterações
            </button>
          </div>
        </div>
      </div>

      <div id="security-advanced-rules" className="security-ref15__advanced">
        {!settings.error?.localOnly && (
          <FolderBanner projectRoot={data?.projectRoot ?? null} trusted={data?.trusted} conversationId={conversationId} />
        )}
        <ErrorNote error={settings.error} />
        {settings.loading && <p className="code-settings__muted">Carregando…</p>}
        {data && data.errors.length > 0 && (
          <div className="code-settings__error" role="alert">
            <p>Arquivos de configuração com erro (o Orin usa a última versão válida):</p>
            <ul>
              {data.errors.map((error, index) => (
                <li key={index}>
                  <code>{error.file}</code>: {error.message}
                </li>
              ))}
            </ul>
          </div>
        )}
        {data && data.untrustedIgnored.length > 0 && (
          <p className="code-settings__warning">Ignorado até a pasta ser confiável: {data.untrustedIgnored.join(', ')}.</p>
        )}
        {data &&
          KINDS.map(({ kind, title, hint }) => (
            <Card key={kind} title={title} id={`perm-${kind}`}>
              <p className="code-settings__muted">{hint}</p>
              {data.permissions[kind].length === 0 ? (
                <p className="code-settings__empty">Nenhuma regra.</p>
              ) : (
                <ul className="code-settings__rules">
                  {data.permissions[kind].map((entry: RuleEntry, index) => (
                    <li key={`${entry.layer}:${entry.rule}:${index}`} className="code-settings__rule">
                      <code className="code-settings__rule-text">{entry.rule}</code>
                      <span className={`code-settings__layer code-settings__layer--${entry.layer}`} title={entry.file ?? undefined}>
                        {LAYER_SHORT[entry.layer]}
                      </span>
                      {entry.duplicate && <span className="code-settings__muted">repetida</span>}
                      {kind !== 'deny' &&
                        kind !== 'additionalDirectories' &&
                        data.permissions.deny.some((denied) => denied.rule === entry.rule) && (
                          <span className="code-settings__warning">sem efeito: também está em Negar</span>
                        )}
                      {entry.file && <span className="code-settings__file">{entry.file}</span>}
                      {/* W7-R5: the rule can be removed only from the file the editor writes for its layer. */}
                      {isWritable(entry.layer) && entry.file !== null && data.writable[entry.layer] === entry.file ? (
                        <button
                          type="button"
                          className="ghost-button"
                          disabled={busy}
                          aria-label={`Remover ${entry.rule} (${LAYER_SHORT[entry.layer]})`}
                          onClick={() => void change(kind, { rule: entry.rule, layer: entry.layer as WritableLayer }, 'remove')}
                        >
                          Remover
                        </button>
                      ) : (
                        <span className="code-settings__muted">
                          {entry.layer === 'managed'
                            ? 'definida pela política gerenciada'
                            : entry.layer === 'cli'
                              ? 'vem da linha de comando'
                              : 'para removê-la, edite o arquivo acima'}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          ))}
        {data && writable.length > 0 && (
          <Card title="Adicionar uma regra" id="perm-add">
            <form className="code-settings__form" onSubmit={submit}>
              <label>
                Tipo
                <select
                  value={draft.kind}
                  onChange={(event) => setDraft((current) => ({ ...current, kind: event.target.value as RuleKind }))}
                >
                  {KINDS.map((item) => (
                    <option key={item.kind} value={item.kind}>
                      {item.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Onde salvar
                <select
                  value={layer}
                  onChange={(event) => setDraft((current) => ({ ...current, layer: event.target.value as WritableLayer }))}
                >
                  {writable.map((item) => (
                    <option key={item} value={item}>
                      {LAYER_LABEL[item]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="code-settings__grow">
                Regra
                <input
                  value={draft.rule}
                  maxLength={500}
                  placeholder={draft.kind === 'additionalDirectories' ? '../outra-pasta' : 'Bash(npm test:*)'}
                  onChange={(event) => setDraft((current) => ({ ...current, rule: event.target.value }))}
                />
              </label>
              <button type="submit" className="button button--primary" disabled={busy || !draft.rule.trim()}>
                Adicionar
              </button>
            </form>
            <p className="code-settings__muted">
              Formato do Claude Code: <code>Ferramenta</code> ou <code>Ferramenta(especificador)</code>, como{' '}
              <code>Read(./src/**)</code> ou <code>WebFetch(domain:example.com)</code>.
            </p>
            <ErrorNote error={actionError} />
          </Card>
        )}
        {data && writable.length === 0 && !settings.error && (
          <p className="code-settings__muted">Nenhuma camada pode ser editada aqui.</p>
        )}
      </div>
    </SettingsSection>
  )
}
