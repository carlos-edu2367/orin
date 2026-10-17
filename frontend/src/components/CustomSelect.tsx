import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'

export type CustomSelectOption = {
  value: string
  label: string
  description?: string
}

export type CustomSelectProps = {
  value: string
  options: ReadonlyArray<CustomSelectOption>
  onChange: (value: string) => void
  disabled?: boolean
  id?: string
  ariaLabel?: string
  testId?: string
}

export function CustomSelect({
  value,
  options,
  onChange,
  disabled = false,
  id,
  ariaLabel,
  testId = 'custom-select-trigger',
}: CustomSelectProps) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const selectedIndex = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  )
  const selectedOption = options[selectedIndex] ?? options[0]

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    window.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      window.removeEventListener('keydown', onKeyDown, true)
    }
  }, [open])

  function handleKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (disabled || options.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
      } else {
        const next = options[(selectedIndex + 1) % options.length]
        if (next) onChange(next.value)
      }
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
      } else {
        const prev = options[(selectedIndex - 1 + options.length) % options.length]
        if (prev) onChange(prev.value)
      }
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      setOpen((current) => !current)
    }
  }

  return (
    <div
      ref={containerRef}
      className={`orin-select${open ? ' is-open' : ''}${disabled ? ' is-disabled' : ''}`}
    >
      <select
        id={id}
        aria-label={ariaLabel}
        aria-hidden={open ? 'true' : undefined}
        className="orin-select__native"
        value={value}
        disabled={disabled}
        tabIndex={-1}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      <button
        type="button"
        className="orin-select__trigger"
        data-testid={testId}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => {
          if (!disabled) setOpen((current) => !current)
        }}
        onKeyDown={handleKeyDown}
      >
        <span className="orin-select__trigger-copy">
          <span className="orin-select__trigger-label">{selectedOption?.label ?? value}</span>
          {selectedOption?.description && (
            <small className="orin-select__trigger-desc">{selectedOption.description}</small>
          )}
        </span>
        <svg
          className="orin-select__caret"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          aria-hidden="true"
        >
          <path d="M4.5 6.25 8 9.75l3.5-3.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && !disabled && (
        <ul className="orin-select__menu" role="listbox" aria-label={ariaLabel ?? 'Opções'}>
          {options.map((option) => {
            const isSelected = option.value === value
            return (
              <li
                key={option.value}
                role="option"
                aria-selected={isSelected}
                className={`orin-select__option${isSelected ? ' is-selected' : ''}`}
                onClick={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  onChange(option.value)
                  setOpen(false)
                }}
              >
                <span className="orin-select__option-copy">
                  <span className="orin-select__option-label">{option.label}</span>
                  {option.description && (
                    <small className="orin-select__option-desc">{option.description}</small>
                  )}
                </span>
                <span className="orin-select__option-check" aria-hidden="true">
                  {isSelected ? '✓' : ''}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
