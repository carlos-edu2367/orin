import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError } from '../../api/errors'
import { createBrowserApiClient, type ApiClient, type MutationIntent } from '../../api/client'
import { controlExecution, provideExecutionInput } from '../../api/executions'
import type { ExecutionControlAction } from '../../api/types'
import { ExecutionRealtimeStore } from '../realtime/realtimeStore'
import { ExecutionPage } from './ExecutionPage'

const browserClient = createBrowserApiClient()

type ExecutionRouteProps = {
  client?: ApiClient
}

export function ExecutionRoute({ client = browserClient }: ExecutionRouteProps = {}) {
  const { executionId = '' } = useParams()
  const store = useMemo(() => new ExecutionRealtimeStore({ client, executionId }), [client, executionId])
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)
  const [controlPending, setControlPending] = useState(false)
  const [controlError, setControlError] = useState<ApiError | null>(null)
  const [inputPending, setInputPending] = useState(false)
  const [inputError, setInputError] = useState<ApiError | null>(null)
  // One key per control intention, reused while that intention has not been accepted.
  const intents = useRef(new Map<string, MutationIntent>())

  useEffect(() => {
    store.start()
    return () => store.stop()
  }, [store])

  const execution = snapshot.executions[executionId]

  async function onControl(action: ExecutionControlAction) {
    if (!execution || controlPending) return
    setControlPending(true)
    setControlError(null)
    const intentKey = `${action}:${execution.state_version}`
    const intent = intents.current.get(intentKey) ?? client.createMutationIntent()
    intents.current.set(intentKey, intent)
    try {
      await controlExecution(client, execution.execution_id, {
        action,
        expected_state_version: execution.state_version,
      }, intent)
      intents.current.delete(intentKey)
    } catch (error) {
      const apiError = toApiError(error)
      setControlError(apiError)
      if (apiError.category === 'CONFLICT' || apiError.category === 'INDETERMINATE') {
        store.resync()
      }
    } finally {
      setControlPending(false)
    }
  }

  async function onInput(inputRef: string) {
    if (!execution || inputPending || execution.state !== 'WAITING_USER') return
    setInputPending(true)
    setInputError(null)
    const intentKey = `input:${execution.state_version}:${inputRef}`
    const intent = intents.current.get(intentKey) ?? client.createMutationIntent()
    intents.current.set(intentKey, intent)
    try {
      await provideExecutionInput(client, execution.execution_id, {
        input_ref: inputRef,
        expected_state_version: execution.state_version,
      }, intent)
      intents.current.delete(intentKey)
    } catch (error) {
      const apiError = toApiError(error)
      setInputError(apiError)
      if (apiError.category === 'CONFLICT' || apiError.category === 'INDETERMINATE') {
        store.resync()
      }
    } finally {
      setInputPending(false)
    }
  }

  if (!execution) {
    return (
      <main className="app-shell loading-shell" aria-busy="true">
        <p role="status" data-connection={snapshot.connection}>
          {snapshot.connection === 'degraded'
            ? 'Não foi possível carregar a execução autorizada.'
            : snapshot.connection === 'recovering' || snapshot.connection === 'resyncing'
              ? 'Recuperando a execução autorizada…'
              : 'Carregando execução autorizada…'}
        </p>
      </main>
    )
  }

  return (
    <>
      <ExecutionPage
        execution={execution}
        realtime={{ connection: snapshot.connection, appliedEventCount: snapshot.appliedEventCount }}
        controlPending={controlPending}
        onControl={onControl}
        inputPending={inputPending}
        onInput={onInput}
      />
      {controlError && (
        <p className="sr-alert" role="alert">
          O comando não foi confirmado. O estado atual foi preservado.
          {controlError.retryAfter !== null && ` Tente novamente em ${controlError.retryAfter}s.`}
        </p>
      )}
      {inputError && (
        <p className="sr-alert" role="alert">
          A entrada não foi confirmada. O estado atual foi preservado.
          {inputError.retryAfter !== null && ` Tente novamente em ${inputError.retryAfter}s.`}
        </p>
      )}
    </>
  )
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  return new ApiError({ status: 0, code: 'request_failed', category: 'INTERNAL', messageKey: 'request_failed', retryable: false })
}
