export type OrbThemeId = 'aurora' | 'ocean' | 'sunset' | 'mint' | 'mono'

export type OrbTheme = {
  id: OrbThemeId
  label: string
  /** Body gradient stops, deep → bright. */
  core: [string, string, string]
  particles: string
  glow: string
  /** Backdrop of the Live stage. */
  stageTop: string
  stageBottom: string
}

export const ORB_THEMES: Record<OrbThemeId, OrbTheme> = {
  aurora: {
    id: 'aurora',
    label: 'Aurora',
    core: ['#6d28d9', '#c084fc', '#7dd3fc'],
    particles: '#d8b4fe',
    glow: '#a855f7',
    stageTop: '#0b0718',
    stageBottom: '#3b1d6e',
  },
  ocean: {
    id: 'ocean',
    label: 'Oceano',
    core: ['#1e3a8a', '#3b82f6', '#a5f3fc'],
    particles: '#93c5fd',
    glow: '#3b82f6',
    stageTop: '#030712',
    stageBottom: '#0c3a7a',
  },
  sunset: {
    id: 'sunset',
    label: 'Pôr do sol',
    core: ['#be123c', '#fb7185', '#fde68a'],
    particles: '#fecdd3',
    glow: '#f97316',
    stageTop: '#12060a',
    stageBottom: '#6b1d2c',
  },
  mint: {
    id: 'mint',
    label: 'Menta',
    core: ['#065f46', '#10b981', '#ccfbf1'],
    particles: '#99f6e4',
    glow: '#14b8a6',
    stageTop: '#03100d',
    stageBottom: '#0d4a40',
  },
  mono: {
    id: 'mono',
    label: 'Grafite',
    core: ['#27272a', '#a1a1aa', '#fafafa'],
    particles: '#e4e4e7',
    glow: '#a1a1aa',
    stageTop: '#09090b',
    stageBottom: '#2a2a31',
  },
}

export const ORB_THEME_IDS = Object.keys(ORB_THEMES) as OrbThemeId[]

export function orbTheme(id: string | null | undefined): OrbTheme {
  return ORB_THEMES[(id ?? '') as OrbThemeId] ?? ORB_THEMES.aurora
}
