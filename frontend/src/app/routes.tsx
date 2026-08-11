import type { ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { collaborationFixtureEvents, fixtureExecutions } from '../features/executions/fixtures'
import { ExecutionPage } from '../features/executions/ExecutionPage'
import { ExecutionRoute } from '../features/executions/ExecutionRoute'
import { ProviderSettingsPage } from '../features/providers/ProviderSettingsPage'
import { Home } from './Home'
import { ChatPage } from '../features/conversations/ChatPage'
import { SkillsPage } from '../features/skills/SkillsPage'
import { ProjectPage } from '../features/projects/ProjectPage'
import { SettingsPage, SettingsPlaceholder } from '../features/settings/SettingsPage'
import { RuntimeSettingsPage } from '../features/settings/RuntimeSettingsPage'
import { MemoryPage } from '../features/memory/MemoryPage'

export type RouteDefinition = {
  path: string
  element: ReactNode
}

export const routes: RouteDefinition[] = [
  { path: '/', element: <Home /> },
  { path: '/chats/:conversationId', element: <ChatPage /> },
  { path: '/chats/:conversationId/overview', element: <ChatPage /> },
  { path: '/projects/:projectId/chats/:conversationId', element: <ChatPage /> },
  { path: '/projects/:projectId/chats/:conversationId/overview', element: <ChatPage /> },
  { path: '/projects/:projectId', element: <ProjectPage /> },
  { path: '/projects/:projectId/memory', element: <ProjectMemoryRoute /> },
  { path: '/settings', element: <SettingsPage /> },
  { path: '/settings/general', element: <RuntimeSettingsPage /> },
  { path: '/settings/memory', element: <MemoryPage /> },
  { path: '/settings/providers', element: <ProviderSettingsPage /> },
  { path: '/settings/omniroute', element: <ProviderSettingsPage /> },
  { path: '/settings/skills', element: <SkillsPage /> },
  { path: '/settings/agents', element: <SettingsPlaceholder title="Agents" description="Configurações específicas de agentes ficam no contexto de cada agente." /> },
  { path: '/settings/workspace', element: <SettingsPlaceholder title="Workspace" description="Projetos e workspaces continuam contextualizados." /> },
  { path: '/settings/advanced', element: <SettingsPlaceholder title="Advanced" description="Nenhuma opção técnica adicional está exposta sem suporte do runtime." /> },
  { path: '/providers', element: <ProviderSettingsPage /> },
  { path: '/skills', element: <SkillsPage /> },
  { path: '/skills/:skillId', element: <SkillsPage /> },
  // Deterministic execution fixtures. They render the legacy execution surface
  // without a backend and exist for visual/a11y regression runs only.
  { path: '/execution/fixture-running', element: <ExecutionPage execution={fixtureExecutions.running} /> },
  { path: '/execution/fixture-waiting', element: <ExecutionPage execution={fixtureExecutions.waiting} /> },
  { path: '/execution/fixture-completed', element: <ExecutionPage execution={fixtureExecutions.completed} /> },
  { path: '/execution/fixture-failed', element: <ExecutionPage execution={fixtureExecutions.failed} /> },
  { path: '/execution/fixture-cancelled', element: <ExecutionPage execution={fixtureExecutions.cancelled} /> },
  { path: '/execution/fixture-collaborating', element: <ExecutionPage execution={fixtureExecutions.running} events={collaborationFixtureEvents} /> },
  { path: '/execution/:executionId', element: <ExecutionRoute /> },
]

function ProjectMemoryRoute() {
  const { projectId = '' } = useParams()
  return <MemoryPage projectId={projectId} />
}
