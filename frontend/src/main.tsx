import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './app/App'
import { ThemeProvider, initThemeDom } from './app/ThemeContext'
import './styles/theme.css'
import './styles/index.css'
// Loaded after the Tailwind layers so the product's component styles win over
// preflight without any !important.
import './styles/agentos.css'
import './styles/live.css'

initThemeDom()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>,
)
