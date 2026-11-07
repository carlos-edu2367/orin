// The visible UI is just the animated mascot and "Aguarde…". Technical detail
// (service states, launcher messages) is only surfaced behind "Ver detalhes" when startup fails.
const SERVICE_LABELS = {
  database: 'Banco de dados',
  migrations: 'Atualizações',
  backend: 'API local',
  health: 'Verificação',
  ready: 'Aplicação',
  worker: 'Worker',
  scheduler: 'Agendador',
  frontend: 'Interface',
}

const card = document.querySelector('.splash')
const title = document.querySelector('#title')
const waitLabel = document.querySelector('#wait-label')
const failure = document.querySelector('#failure')
const details = document.querySelector('#details')
const toggle = document.querySelector('#toggle-details')
let loaded = false

document.querySelector('#retry').addEventListener('click', async () => {
  card.dataset.phase = 'starting'
  failure.hidden = true
  waitLabel.textContent = 'Reiniciando o Orin'
  await window.orinDesktop.retry()
})
document.querySelector('#open-logs').addEventListener('click', () => window.orinDesktop.openLogs())
document.querySelector('#close').addEventListener('click', () => window.orinDesktop.close())
toggle.addEventListener('click', () => {
  const open = details.hidden
  details.hidden = !open
  toggle.setAttribute('aria-expanded', String(open))
  toggle.textContent = open ? 'Ocultar detalhes' : 'Ver detalhes'
})

async function refresh() {
  const status = await window.orinDesktop.startupStatus()
  if (!status || !status.services) return
  render(status)
  if (status.mode === 'ready' && status.url && !loaded) {
    loaded = true
    setTimeout(() => window.orinDesktop.loadApp(status.url), 420)
  }
}

function render(status) {
  const entries = Object.entries(status.services)
  const failed = entries.some(([, s]) => s && s.state === 'error')
  const phase = status.mode === 'error' || failed ? 'error' : status.mode === 'stopped' ? 'stopped' : status.mode === 'ready' ? 'ready' : 'starting'
  showPhase(phase, status, entries)
}

function showPhase(phase, status, entries) {
  const bad = phase === 'error' || phase === 'stopped'
  card.dataset.phase = bad ? 'error' : phase
  failure.hidden = !bad
  title.textContent = phase === 'stopped' ? 'Orin encerrado' : 'Não foi possível iniciar o Orin'
  if (!bad) {
    waitLabel.textContent = 'Aguarde'
    return
  }
  const lines = [status.message || (phase === 'stopped' ? 'A inicialização foi cancelada.' : 'Algo impediu o Orin de iniciar.')]
  for (const [id, s] of entries || []) {
    if (!s) continue
    lines.push(`${SERVICE_LABELS[id] || id}: ${s.state || 'pending'}${s.detail ? ` — ${s.detail}` : ''}`)
  }
  details.textContent = lines.join('\n')
}

refresh()
setInterval(refresh, 350)
