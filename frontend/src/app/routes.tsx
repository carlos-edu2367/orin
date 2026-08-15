import type { ReactNode } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { useMemo } from 'react'
import { createBrowserApiClient } from '../api/client'
import { collaborationFixtureEvents, fixtureExecutions } from '../features/executions/fixtures'
import { ExecutionPage } from '../features/executions/ExecutionPage'
import { ExecutionRoute } from '../features/executions/ExecutionRoute'
import { ProvidersSection } from '../features/providers/ProvidersSection'
import { ProviderDetail } from '../features/providers/ProviderDetail'
import { VisionModelSetting } from '../features/providers/VisionModelSetting'
import { PROVIDER_NAMES, type ProviderName } from '../api/providers'
import { Home } from './Home'
import { ChatPage } from '../features/conversations/ChatPage'
import { SkillsSection } from '../features/skills/SkillsSection'
import { ProjectPage } from '../features/projects/ProjectPage'
import { RuntimeSettingsPage } from '../features/settings/RuntimeSettingsPage'
import { SettingsShell } from '../features/settings/SettingsShell'
import { SettingsSection } from '../features/settings/SettingsSection'
import { useSettingsBadges } from '../features/settings/useSettingsBadges'
import { AboutSection } from '../features/settings/AboutSection'
import { WorkspaceSection } from '../features/settings/WorkspaceSection'
import { MemoryPage } from '../features/memory/MemoryPage'
import { SchedulesPage } from '../features/schedules/SchedulesPage'
import { McpSection } from '../features/mcp/McpSection'
import { PluginsSection } from '../features/plugins/PluginsSection'

export type RouteDefinition = {
  path: string
  element: ReactNode
}

export const routes: RouteDefinition[] = [
  { path: '/', element: <Home /> },
  { path: '/chats/:conversationId', element: <ConversationRoute /> },
  { path: '/chats/:conversationId/overview', element: <ConversationRoute /> },
  { path: '/projects/:projectId/chats/:conversationId', element: <ConversationRoute /> },
  { path: '/projects/:projectId/chats/:conversationId/overview', element: <ConversationRoute /> },
  { path: '/projects/:projectId', element: <ProjectPage /> },
  { path: '/projects/:projectId/memory', element: <ProjectMemoryRoute /> },
  { path: '/settings', element: <Navigate to="/settings/general" replace /> },
  { path: '/settings/general', element: <SettingsRoute><RuntimeSettingsPage embedded /></SettingsRoute> },
  { path: '/settings/memory', element: <SettingsRoute><MemoryPage embedded /></SettingsRoute> },
  { path: '/settings/providers', element: <ProviderSettingsRoute /> },
  { path: '/settings/providers/:provider', element: <ProviderSettingsRoute /> },
  { path: '/settings/omniroute', element: <Navigate to="/settings/providers/omniroute" replace /> },
  { path: '/settings/skills', element: <SettingsRoute><SkillsSection /></SettingsRoute> },
  { path: '/settings/skills/:skillId', element: <SettingsRoute><SkillsSection /></SettingsRoute> },
  { path: '/settings/vision', element: <VisionSettingsRoute /> },
  { path: '/settings/mcp', element: <SettingsRoute><McpSection /></SettingsRoute> },
  { path: '/settings/plugins', element: <SettingsRoute><PluginsSection /></SettingsRoute> },
  { path: '/settings/agents', element: <Navigate to="/settings/general" replace /> },
  { path: '/settings/workspace', element: <SettingsRoute><WorkspaceSection /></SettingsRoute> },
  { path: '/settings/schedules', element: <SettingsRoute><SchedulesPage embedded /></SettingsRoute> },
  { path: '/settings/about', element: <SettingsRoute><AboutSection /></SettingsRoute> },
  { path: '/providers', element: <Navigate to="/settings/providers" replace /> },
  { path: '/skills', element: <Navigate to="/settings/skills" replace /> },
  { path: '/skills/:skillId', element: <Navigate to="/settings/skills" replace /> },
  { path: '/schedules', element: <Navigate to="/settings/schedules" replace /> },
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

function ConversationRoute() {
  const { conversationId = '', projectId = '' } = useParams()
  // Switching conversations must not leave a previous transcript visible while
  // the next snapshot is still loading. Toggling the overview keeps this key.
  return <ChatPage key={`${projectId}:${conversationId}`} />
}

function SettingsRoute({ children }: { children: ReactNode }) {
  const badges = useSettingsBadges()
  return <SettingsShell badges={badges}>{children}</SettingsShell>
}

function ProviderSettingsRoute() {
  const { provider: rawProvider } = useParams()
  const navigate = useNavigate()
  const client = useMemo(() => createBrowserApiClient(), [])
  const provider = PROVIDER_NAMES.includes(rawProvider as ProviderName) ? rawProvider as ProviderName : null
  const badges = useSettingsBadges()
  if (rawProvider && !provider) return <Navigate to="/settings/providers" replace />
  return <SettingsShell badges={badges} drawer={provider ? <ProviderDetail provider={provider} client={client} onClose={() => navigate('/settings/providers')} /> : undefined}><ProvidersSection client={client} /></SettingsShell>
}

function VisionSettingsRoute() {
  return <SettingsRoute><SettingsSection eyebrow="INTELIGÊNCIA / LEITURA VISUAL"><VisionModelSettingRouteContent /></SettingsSection></SettingsRoute>
}

function VisionModelSettingRouteContent() {
  const client = useMemo(() => createBrowserApiClient(), [])
  return <VisionModelSetting client={client} />
}
