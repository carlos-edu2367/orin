import { useId, useState, type ReactNode } from 'react'

type DisclosureProps = {
  label: string
  children: ReactNode
  defaultOpen?: boolean
  className?: string
}

export function Disclosure({ label, children, defaultOpen = false, className = '' }: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen)
  const contentId = useId()

  return (
    <div className={`disclosure ${className}`}>
      <button
        type="button"
        className="disclosure__trigger"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen((value) => !value)}
      >
        <span>{label}</span>
        <svg aria-hidden="true" viewBox="0 0 16 16" className={`disclosure__chevron ${open ? 'is-open' : ''}`}>
          <path d="m4 6 4 4 4-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
        </svg>
      </button>
      {open && (
        <div id={contentId} className="disclosure__content">
          {children}
        </div>
      )}
    </div>
  )
}
