import {
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type JSX,
} from 'react'
import { useInRouterContext } from 'react-router-dom'
import type { ApiClient } from '../../api/client'
import {
  DEFAULT_FALLBACK_CHARACTER_NAMES,
  OPTIONAL_SCENE_CHARACTER_POSES,
  SCENE_CHARACTER_POSES,
  useSceneCharacters,
  type SceneCharacterPoseKey,
  type SceneCharacterRecord,
} from '../../api/sceneCharacters'
import { SettingsSection } from './SettingsSection'
import './charactersSection.css'

const MAX_SPRITE_BYTES = 2 * 1024 * 1024 // 2 MB
const ALLOWED_MIME_TYPES = new Set(['image/png', 'image/webp', 'image/gif'])
const ALLOWED_EXTENSIONS = /\.(png|webp|gif)$/i

export const POSE_LABELS_PT: Readonly<Record<SceneCharacterPoseKey, string>> = {
  idle: 'Parado (obrigatório)',
  think: 'Pensando (opcional)',
  work: 'Trabalhando (opcional)',
  wait: 'Esperando (opcional)',
  scared: 'Assustado (opcional)',
}

export const POSE_SHORT_LABELS_PT: Readonly<Record<SceneCharacterPoseKey, string>> = {
  idle: 'Parado',
  think: 'Pensando',
  work: 'Trabalhando',
  wait: 'Esperando',
  scared: 'Assustado',
}

export function validateCharacterSpriteFile(file: File): string | null {
  const mimeOk = ALLOWED_MIME_TYPES.has((file.type || '').toLowerCase())
  const extOk = ALLOWED_EXTENSIONS.test(file.name || '')
  if (!mimeOk && !extOk) {
    return 'Formato inválido. Envie uma imagem PNG, WebP ou GIF.'
  }
  if (file.type && !mimeOk) {
    return 'Formato inválido. Envie uma imagem PNG, WebP ou GIF.'
  }
  if (file.size > MAX_SPRITE_BYTES) {
    return 'O arquivo excede o limite máximo de 2 MB.'
  }
  if (file.size === 0) {
    return 'O arquivo selecionado está vazio.'
  }
  return null
}

function createPreviewUrl(file: File | Blob): string {
  if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
    try {
      return URL.createObjectURL(file)
    } catch {
      return 'blob:preview'
    }
  }
  return 'blob:preview'
}

function revokePreviewUrl(url: string | undefined): void {
  if (
    url &&
    url.startsWith('blob:') &&
    typeof URL !== 'undefined' &&
    typeof URL.revokeObjectURL === 'function'
  ) {
    try {
      URL.revokeObjectURL(url)
    } catch {
      // ignore
    }
  }
}

export interface CharactersSectionProps {
  client?: ApiClient
}

export function CharactersSection({ client }: CharactersSectionProps = {}): JSX.Element {
  const inRouter = useInRouterContext()
  const baseId = useId()
  const {
    config,
    loading,
    error: loadError,
    saveNames,
    createCharacter,
    updateCharacter,
    deleteCharacter,
    saveBindings,
  } = useSceneCharacters(client)

  // ─── 1. Names list state ───────────────────────────────────────────────────
  const [newNameInput, setNewNameInput] = useState('')
  const [namesError, setNamesError] = useState<string | null>(null)
  const [namesStatus, setNamesStatus] = useState<string | null>(null)
  const [savingNames, setSavingNames] = useState(false)
  const newNameInputRef = useRef<HTMLInputElement | null>(null)

  // ─── 2. Create character form state ────────────────────────────────────────
  const [createName, setCreateName] = useState('')
  const [createFiles, setCreateFiles] = useState<Partial<Record<SceneCharacterPoseKey, File>>>({})
  const [createPreviews, setCreatePreviews] = useState<
    Partial<Record<SceneCharacterPoseKey, string>>
  >({})
  const [createErrors, setCreateErrors] = useState<
    Partial<Record<SceneCharacterPoseKey | 'name' | 'form', string>>
  >({})
  const [createSuccess, setCreateSuccess] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const createNameInputRef = useRef<HTMLInputElement | null>(null)

  // ─── 3. Edit / delete character state ──────────────────────────────────────
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editFiles, setEditFiles] = useState<Partial<Record<SceneCharacterPoseKey, File>>>({})
  const [editPreviews, setEditPreviews] = useState<Partial<Record<SceneCharacterPoseKey, string>>>({})
  const [editRemovePoses, setEditRemovePoses] = useState<Set<SceneCharacterPoseKey>>(new Set())
  const [editError, setEditError] = useState<string | null>(null)
  const [savingEdit, setSavingEdit] = useState(false)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  // ─── 4. Agent -> character bindings state ──────────────────────────────────
  const [bindingAgentKey, setBindingAgentKey] = useState('')
  const [bindingCharId, setBindingCharId] = useState('')
  const [bindingError, setBindingError] = useState<string | null>(null)
  const [savingBinding, setSavingBinding] = useState(false)

  useEffect(() => {
    return () => {
      Object.values(createPreviews).forEach(revokePreviewUrl)
      Object.values(editPreviews).forEach(revokePreviewUrl)
    }
  }, [createPreviews, editPreviews])

  const handleAddName = async (event: FormEvent) => {
    event.preventDefault()
    setNamesError(null)
    setNamesStatus(null)
    const trimmed = newNameInput.trim()
    if (!trimmed) {
      setNamesError('Digite um nome para adicionar à lista.')
      newNameInputRef.current?.focus()
      return
    }
    if (trimmed.length > 32) {
      setNamesError('O nome deve ter no máximo 32 caracteres.')
      newNameInputRef.current?.focus()
      return
    }
    if (config.names.some((n) => n.toLowerCase() === trimmed.toLowerCase())) {
      setNamesError(`O nome "${trimmed}" já está na lista.`)
      newNameInputRef.current?.focus()
      return
    }
    const nextNames = [...config.names, trimmed]
    setSavingNames(true)
    try {
      await saveNames(nextNames)
      setNewNameInput('')
      setNamesStatus(`Nome "${trimmed}" adicionado.`)
    } catch (err) {
      setNamesError(err instanceof Error ? err.message : 'Não foi possível salvar a lista de nomes.')
    } finally {
      setSavingNames(false)
    }
  }

  const handleRemoveName = async (nameToRemove: string) => {
    setNamesError(null)
    setNamesStatus(null)
    const nextNames = config.names.filter((n) => n !== nameToRemove)
    setSavingNames(true)
    try {
      await saveNames(nextNames)
      setNamesStatus(`Nome "${nameToRemove}" removido.`)
    } catch (err) {
      setNamesError(err instanceof Error ? err.message : 'Não foi possível remover o nome.')
    } finally {
      setSavingNames(false)
    }
  }

  const handleRestoreDefaultNames = async () => {
    setNamesError(null)
    setNamesStatus(null)
    const defaults =
      config.defaultNames.length > 0
        ? [...config.defaultNames]
        : [...DEFAULT_FALLBACK_CHARACTER_NAMES]
    setSavingNames(true)
    try {
      await saveNames(defaults)
      setNamesStatus('Lista padrão de nomes restaurada.')
    } catch (err) {
      setNamesError(err instanceof Error ? err.message : 'Não foi possível restaurar a lista padrão.')
    } finally {
      setSavingNames(false)
    }
  }

  const handleCreatePoseChange = (
    pose: SceneCharacterPoseKey,
    event: ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0]
    setCreateSuccess(null)
    if (!file) {
      setCreateFiles((prev) => {
        const next = { ...prev }
        delete next[pose]
        return next
      })
      setCreatePreviews((prev) => {
        revokePreviewUrl(prev[pose])
        const next = { ...prev }
        delete next[pose]
        return next
      })
      setCreateErrors((prev) => {
        const next = { ...prev }
        delete next[pose]
        return next
      })
      return
    }

    const validationError = validateCharacterSpriteFile(file)
    if (validationError) {
      setCreateFiles((prev) => {
        const next = { ...prev }
        delete next[pose]
        return next
      })
      setCreatePreviews((prev) => {
        revokePreviewUrl(prev[pose])
        const next = { ...prev }
        delete next[pose]
        return next
      })
      setCreateErrors((prev) => ({ ...prev, [pose]: validationError }))
      return
    }

    const previewUrl = createPreviewUrl(file)
    setCreateFiles((prev) => ({ ...prev, [pose]: file }))
    setCreatePreviews((prev) => {
      revokePreviewUrl(prev[pose])
      return { ...prev, [pose]: previewUrl }
    })
    setCreateErrors((prev) => {
      const next = { ...prev }
      delete next[pose]
      delete next.form
      return next
    })
  }

  const handleCreateSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setCreateSuccess(null)
    const trimmedName = createName.trim()
    const nextErrors: Partial<Record<SceneCharacterPoseKey | 'name' | 'form', string>> = {}

    if (!trimmedName) {
      nextErrors.name = 'Informe o nome do personagem.'
    } else if (trimmedName.length > 32) {
      nextErrors.name = 'O nome do personagem deve ter no máximo 32 caracteres.'
    }

    if (!createFiles.idle) {
      nextErrors.idle = createErrors.idle ?? 'A imagem da pose "Parado" é obrigatória.'
    }

    for (const pose of SCENE_CHARACTER_POSES) {
      if (createErrors[pose]) {
        nextErrors[pose] = createErrors[pose]
      }
    }

    if (Object.keys(nextErrors).length > 0) {
      setCreateErrors(nextErrors)
      if (nextErrors.name) {
        createNameInputRef.current?.focus()
      }
      return
    }

    setCreating(true)
    setCreateErrors({})
    try {
      const created = await createCharacter({
        name: trimmedName,
        idle: createFiles.idle!,
        think: createFiles.think ?? null,
        work: createFiles.work ?? null,
        wait: createFiles.wait ?? null,
        scared: createFiles.scared ?? null,
      })
      Object.values(createPreviews).forEach(revokePreviewUrl)
      setCreateName('')
      setCreateFiles({})
      setCreatePreviews({})
      setCreateSuccess(`Personagem "${created.name}" criado com sucesso.`)
    } catch (err) {
      setCreateErrors({
        form: err instanceof Error ? err.message : 'Não foi possível criar o personagem.',
      })
    } finally {
      setCreating(false)
    }
  }

  const startEditingCharacter = (char: SceneCharacterRecord) => {
    Object.values(editPreviews).forEach(revokePreviewUrl)
    setEditingId(char.id)
    setEditName(char.name)
    setEditFiles({})
    setEditPreviews({})
    setEditRemovePoses(new Set())
    setEditError(null)
  }

  const cancelEditingCharacter = () => {
    Object.values(editPreviews).forEach(revokePreviewUrl)
    setEditingId(null)
    setEditName('')
    setEditFiles({})
    setEditPreviews({})
    setEditRemovePoses(new Set())
    setEditError(null)
  }

  const handleEditPoseChange = (
    pose: SceneCharacterPoseKey,
    event: ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0]
    if (!file) return
    const validationError = validateCharacterSpriteFile(file)
    if (validationError) {
      setEditError(validationError)
      return
    }
    setEditError(null)
    const previewUrl = createPreviewUrl(file)
    setEditFiles((prev) => ({ ...prev, [pose]: file }))
    setEditPreviews((prev) => {
      revokePreviewUrl(prev[pose])
      return { ...prev, [pose]: previewUrl }
    })
    setEditRemovePoses((prev) => {
      const next = new Set(prev)
      next.delete(pose)
      return next
    })
  }

  const toggleRemoveOptionalPose = (pose: SceneCharacterPoseKey) => {
    if (pose === 'idle') return
    setEditRemovePoses((prev) => {
      const next = new Set(prev)
      if (next.has(pose)) {
        next.delete(pose)
      } else {
        next.add(pose)
      }
      return next
    })
  }

  const handleSaveEdit = async (event: FormEvent) => {
    event.preventDefault()
    if (!editingId) return
    const trimmed = editName.trim()
    if (!trimmed) {
      setEditError('O nome do personagem não pode ficar em branco.')
      return
    }
    if (trimmed.length > 32) {
      setEditError('O nome do personagem deve ter no máximo 32 caracteres.')
      return
    }
    setSavingEdit(true)
    setEditError(null)
    try {
      await updateCharacter(editingId, {
        name: trimmed,
        idle: editFiles.idle ?? null,
        think: editFiles.think ?? null,
        work: editFiles.work ?? null,
        wait: editFiles.wait ?? null,
        scared: editFiles.scared ?? null,
        removePoses: Array.from(editRemovePoses),
      })
      cancelEditingCharacter()
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Não foi possível atualizar o personagem.')
    } finally {
      setSavingEdit(false)
    }
  }

  const handleConfirmDelete = async (charId: string) => {
    setDeletingId(charId)
    try {
      await deleteCharacter(charId)
      setConfirmDeleteId(null)
      if (editingId === charId) {
        cancelEditingCharacter()
      }
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Não foi possível apagar o personagem.')
    } finally {
      setDeletingId(null)
    }
  }

  const handleAddBinding = async (event: FormEvent) => {
    event.preventDefault()
    setBindingError(null)
    const key = bindingAgentKey.trim().toLowerCase()
    const targetCharId = bindingCharId || config.characters[0]?.id || ''
    if (!key) {
      setBindingError('Informe o nome ou tipo do agente (ex.: explore, claude-1).')
      return
    }
    if (!targetCharId) {
      setBindingError('Selecione um personagem para vincular.')
      return
    }
    setSavingBinding(true)
    try {
      await saveBindings({
        ...config.bindings,
        [key]: targetCharId,
      })
      setBindingAgentKey('')
    } catch (err) {
      setBindingError(err instanceof Error ? err.message : 'Não foi possível salvar o vínculo.')
    } finally {
      setSavingBinding(false)
    }
  }

  const handleRemoveBinding = async (agentKey: string) => {
    setBindingError(null)
    const nextBindings = { ...config.bindings }
    delete nextBindings[agentKey]
    setSavingBinding(true)
    try {
      await saveBindings(nextBindings)
    } catch (err) {
      setBindingError(err instanceof Error ? err.message : 'Não foi possível remover o vínculo.')
    } finally {
      setSavingBinding(false)
    }
  }

  const nameInputId = `${baseId}-new-name`
  const nameErrorId = `${baseId}-names-error`
  const createNameId = `${baseId}-create-name`
  const createNameErrorId = `${baseId}-create-name-error`
  const bindingAgentInputId = `${baseId}-binding-agent`
  const bindingSelectId = `${baseId}-binding-char`
  const bindingErrorId = `${baseId}-binding-error`

  const content = (
    <div className="characters-section" data-testid="characters-settings-section">
      {loadError ? (
        <p className="characters-section__error" role="alert" data-testid="characters-load-error">
          {loadError}
        </p>
      ) : null}

      {/* 1. Lista de nomes */}
      <section
        className="settings-card characters-section__card"
        aria-labelledby={`${baseId}-names-heading`}
        data-testid="characters-names-section"
      >
        <div>
          <h2 id={`${baseId}-names-heading`}>Lista de nomes sorteados</h2>
          <p className="settings-card__muted">
            Quando o agente que inicia um subagente não escolhe um nome de personagem nem há vínculo fixo, a Cena sorteia um nome desta lista.
          </p>
        </div>

        <form className="characters-section__row" onSubmit={handleAddName} noValidate>
          <div className="characters-section__field">
            <label htmlFor={nameInputId}>Novo nome para o sorteio</label>
            <input
              ref={newNameInputRef}
              id={nameInputId}
              type="text"
              maxLength={32}
              className="characters-section__input"
              data-testid="characters-name-input"
              placeholder="Ex.: Tico, Lira, Zeca…"
              value={newNameInput}
              aria-invalid={Boolean(namesError)}
              aria-describedby={namesError ? nameErrorId : undefined}
              onChange={(e) => {
                setNewNameInput(e.target.value)
                if (namesError) setNamesError(null)
              }}
            />
          </div>
          <button
            type="submit"
            className="characters-section__btn characters-section__btn--primary"
            data-testid="characters-add-name-button"
            disabled={savingNames || loading}
          >
            Adicionar nome
          </button>
          <button
            type="button"
            className="characters-section__btn"
            data-testid="characters-restore-names-button"
            disabled={savingNames || loading}
            onClick={handleRestoreDefaultNames}
          >
            Restaurar padrão
          </button>
        </form>

        {namesError ? (
          <p
            id={nameErrorId}
            className="characters-section__error"
            role="alert"
            data-testid="characters-names-error"
          >
            {namesError}
          </p>
        ) : null}
        {namesStatus ? (
          <p className="characters-section__status" role="status" data-testid="characters-names-status">
            {namesStatus}
          </p>
        ) : null}

        <ul
          className="characters-section__names-list"
          aria-label="Nomes disponíveis para sorteio"
          data-testid="characters-names-list"
        >
          {config.names.map((name) => (
            <li key={name} className="characters-section__name-chip">
              <span>{name}</span>
              <button
                type="button"
                className="characters-section__chip-remove"
                aria-label={`Remover nome ${name}`}
                data-testid={`characters-remove-name-${name}`}
                disabled={savingNames}
                onClick={() => handleRemoveName(name)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </section>

      {/* 2. Criação de personagem próprio com upload */}
      <section
        className="settings-card characters-section__card"
        aria-labelledby={`${baseId}-create-heading`}
        data-testid="characters-create-section"
      >
        <div>
          <h2 id={`${baseId}-create-heading`}>Criar personagem próprio</h2>
          <p className="settings-card__muted">
            Envie sprites em PNG, WebP ou GIF (até 2 MB por imagem, fundo transparente preservado). A pose &ldquo;Parado&rdquo; é obrigatória; as demais usam &ldquo;Parado&rdquo; caso não sejam enviadas.
          </p>
        </div>

        <form className="characters-section__card" onSubmit={handleCreateSubmit} noValidate>
          <div className="characters-section__field">
            <label htmlFor={createNameId}>Nome do personagem</label>
            <input
              ref={createNameInputRef}
              id={createNameId}
              type="text"
              maxLength={32}
              className="characters-section__input"
              data-testid="character-create-name-input"
              placeholder="Ex.: Capitão Pixel"
              value={createName}
              aria-invalid={Boolean(createErrors.name)}
              aria-describedby={createErrors.name ? createNameErrorId : undefined}
              onChange={(e) => {
                setCreateName(e.target.value)
                if (createErrors.name) {
                  setCreateErrors((prev) => {
                    const next = { ...prev }
                    delete next.name
                    return next
                  })
                }
              }}
            />
            {createErrors.name ? (
              <p
                id={createNameErrorId}
                className="characters-section__error"
                role="alert"
                data-testid="character-create-name-error"
              >
                {createErrors.name}
              </p>
            ) : null}
          </div>

          <div className="characters-section__poses-form-grid">
            {SCENE_CHARACTER_POSES.map((pose) => {
              const inputId = `${baseId}-pose-${pose}`
              const errId = `${baseId}-pose-error-${pose}`
              const poseErr = createErrors[pose]
              const previewUrl = createPreviews[pose]
              return (
                <div key={pose} className="characters-section__pose-upload">
                  <label htmlFor={inputId}>{POSE_LABELS_PT[pose]}</label>
                  <div className="characters-section__sprite-preview-box">
                    {previewUrl ? (
                      <img
                        src={previewUrl}
                        alt={`Prévia da pose ${POSE_SHORT_LABELS_PT[pose]}`}
                        className="characters-section__sprite-preview"
                        data-testid={`character-create-preview-${pose}`}
                      />
                    ) : (
                      <span className="settings-card__muted" aria-hidden="true">
                        —
                      </span>
                    )}
                  </div>
                  <input
                    id={inputId}
                    type="file"
                    accept="image/png,image/webp,image/gif,.png,.webp,.gif"
                    data-testid={`character-create-file-${pose}`}
                    aria-invalid={Boolean(poseErr)}
                    aria-describedby={poseErr ? errId : undefined}
                    onChange={(e) => handleCreatePoseChange(pose, e)}
                  />
                  {poseErr ? (
                    <p
                      id={errId}
                      className="characters-section__error"
                      role="alert"
                      data-testid={`character-create-error-${pose}`}
                    >
                      {poseErr}
                    </p>
                  ) : null}
                </div>
              )
            })}
          </div>

          {createErrors.form ? (
            <p
              className="characters-section__error"
              role="alert"
              data-testid="character-create-form-error"
            >
              {createErrors.form}
            </p>
          ) : null}
          {createSuccess ? (
            <p
              className="characters-section__status"
              role="status"
              data-testid="character-create-success"
            >
              {createSuccess}
            </p>
          ) : null}

          <div className="characters-section__row">
            <button
              type="submit"
              className="characters-section__btn characters-section__btn--primary"
              data-testid="character-create-submit"
              disabled={creating}
            >
              {creating ? 'Criando personagem…' : 'Criar personagem'}
            </button>
          </div>
        </form>
      </section>

      {/* 3. Grade de personagens criados */}
      <section
        className="settings-card characters-section__card"
        aria-labelledby={`${baseId}-gallery-heading`}
        data-testid="characters-gallery-section"
      >
        <div>
          <h2 id={`${baseId}-gallery-heading`}>Personagens criados</h2>
          <p className="settings-card__muted">
            Prévia pixelada das poses cadastradas. Se o nome sorteado ou escolhido para um subagente coincidir com um personagem abaixo, suas imagens serão usadas automaticamente na Cena.
          </p>
        </div>

        {config.characters.length === 0 ? (
          <p className="settings-card__muted" data-testid="characters-gallery-empty">
            Nenhum personagem próprio criado ainda.
          </p>
        ) : (
          <div className="characters-section__grid" data-testid="characters-gallery-grid">
            {config.characters.map((char) => {
              const isEditing = editingId === char.id
              const isConfirmingDelete = confirmDeleteId === char.id
              return (
                <article
                  key={char.id}
                  className="characters-section__char-card"
                  data-testid={`character-card-${char.id}`}
                  aria-label={`Personagem ${char.name}`}
                >
                  <div className="characters-section__char-header">
                    <strong>{char.name}</strong>
                    <div className="characters-section__row">
                      {!isEditing ? (
                        <button
                          type="button"
                          className="characters-section__btn"
                          data-testid={`character-edit-button-${char.id}`}
                          onClick={() => startEditingCharacter(char)}
                        >
                          Editar
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="characters-section__btn characters-section__btn--danger"
                        data-testid={`character-delete-button-${char.id}`}
                        onClick={() => setConfirmDeleteId(char.id)}
                      >
                        Apagar
                      </button>
                    </div>
                  </div>

                  <div className="characters-section__char-poses">
                    {SCENE_CHARACTER_POSES.map((pose) => {
                      const url = char.poses[pose]
                      if (!url) return null
                      return (
                        <div key={pose} className="characters-section__char-pose-item">
                          <div className="characters-section__sprite-preview-box">
                            <img
                              src={url}
                              alt={`${char.name} — pose ${POSE_SHORT_LABELS_PT[pose]}`}
                              className="characters-section__sprite-preview"
                              data-testid={`character-pose-img-${char.id}-${pose}`}
                            />
                          </div>
                          <span>{POSE_SHORT_LABELS_PT[pose]}</span>
                        </div>
                      )
                    })}
                  </div>

                  {isConfirmingDelete ? (
                    <div
                      className="characters-section__card"
                      role="alert"
                      data-testid={`character-delete-confirm-${char.id}`}
                    >
                      <p>
                        Tem certeza que deseja apagar <strong>{char.name}</strong>?
                      </p>
                      <div className="characters-section__row">
                        <button
                          type="button"
                          className="characters-section__btn characters-section__btn--danger"
                          data-testid={`character-delete-confirm-btn-${char.id}`}
                          disabled={deletingId === char.id}
                          onClick={() => handleConfirmDelete(char.id)}
                        >
                          {deletingId === char.id ? 'Apagando…' : 'Confirmar exclusão'}
                        </button>
                        <button
                          type="button"
                          className="characters-section__btn"
                          data-testid={`character-delete-cancel-btn-${char.id}`}
                          onClick={() => setConfirmDeleteId(null)}
                        >
                          Cancelar
                        </button>
                      </div>
                    </div>
                  ) : null}

                  {isEditing ? (
                    <form
                      className="characters-section__card"
                      onSubmit={handleSaveEdit}
                      data-testid={`character-edit-form-${char.id}`}
                    >
                      <div className="characters-section__field">
                        <label htmlFor={`${baseId}-edit-name-${char.id}`}>
                          Renomear personagem
                        </label>
                        <input
                          id={`${baseId}-edit-name-${char.id}`}
                          type="text"
                          maxLength={32}
                          className="characters-section__input"
                          data-testid={`character-edit-name-input-${char.id}`}
                          value={editName}
                          onChange={(e) => setEditName(e.target.value)}
                        />
                      </div>

                      {SCENE_CHARACTER_POSES.map((pose) => {
                        const fileId = `${baseId}-edit-pose-${char.id}-${pose}`
                        const hasExisting = Boolean(char.poses[pose])
                        const markedForRemoval = editRemovePoses.has(pose)
                        return (
                          <div key={pose} className="characters-section__field">
                            <label htmlFor={fileId}>
                              Trocar imagem: {POSE_SHORT_LABELS_PT[pose]}
                            </label>
                            <input
                              id={fileId}
                              type="file"
                              accept="image/png,image/webp,image/gif,.png,.webp,.gif"
                              data-testid={`character-edit-file-${char.id}-${pose}`}
                              onChange={(e) => handleEditPoseChange(pose, e)}
                            />
                            {OPTIONAL_SCENE_CHARACTER_POSES.includes(pose) && hasExisting ? (
                              <label>
                                <input
                                  type="checkbox"
                                  checked={markedForRemoval}
                                  data-testid={`character-edit-remove-pose-${char.id}-${pose}`}
                                  onChange={() => toggleRemoveOptionalPose(pose)}
                                />{' '}
                                Remover pose {POSE_SHORT_LABELS_PT[pose]}
                              </label>
                            ) : null}
                          </div>
                        )
                      })}

                      {editError ? (
                        <p className="characters-section__error" role="alert">
                          {editError}
                        </p>
                      ) : null}

                      <div className="characters-section__row">
                        <button
                          type="submit"
                          className="characters-section__btn characters-section__btn--primary"
                          data-testid={`character-edit-save-${char.id}`}
                          disabled={savingEdit}
                        >
                          {savingEdit ? 'Salvando…' : 'Salvar alterações'}
                        </button>
                        <button
                          type="button"
                          className="characters-section__btn"
                          data-testid={`character-edit-cancel-${char.id}`}
                          onClick={cancelEditingCharacter}
                        >
                          Cancelar
                        </button>
                      </div>
                    </form>
                  ) : null}
                </article>
              )
            })}
          </div>
        )}
      </section>

      {/* 4. Vínculos agente -> personagem */}
      <section
        className="settings-card characters-section__card"
        aria-labelledby={`${baseId}-bindings-heading`}
        data-testid="characters-bindings-section"
      >
        <div>
          <h2 id={`${baseId}-bindings-heading`}>Vínculos agente → personagem</h2>
          <p className="settings-card__muted">
            Associe o nome ou tipo técnico de um subagente (ex.: <code>explore</code>, <code>plan</code>, <code>claude-1</code>) a um personagem criado.
          </p>
        </div>

        <form className="characters-section__row" onSubmit={handleAddBinding} noValidate>
          <div className="characters-section__field">
            <label htmlFor={bindingAgentInputId}>Nome ou tipo do agente</label>
            <input
              id={bindingAgentInputId}
              type="text"
              className="characters-section__input"
              data-testid="characters-binding-agent-input"
              placeholder="Ex.: explore, plan, claude-1"
              value={bindingAgentKey}
              aria-invalid={Boolean(bindingError)}
              aria-describedby={bindingError ? bindingErrorId : undefined}
              onChange={(e) => {
                setBindingAgentKey(e.target.value)
                if (bindingError) setBindingError(null)
              }}
            />
          </div>

          <div className="characters-section__field">
            <label htmlFor={bindingSelectId}>Personagem vinculado</label>
            <select
              id={bindingSelectId}
              className="characters-section__select"
              data-testid="characters-binding-char-select"
              value={bindingCharId || config.characters[0]?.id || ''}
              onChange={(e) => setBindingCharId(e.target.value)}
            >
              {config.characters.length === 0 ? (
                <option value="">Crie um personagem primeiro</option>
              ) : (
                config.characters.map((ch) => (
                  <option key={ch.id} value={ch.id}>
                    {ch.name}
                  </option>
                ))
              )}
            </select>
          </div>

          <button
            type="submit"
            className="characters-section__btn characters-section__btn--primary"
            data-testid="characters-add-binding-button"
            disabled={savingBinding || config.characters.length === 0}
          >
            Vincular agente
          </button>
        </form>

        {bindingError ? (
          <p
            id={bindingErrorId}
            className="characters-section__error"
            role="alert"
            data-testid="characters-binding-error"
          >
            {bindingError}
          </p>
        ) : null}

        {Object.keys(config.bindings).length === 0 ? (
          <p className="settings-card__muted" data-testid="characters-bindings-empty">
            Nenhum vínculo configurado.
          </p>
        ) : (
          <ul
            className="characters-section__bindings-list"
            aria-label="Vínculos entre agentes e personagens"
            data-testid="characters-bindings-list"
          >
            {Object.entries(config.bindings).map(([agentKey, charId]) => {
              const targetChar = config.characters.find((c) => c.id === charId)
              return (
                <li
                  key={agentKey}
                  className="characters-section__binding-item"
                  data-testid={`characters-binding-item-${agentKey}`}
                >
                  <span>
                    <code>{agentKey}</code> → <strong>{targetChar?.name ?? charId}</strong>
                  </span>
                  <button
                    type="button"
                    className="characters-section__btn characters-section__btn--danger"
                    aria-label={`Remover vínculo de ${agentKey}`}
                    data-testid={`characters-remove-binding-${agentKey}`}
                    disabled={savingBinding}
                    onClick={() => handleRemoveBinding(agentKey)}
                  >
                    Remover vínculo
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>
    </div>
  )

  if (!inRouter) {
    return (
      <div>
        <div className="settings-section__head">
          <div>
            <p className="eyebrow">SESSÃO / PERSONAGENS</p>
            <h1>Personagens</h1>
            <p className="settings-content__lede">
              Nomes, sprites pixel art e vínculos dos subagentes na Cena.
            </p>
          </div>
        </div>
        {content}
      </div>
    )
  }

  return (
    <SettingsSection
      eyebrow="SESSÃO / PERSONAGENS"
      title="Personagens"
      lede="Nomes, sprites pixel art e vínculos dos subagentes na Cena."
    >
      {content}
    </SettingsSection>
  )
}

export default CharactersSection
