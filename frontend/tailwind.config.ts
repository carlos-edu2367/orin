import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: 'var(--orin-ink)',
        night: 'var(--orin-surface)',
        accent: 'var(--orin-accent)',
        orin: {
          ink: 'var(--orin-ink)',
          surface: 'var(--orin-surface-1)',
          raised: 'var(--orin-surface-2)',
          'raised-strong': 'var(--orin-surface-3)',
          hover: 'var(--orin-surface-hover)',
          border: 'var(--orin-border)',
          'border-subtle': 'var(--orin-border-subtle)',
          'border-strong': 'var(--orin-border-strong)',
          text: 'var(--orin-text-primary)',
          secondary: 'var(--orin-text-secondary)',
          muted: 'var(--orin-text-muted)',
          disabled: 'var(--orin-text-disabled)',
          accent: 'var(--orin-accent)',
          'accent-strong': 'var(--orin-accent-strong)',
          'accent-soft': 'var(--orin-accent-soft)',
          'accent-bg': 'var(--orin-accent-bg)',
          success: 'var(--orin-success)',
          warn: 'var(--orin-warn)',
          danger: 'var(--orin-danger)',
          info: 'var(--orin-info)',
        },
      },
      opacity: {
        11: '0.11',
        12: '0.12',
        14: '0.14',
        15: '0.15',
        18: '0.18',
        35: '0.35',
      },
    },
  },
  plugins: [],
} satisfies Config
