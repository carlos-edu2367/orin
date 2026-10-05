// Short, friendly names for the launcher's services. The raw detail the launcher
// reports is English-ish and technical, so it only surfaces when a step fails.
const SERVICES = [
  ['database', 'Banco de dados', 'Abrindo o banco de dados local'],
  ['migrations', 'Atualizações', 'Aplicando atualizações do banco'],
  ['backend', 'API local', 'Iniciando a API local'],
  ['health', 'Verificação', 'Verificando a saúde do sistema'],
  ['ready', 'Aplicação', 'Preparando a aplicação'],
  ['worker', 'Worker', 'Iniciando o worker'],
  ['scheduler', 'Agendador', 'Ligando as tarefas agendadas'],
  ['frontend', 'Interface', 'Carregando a interface'],
]
const RING = 2 * Math.PI * 52

const card = document.querySelector('.splash-card')
const title = document.querySelector('#title')
const message = document.querySelector('#message')
const steps = document.querySelector('#steps')
const progressBar = document.querySelector('#progress-bar')
const progressTrack = document.querySelector('.progress')
const percent = document.querySelector('#percent')
const orbProgress = document.querySelector('#orb-progress')
const actions = document.querySelector('#failure-actions')
const tip = document.querySelector('#tip')
let loaded = false
let shown = 0

orbProgress.style.strokeDasharray = String(RING)
orbProgress.style.strokeDashoffset = String(RING)

document.querySelector('#retry').addEventListener('click', async () => {
  message.textContent = 'Reiniciando o Orin…'
  await window.orinDesktop.retry()
})
document.querySelector('#open-logs').addEventListener('click', () => window.orinDesktop.openLogs())
document.querySelector('#close').addEventListener('click', () => window.orinDesktop.close())

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
  const entries = SERVICES.filter(([id]) => status.services[id])
  const ready = entries.filter(([id]) => status.services[id].state === 'ready').length
  const failed = entries.find(([id]) => status.services[id].state === 'error')
  const current = entries.find(([id]) => status.services[id].state === 'starting')
  const done = status.mode === 'ready'
  const target = done ? 100 : Math.min(96, Math.round(((ready + (current ? 0.5 : 0)) / entries.length) * 100))
  // Never move backwards: services briefly flip back to "starting" while retrying.
  shown = Math.max(shown, target)

  const phase = status.mode === 'error' || failed ? 'error' : done ? 'ready' : status.mode === 'stopped' ? 'stopped' : 'starting'
  card.dataset.phase = phase
  title.textContent = { error: 'Não foi possível iniciar', ready: 'Tudo pronto', stopped: 'Orin encerrado', starting: 'Preparando seu ambiente' }[phase]
  message.textContent = phase === 'error'
    ? status.message || 'Algo impediu o Orin de iniciar.'
    : phase === 'ready' ? 'Abrindo o Orin…'
    : phase === 'stopped' ? status.message || 'A inicialização foi cancelada.'
    : current ? `${current[2]}…` : 'Preparando…'

  progressBar.style.width = `${shown}%`
  progressTrack.setAttribute('aria-valuenow', String(shown))
  percent.textContent = String(shown)
  orbProgress.style.strokeDashoffset = String(RING * (1 - shown / 100))

  steps.replaceChildren(...entries.map(([id, label]) => stepElement(label, status.services[id])))
  actions.hidden = !(phase === 'error' || phase === 'stopped')
  tip.hidden = phase === 'error' || phase === 'stopped'
}

function stepElement(label, service) {
  const item = document.createElement('li')
  const state = service.state || 'pending'
  item.className = `step step--${state}`
  const icon = document.createElement('span')
  icon.className = 'step-icon'
  icon.setAttribute('aria-hidden', 'true')
  icon.textContent = state === 'ready' ? '✓' : state === 'error' ? '×' : ''
  const text = document.createElement('span')
  text.className = 'step-label'
  text.textContent = label
  item.append(icon, text)
  item.title = state === 'error' && service.detail ? service.detail : stateLabel(state)
  return item
}

function stateLabel(state) {
  return { pending: 'Aguardando', starting: 'Em andamento', ready: 'Concluído', error: 'Não foi possível concluir', stopped: 'Cancelado' }[state] || ''
}

refresh()
setInterval(refresh, 350)
