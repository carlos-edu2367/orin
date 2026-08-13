const SERVICES = [
  ['database', 'Banco de dados local'],
  ['migrations', 'Atualizações do banco'],
  ['backend', 'API local'],
  ['health', 'Verificação de saúde'],
  ['ready', 'Aplicação pronta'],
  ['worker', 'Worker'],
  ['scheduler', 'Tarefas agendadas'],
  ['frontend', 'Interface do Orin'],
]

const message = document.querySelector('#message')
const steps = document.querySelector('#steps')
const progressBar = document.querySelector('#progress-bar')
const actions = document.querySelector('#failure-actions')
let loaded = false

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
    setTimeout(() => window.orinDesktop.loadApp(status.url), 220)
  }
}

function render(status) {
  message.textContent = status.message || 'Preparando seu ambiente'
  const entries = SERVICES.filter(([id]) => status.services[id])
  const readyCount = entries.filter(([id]) => status.services[id].state === 'ready').length
  progressBar.style.width = `${Math.max(8, Math.round((readyCount / entries.length) * 100))}%`
  steps.replaceChildren(...entries.map(([id, label]) => serviceElement(label, status.services[id])))
  actions.hidden = status.mode !== 'error'
  if (status.mode === 'stopped') actions.hidden = false
}

function serviceElement(label, service) {
  const item = document.createElement('li')
  const state = service.state || 'pending'
  item.className = `step step--${state}`
  const icon = state === 'ready' ? '✓' : state === 'error' ? '×' : state === 'starting' ? '●' : '○'
  item.innerHTML = `<span class="step-icon" aria-hidden="true">${icon}</span><span class="step-label">${label}<small class="step-detail"></small></span>`
  item.querySelector('.step-detail').textContent = service.detail || stateLabel(state)
  return item
}

function stateLabel(state) {
  return { pending: 'Aguardando', starting: 'Em andamento', ready: 'Concluído', error: 'Não foi possível concluir', stopped: 'Cancelado' }[state] || ''
}

refresh()
setInterval(refresh, 350)
