import type { ReactNode } from 'react'

export function AuthLayout({ title, lede, children }: { title: string; lede?: string; children: ReactNode }) {
  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <p className="eyebrow">ORIN</p>
        <h1 id="auth-title">{title}</h1>
        {lede && <p className="auth-card__lede">{lede}</p>}
        {children}
      </section>
    </main>
  )
}
