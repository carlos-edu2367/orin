import { useMemo } from 'react'
import { createBrowserApiClient } from '../../api/client'
import { createSkillsClient, type SkillsClient } from '../../api/skills'
import { SkillsPage } from './SkillsPage'

export function SkillsSection({ client }: { client?: SkillsClient }) {
  const apiClient = useMemo(() => client ?? createSkillsClient(createBrowserApiClient()), [client])
  return <SkillsPage client={apiClient} embedded />
}
