import { useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import type { ApiClient } from '../../api/client'
import { listProjectSidebar, type ProjectChat } from '../../api/projects'

export type PipelineScope = {
  /** The conversation whose folder, permission mode and subscription the pipelines are read and run against. */
  conversationId: string | null
  projectId: string | null
  projectName: string | null
  /** The chats of the project (newest first), to pick the conversation a run goes to. */
  chats: ProjectChat[]
  loading: boolean
  setConversation: (conversationId: string) => void
}

function newestFirst(chats: readonly ProjectChat[]): ProjectChat[] {
  return [...chats].sort((a, b) => (Date.parse(b.updated_at ?? '') || 0) - (Date.parse(a.updated_at ?? '') || 0))
}

/**
 * Where the pipeline screens look: `?conversation=` wins; on a project route the newest chat of the project stands in,
 * because the folder (and so the project's own pipelines and their trust) belongs to a conversation, not to the project.
 */
export function usePipelineScope(client: ApiClient): PipelineScope {
  const { projectId = null } = useParams()
  const [search, setSearch] = useSearchParams()
  const explicit = search.get('conversation')
  const [chats, setChats] = useState<ProjectChat[]>([])
  const [projectName, setProjectName] = useState<string | null>(null)
  const [loading, setLoading] = useState(Boolean(projectId))

  useEffect(() => {
    if (!projectId) return undefined
    let cancelled = false
    listProjectSidebar(client)
      .then(({ items }) => {
        if (cancelled) return
        const found = items.find((item) => item.project_id === projectId)
        setChats(newestFirst(found?.chats ?? []))
        setProjectName(found?.name ?? null)
      })
      .catch(() => { if (!cancelled) setChats([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [client, projectId])

  const conversationId = explicit ?? (projectId ? chats[0]?.conversation_id ?? null : null)

  return useMemo(
    () => ({
      conversationId,
      projectId,
      projectName,
      chats,
      loading,
      setConversation: (id: string) => {
        const next = new URLSearchParams(search)
        next.set('conversation', id)
        setSearch(next, { replace: true })
      },
    }),
    [conversationId, projectId, projectName, chats, loading, search, setSearch],
  )
}
