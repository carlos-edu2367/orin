import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#f4f0e9',
        night: '#0b0d10',
        accent: '#c8ff6a',
      },
    },
  },
  plugins: [],
} satisfies Config
