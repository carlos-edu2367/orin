import { useState, type FormEvent } from 'react'
import { Disclosure } from '../../components/ui/Disclosure'

type ExecutionInputComposerProps = {
  pending: boolean
  onSubmit: (inputRef: string) => void
}

/**
 * The execution API accepts an opaque `input_ref`, not free-form input content.
 * Keeping that distinction visible prevents this UI from promising a text channel
 * the current backend does not authorize or persist.
 */
export function ExecutionInputComposer({ pending, onSubmit }: ExecutionInputComposerProps) {
  const [inputRef, setInputRef] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = inputRef.trim()
    if (!value || pending) return
    onSubmit(value)
  }

  return (
    <Disclosure label="Fornecer referência de entrada" className="execution-input">
      <form onSubmit={submit}>
        <label htmlFor="execution-input-ref">Referência de entrada</label>
        <input
          id="execution-input-ref"
          value={inputRef}
          onChange={(event) => setInputRef(event.target.value)}
          disabled={pending}
          required
        />
        <p>Envie a referência autorizada para que a execução possa continuar.</p>
        <button type="submit" className="button button--secondary" disabled={pending || inputRef.trim().length === 0}>
          Enviar entrada
        </button>
      </form>
    </Disclosure>
  )
}
