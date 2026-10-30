import { useId, useMemo, useState, type JSX } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { configureProvider, testOllamaConnection, type ProviderName } from '../../api/providers'
import { Brand } from '../../components/Brand'
import { OrinMascot } from '../../components/OrinMascot'
import './onboarding.css'

export type OnboardingStepId = 1 | 2 | 3 | 4
type ConnectionMethod = 'provider' | 'ollama' | 'existing'
type FriendTone = 'Amigável' | 'Direto' | 'Criativo' | 'Analítico'

export const ONBOARDING_DISPLAY_NAME_KEY = 'orin.user.displayName'
export const ONBOARDING_COMPLETED_KEY = 'orin.onboarding.completed'
export const ONBOARDING_FRIEND_NAME_KEY = 'orin.friend.name'
export const ONBOARDING_FRIEND_TONE_KEY = 'orin.friend.tone'

const PROVIDER_OPTIONS: ReadonlyArray<{ value: ProviderName; label: string }> = [
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'nvidia', label: 'NVIDIA' },
  { value: 'omniroute', label: 'OmniRoute' },
  { value: 'opencode', label: 'OpenCode' },
]

const FRIEND_TONES: ReadonlyArray<FriendTone> = ['Amigável', 'Direto', 'Criativo', 'Analítico']

const STEPS: ReadonlyArray<{
  id: OnboardingStepId
  title: string
  subtitle: string
}> = [
  { id: 1, title: 'Boas-vindas', subtitle: 'Conheça o Orin' },
  { id: 2, title: 'Conectar IA', subtitle: 'Escolha como começar' },
  { id: 3, title: 'Seu Friend', subtitle: 'Personalize seu companheiro' },
  { id: 4, title: 'Tudo pronto', subtitle: 'Hora de começar' },
]

export function sanitizeOnboardingDisplayName(rawName?: string | null): string {
  if (typeof rawName !== 'string') return ''
  const cleaned = Array.from(rawName)
    .filter((ch) => {
      const code = ch.charCodeAt(0)
      return code > 0x1f && code !== 0x7f
    })
    .join('')
    .trim()
  if (!cleaned) return ''
  return cleaned.slice(0, 60)
}

function readStoredString(key: string, fallback: string): string {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return fallback
    const raw = window.localStorage.getItem(key)
    return raw && raw.trim() ? raw.trim() : fallback
  } catch {
    return fallback
  }
}

function writeStoredString(key: string, value: string): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(key, value)
    }
  } catch {
    // Ignore storage errors in restricted environments
  }
}

function parseStepParam(raw: string | null, fallback: OnboardingStepId): OnboardingStepId {
  if (raw === '1') return 1
  if (raw === '2') return 2
  if (raw === '3') return 3
  if (raw === '4') return 4
  return fallback
}

export type OnboardingWizardProps = {
  client?: ApiClient
  initialStep?: OnboardingStepId
}

export function OnboardingWizard({
  client,
  initialStep,
}: OnboardingWizardProps): JSX.Element {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const apiKeyInputId = useId()
  const providerSelectId = useId()
  const ollamaUrlInputId = useId()

  const resolvedInitialStep =
    initialStep ?? parseStepParam(searchParams.get('step'), 2)

  const [currentStep, setCurrentStep] = useState<OnboardingStepId>(resolvedInitialStep)
  const [displayName, setDisplayName] = useState<string>(() =>
    readStoredString(ONBOARDING_DISPLAY_NAME_KEY, ''),
  )
  const [connectionMethod, setConnectionMethod] = useState<ConnectionMethod>('provider')
  const [selectedProvider, setSelectedProvider] = useState<ProviderName>('openrouter')
  const [apiKey, setApiKey] = useState<string>('')
  const [showKey, setShowKey] = useState<boolean>(false)
  const [ollamaUrl, setOllamaUrl] = useState<string>('http://localhost:11434')
  const [testingConnection, setTestingConnection] = useState<boolean>(false)
  const [connectionVerified, setConnectionVerified] = useState<boolean>(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const [friendName, setFriendName] = useState<string>(() =>
    readStoredString(ONBOARDING_FRIEND_NAME_KEY, 'Orin'),
  )
  const [friendTone, setFriendTone] = useState<FriendTone>('Amigável')

  function persistCurrentStepState(step: OnboardingStepId) {
    if (step === 1) {
      const safeName = sanitizeOnboardingDisplayName(displayName)
      setDisplayName(safeName)
      writeStoredString(ONBOARDING_DISPLAY_NAME_KEY, safeName)
    } else if (step === 3) {
      const safeFriend = sanitizeOnboardingDisplayName(friendName) || 'Orin'
      setFriendName(safeFriend)
      writeStoredString(ONBOARDING_FRIEND_NAME_KEY, safeFriend)
      writeStoredString(ONBOARDING_FRIEND_TONE_KEY, friendTone)
    }
  }

  function handleStepSelect(step: OnboardingStepId) {
    persistCurrentStepState(currentStep)
    setErrorMessage(null)
    setCurrentStep(step)
  }

  function handleBack() {
    persistCurrentStepState(currentStep)
    setErrorMessage(null)
    if (currentStep > 1) {
      setCurrentStep((prev) => (prev - 1) as OnboardingStepId)
    }
  }

  function handleContinue() {
    persistCurrentStepState(currentStep)
    setErrorMessage(null)
    if (currentStep < 4) {
      setCurrentStep((prev) => (prev + 1) as OnboardingStepId)
    }
  }

  function handleFinishOnboarding() {
    const safeName = sanitizeOnboardingDisplayName(displayName)
    writeStoredString(ONBOARDING_DISPLAY_NAME_KEY, safeName)
    writeStoredString(ONBOARDING_FRIEND_NAME_KEY, sanitizeOnboardingDisplayName(friendName) || 'Orin')
    writeStoredString(ONBOARDING_FRIEND_TONE_KEY, friendTone)
    writeStoredString(ONBOARDING_COMPLETED_KEY, 'true')
    navigate('/')
  }

  async function handleTestProviderConnection() {
    const trimmedKey = apiKey.trim()
    if (!trimmedKey) {
      setConnectionVerified(false)
      setErrorMessage('Informe uma chave de API válida.')
      return
    }

    setTestingConnection(true)
    setErrorMessage(null)
    setConnectionVerified(false)

    try {
      await configureProvider(apiClient, selectedProvider, {
        apiKey: trimmedKey,
        enabled: true,
      })
      setConnectionVerified(true)
    } catch {
      setConnectionVerified(false)
      setErrorMessage(
        'Não foi possível verificar a conexão com o provedor. Confira a chave informada e tente novamente.',
      )
    } finally {
      setTestingConnection(false)
    }
  }

  async function handleTestOllama() {
    const trimmedUrl = ollamaUrl.trim()
    if (!trimmedUrl || !/^https?:\/\//i.test(trimmedUrl)) {
      setConnectionVerified(false)
      setErrorMessage('Informe um endereço local válido do Ollama (ex.: http://localhost:11434).')
      return
    }

    setTestingConnection(true)
    setErrorMessage(null)
    setConnectionVerified(false)

    try {
      await testOllamaConnection(apiClient, { apiKey: '', baseUrl: trimmedUrl })
      setConnectionVerified(true)
    } catch {
      setConnectionVerified(false)
      setErrorMessage('Não foi possível conectar ao Ollama local. Verifique se o serviço está ativo.')
    } finally {
      setTestingConnection(false)
    }
  }

  return (
    <div className="onboarding-wizard" data-testid="onboarding-wizard">
      {/* Coluna 1: Stepper Lateral Esquerdo */}
      <aside
        className="onboarding-wizard__stepper"
        data-testid="onboarding-stepper"
        aria-label="Etapas de configuração inicial"
      >
        <div className="onboarding-wizard__brand">
          <Brand to="/" />
        </div>

        <ol className="onboarding-wizard__steps">
          {STEPS.map((step) => {
            const isCompleted = step.id < currentStep
            const isActive = step.id === currentStep
            const stateClass = isCompleted
              ? 'onboarding-step--completed'
              : isActive
                ? 'onboarding-step--active'
                : 'onboarding-step--upcoming'

            return (
              <li key={step.id} className={`onboarding-step ${stateClass}`}>
                <button
                  type="button"
                  className="onboarding-step__button"
                  onClick={() => handleStepSelect(step.id)}
                  aria-current={isActive ? 'step' : undefined}
                >
                  <span className="onboarding-step__badge" aria-hidden="true">
                    {isCompleted ? (
                      <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                        <path
                          d="M3.5 8.5L6.5 11.5L12.5 4.5"
                          stroke="currentColor"
                          strokeWidth="2.2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    ) : (
                      step.id
                    )}
                  </span>
                  <span className="onboarding-step__text">
                    <span className="onboarding-step__title">{step.title}</span>
                    <span className="onboarding-step__subtitle">{step.subtitle}</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      </aside>

      {/* Coluna 2: Palco Central */}
      <main className="onboarding-wizard__stage" data-testid="onboarding-stage">
        <div className="onboarding-wizard__stage-inner">
          {currentStep === 1 && (
            <>
              <h1 className="onboarding-wizard__heading">Bem-vindo ao Orin</h1>
              <p className="onboarding-wizard__subtitle">
                Vamos personalizar seu espaço para que o Orin trabalhe do seu jeito.
              </p>

              <div className="onboarding-card onboarding-card--active">
                <div className="onboarding-field-group">
                  <label className="onboarding-field-label" htmlFor="onboarding-display-name-input">
                    Como podemos chamar você?
                  </label>
                  <input
                    id="onboarding-display-name-input"
                    data-testid="onboarding-display-name"
                    type="text"
                    className="onboarding-key-input"
                    value={displayName}
                    maxLength={60}
                    onChange={(event) => setDisplayName(event.target.value)}
                    placeholder="Seu nome"
                  />
                </div>
              </div>
            </>
          )}

          {currentStep === 2 && (
            <>
              <h1 className="onboarding-wizard__heading">Escolha como começar</h1>
              <p className="onboarding-wizard__subtitle">Você pode mudar isso depois.</p>

              <div className="onboarding-wizard__cards" role="radiogroup" aria-label="Opções de conexão de IA">
                {/* Card 1: Conectar um provedor */}
                <div
                  className={`onboarding-card${connectionMethod === 'provider' ? ' onboarding-card--active' : ''}`}
                >
                  <button
                    type="button"
                    className="onboarding-card__header"
                    role="radio"
                    aria-checked={connectionMethod === 'provider'}
                    onClick={() => {
                      setConnectionMethod('provider')
                      setConnectionVerified(false)
                      setErrorMessage(null)
                    }}
                  >
                    <span className="onboarding-card__header-left">
                      <span className="onboarding-card__radio" aria-hidden="true">
                        {connectionMethod === 'provider' && (
                          <span className="onboarding-card__radio-dot" />
                        )}
                      </span>
                      <span className="onboarding-card__icon" aria-hidden="true">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                          <path
                            d="M6.5 18H17.5C19.9853 18 22 15.9853 22 13.5C22 11.1965 20.2683 9.29699 18.0315 9.0311C17.4063 5.60336 14.4071 3 10.8 3C6.71309 3 3.4 6.31309 3.4 10.4C3.4 10.7155 3.41975 11.0263 3.45804 11.3311C2.5679 12.0119 2 13.088 2 14.3C2 16.3435 3.65655 18 5.7 18H6.5Z"
                            stroke="currentColor"
                            strokeWidth="1.7"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                      <span className="onboarding-card__titles">
                        <span className="onboarding-card__title">Conectar um provedor</span>
                        <span className="onboarding-card__desc">
                          Use uma chave de API do seu serviço.
                        </span>
                      </span>
                    </span>

                    {connectionVerified && connectionMethod === 'provider' ? (
                      <span
                        className="onboarding-verified-badge"
                        data-testid="onboarding-connection-verified"
                      >
                        <span className="onboarding-verified-badge__dot" aria-hidden="true" />
                        Conexão verificada
                      </span>
                    ) : (
                      connectionMethod !== 'provider' && (
                        <span className="onboarding-card__chevron" aria-hidden="true">
                          ›
                        </span>
                      )
                    )}
                  </button>

                  {connectionMethod === 'provider' && (
                    <div className="onboarding-card__body">
                      <div className="onboarding-select-wrap">
                        <span className="onboarding-select-wrap__icon" aria-hidden="true">
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                            <path
                              d="M5 12H19M19 12L14 7M19 12L14 17"
                              stroke="currentColor"
                              strokeWidth="1.8"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </span>
                        <select
                          id={providerSelectId}
                          aria-label="Provedor de IA"
                          className="onboarding-select"
                          value={selectedProvider}
                          onChange={(event) => {
                            setSelectedProvider(event.target.value as ProviderName)
                            setConnectionVerified(false)
                            setErrorMessage(null)
                          }}
                        >
                          {PROVIDER_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                        <span className="onboarding-select-wrap__chevron" aria-hidden="true">
                          ▾
                        </span>
                      </div>

                      <div className="onboarding-field-group">
                        <label className="onboarding-field-label" htmlFor={apiKeyInputId}>
                          Chave de API
                        </label>
                        <div className="onboarding-key-row">
                          <div className="onboarding-key-input-wrap">
                            <span className="onboarding-key-input-wrap__icon" aria-hidden="true">
                              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                                <circle
                                  cx="7.5"
                                  cy="15.5"
                                  r="4.5"
                                  stroke="currentColor"
                                  strokeWidth="1.7"
                                />
                                <path
                                  d="M10.8 12.2L20 3M16 7L19 10"
                                  stroke="currentColor"
                                  strokeWidth="1.7"
                                  strokeLinecap="round"
                                />
                              </svg>
                            </span>
                            <input
                              id={apiKeyInputId}
                              type={showKey ? 'text' : 'password'}
                              autoComplete="off"
                              spellCheck={false}
                              className="onboarding-key-input"
                              value={apiKey}
                              onChange={(event) => {
                                setApiKey(event.target.value)
                                if (connectionVerified) setConnectionVerified(false)
                                if (errorMessage) setErrorMessage(null)
                              }}
                              placeholder="sk-..."
                            />
                            <button
                              type="button"
                              className="onboarding-key-toggle"
                              data-testid="onboarding-toggle-key-visibility"
                              aria-label={showKey ? 'Ocultar chave de API' : 'Mostrar chave de API'}
                              onClick={() => setShowKey((prev) => !prev)}
                            >
                              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                <path
                                  d="M2 12C3.8 7.5 7.5 5 12 5C16.5 5 20.2 7.5 22 12C20.2 16.5 16.5 19 12 19C7.5 19 3.8 16.5 2 12Z"
                                  stroke="currentColor"
                                  strokeWidth="1.7"
                                />
                                <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.7" />
                              </svg>
                            </button>
                          </div>

                          <button
                            type="button"
                            className="onboarding-btn-primary"
                            data-testid="onboarding-test-connection"
                            disabled={testingConnection}
                            onClick={() => void handleTestProviderConnection()}
                          >
                            {testingConnection ? 'Testando...' : 'Testar conexão'}
                          </button>
                        </div>
                      </div>

                      {errorMessage && (
                        <div className="onboarding-alert" role="alert">
                          {errorMessage}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Card 2: Usar IA no computador */}
                <div
                  className={`onboarding-card${connectionMethod === 'ollama' ? ' onboarding-card--active' : ''}`}
                >
                  <button
                    type="button"
                    className="onboarding-card__header"
                    role="radio"
                    aria-checked={connectionMethod === 'ollama'}
                    onClick={() => {
                      setConnectionMethod('ollama')
                      setConnectionVerified(false)
                      setErrorMessage(null)
                    }}
                  >
                    <span className="onboarding-card__header-left">
                      <span className="onboarding-card__radio" aria-hidden="true">
                        {connectionMethod === 'ollama' && (
                          <span className="onboarding-card__radio-dot" />
                        )}
                      </span>
                      <span className="onboarding-card__icon" aria-hidden="true">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                          <rect
                            x="3"
                            y="4"
                            width="18"
                            height="12"
                            rx="2"
                            stroke="currentColor"
                            strokeWidth="1.7"
                          />
                          <path
                            d="M2 19H22"
                            stroke="currentColor"
                            strokeWidth="1.7"
                            strokeLinecap="round"
                          />
                        </svg>
                      </span>
                      <span className="onboarding-card__titles">
                        <span className="onboarding-card__title">Usar IA no computador</span>
                        <span className="onboarding-card__desc">
                          Conecte um modelo local pelo Ollama.
                        </span>
                      </span>
                    </span>

                    {connectionVerified && connectionMethod === 'ollama' ? (
                      <span
                        className="onboarding-verified-badge"
                        data-testid="onboarding-connection-verified"
                      >
                        <span className="onboarding-verified-badge__dot" aria-hidden="true" />
                        Conexão verificada
                      </span>
                    ) : (
                      <span className="onboarding-card__chevron" aria-hidden="true">
                        ›
                      </span>
                    )}
                  </button>

                  {connectionMethod === 'ollama' && (
                    <div className="onboarding-card__body">
                      <div className="onboarding-field-group">
                        <label className="onboarding-field-label" htmlFor={ollamaUrlInputId}>
                          Endereço do Ollama
                        </label>
                        <div className="onboarding-key-row">
                          <input
                            id={ollamaUrlInputId}
                            type="url"
                            className="onboarding-key-input"
                            value={ollamaUrl}
                            onChange={(event) => setOllamaUrl(event.target.value)}
                          />
                          <button
                            type="button"
                            className="onboarding-btn-primary"
                            onClick={() => void handleTestOllama()}
                          >
                            Testar Ollama
                          </button>
                        </div>
                      </div>
                      {errorMessage && (
                        <div className="onboarding-alert" role="alert">
                          {errorMessage}
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Card 3: Já tenho uma conexão */}
                <div
                  className={`onboarding-card${connectionMethod === 'existing' ? ' onboarding-card--active' : ''}`}
                >
                  <button
                    type="button"
                    className="onboarding-card__header"
                    role="radio"
                    aria-checked={connectionMethod === 'existing'}
                    onClick={() => {
                      setConnectionMethod('existing')
                      setErrorMessage(null)
                    }}
                  >
                    <span className="onboarding-card__header-left">
                      <span className="onboarding-card__radio" aria-hidden="true">
                        {connectionMethod === 'existing' && (
                          <span className="onboarding-card__radio-dot" />
                        )}
                      </span>
                      <span className="onboarding-card__icon" aria-hidden="true">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                          <path
                            d="M14 3H7C5.89543 3 5 3.89543 5 5V19C5 20.1046 5.89543 21 7 21H17C18.1046 21 19 20.1046 19 19V8L14 3Z"
                            stroke="currentColor"
                            strokeWidth="1.7"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                          <path
                            d="M14 3V8H19"
                            stroke="currentColor"
                            strokeWidth="1.7"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                      <span className="onboarding-card__titles">
                        <span className="onboarding-card__title">Já tenho uma conexão</span>
                        <span className="onboarding-card__desc">Importar configurações.</span>
                      </span>
                    </span>

                    <span className="onboarding-card__chevron" aria-hidden="true">
                      ›
                    </span>
                  </button>

                  {connectionMethod === 'existing' && (
                    <div className="onboarding-card__body">
                      <p className="onboarding-card__desc">
                        Suas credenciais já configuradas no ambiente serão mantidas. Você também pode
                        gerenciá-las a qualquer momento em Integrações.
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}

          {currentStep === 3 && (
            <>
              <h1 className="onboarding-wizard__heading">Personalize seu companheiro</h1>
              <p className="onboarding-wizard__subtitle">
                Escolha o nome e o estilo de comunicação inicial do seu Friend.
              </p>

              <div className="onboarding-card onboarding-card--active">
                <div className="onboarding-field-group">
                  <label className="onboarding-field-label" htmlFor="onboarding-friend-name-input">
                    Nome do seu Friend
                  </label>
                  <input
                    id="onboarding-friend-name-input"
                    data-testid="onboarding-friend-name"
                    type="text"
                    className="onboarding-key-input"
                    value={friendName}
                    maxLength={60}
                    onChange={(event) => setFriendName(event.target.value)}
                  />
                </div>

                <div className="onboarding-field-group" style={{ marginTop: '16px' }}>
                  <span className="onboarding-field-label">Tom inicial</span>
                  <div className="onboarding-tone-grid">
                    {FRIEND_TONES.map((tone) => (
                      <button
                        key={tone}
                        type="button"
                        className={`onboarding-tone-chip${friendTone === tone ? ' is-selected' : ''}`}
                        onClick={() => setFriendTone(tone)}
                      >
                        {tone}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </>
          )}

          {currentStep === 4 && (
            <>
              <h1 className="onboarding-wizard__heading">Tudo pronto para começar</h1>
              <p className="onboarding-wizard__subtitle">
                Seu espaço foi preparado para conversar, criar e organizar projetos com o Orin.
              </p>

              <div className="onboarding-card onboarding-card--active">
                <div className="onboarding-card__titles">
                  <span className="onboarding-card__title">
                    {sanitizeOnboardingDisplayName(displayName)
                      ? `Olá, ${sanitizeOnboardingDisplayName(displayName)}!`
                      : 'Olá!'}
                  </span>
                  <span className="onboarding-card__desc">
                    Companheiro: {sanitizeOnboardingDisplayName(friendName) || 'Orin'} ({friendTone})
                  </span>
                </div>
              </div>
            </>
          )}

          <footer className="onboarding-wizard__footer">
            <button
              type="button"
              className="onboarding-btn-secondary"
              onClick={handleBack}
              disabled={currentStep === 1}
            >
              Voltar
            </button>

            {currentStep < 4 ? (
              <button
                type="button"
                className="onboarding-btn-primary"
                onClick={handleContinue}
              >
                Continuar <span aria-hidden="true">→</span>
              </button>
            ) : (
              <button
                type="button"
                className="onboarding-btn-primary"
                onClick={handleFinishOnboarding}
              >
                Começar a usar o Orin <span aria-hidden="true">→</span>
              </button>
            )}
          </footer>
        </div>
      </main>

      {/* Coluna 3: Painel Contextual Direito */}
      <aside
        className="onboarding-wizard__context"
        data-testid="onboarding-context-panel"
        aria-label="Assistente Orin"
      >
        <OrinMascot size={112} variant="waving" animated ariaLabel="Mascote Orin acenando" />
        <h2 className="onboarding-wizard__context-title">Vamos preparar seu espaço.</h2>
        <p className="onboarding-wizard__context-desc">
          Conecte sua IA preferida para começar a conversar, criar e ser mais produtivo com o Orin.
        </p>
      </aside>
    </div>
  )
}
