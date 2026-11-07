import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import {
  ancestorFolderIds,
  buildWorkbenchTree,
  filterWorkbenchTree,
  fileIconFor,
  flattenVisibleRows,
  statusLetterFor,
  type WorkbenchTreeFileItem,
  type WorkbenchTreeNode,
  type WorkbenchTreeRow,
} from './workbenchTreeModel'

/** Below this many files every folder starts expanded (the list is the agent's touched files). */
const AUTO_EXPAND_LIMIT = 60

export type WorkbenchFileTreeProps<T extends WorkbenchTreeFileItem> = {
  files: readonly T[]
  selectedPath: string | null
  onOpenFile: (path: string) => void
  query?: string
  rootName?: string | null
  onRefresh?: () => void
  describeFile?: (item: T) => string
  /** Rendered between the header and the tree (the filter input). */
  filterSlot?: ReactNode
  /** Shown instead of the tree when nothing is visible. */
  emptyMessage?: ReactNode
}

function ChevronIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
      <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
      {open ? (
        <path d="M1.5 4.5v8h11l2-5.5H4.2L2.5 12.5M1.5 4.5V3h4l1.2 1.5H12.5V7" fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
      ) : (
        <path d="M1.5 3h4l1.2 1.5h7.8v8h-13z" fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round" />
      )}
    </svg>
  )
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
      <path d="M13 8a5 5 0 1 1-1.5-3.55M13 2.5v2.5h-2.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function CollapseAllIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
      <path d="M5 2.5h8.5V11M2.5 5h8.5v8.5H2.5zM4.8 9.25h4" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function WorkbenchFileTree<T extends WorkbenchTreeFileItem>({
  files,
  selectedPath,
  onOpenFile,
  query = '',
  rootName,
  onRefresh,
  describeFile,
  filterSlot,
  emptyMessage,
}: WorkbenchFileTreeProps<T>) {
  const tree = useMemo(() => buildWorkbenchTree(files), [files])
  const filtering = query.trim().length > 0
  const visibleTree = useMemo(() => (filtering ? filterWorkbenchTree(tree, query) : tree), [filtering, query, tree])

  const [defaultOpen, setDefaultOpen] = useState<boolean>(() => files.length <= AUTO_EXPAND_LIMIT)
  const [overrides, setOverrides] = useState<ReadonlyMap<string, boolean>>(() => new Map())
  const [focusedId, setFocusedId] = useState<string | null>(null)
  const [revealedPath, setRevealedPath] = useState<string | null>(null)
  const rowRefs = useRef(new Map<string, HTMLLIElement>())
  const pendingFocus = useRef(false)

  // Reveal the selected file (e.g. opened from the chat's changed-files card): derived state, no effect.
  if (selectedPath && selectedPath !== revealedPath) {
    const ancestors = ancestorFolderIds(tree, selectedPath)
    if (ancestors.length > 0 || tree.some((node) => node.id === selectedPath)) {
      setRevealedPath(selectedPath)
      if (ancestors.some((id) => overrides.get(id) === false || (!defaultOpen && overrides.get(id) !== true))) {
        const next = new Map(overrides)
        for (const id of ancestors) next.set(id, true)
        setOverrides(next)
      }
    }
  }

  const isExpanded = (id: string): boolean => filtering || (overrides.get(id) ?? defaultOpen)
  const rows = flattenVisibleRows(visibleTree, isExpanded)
  const rowIndex = new Map(rows.map((row, index) => [row.node.id, index]))
  const activeId =
    focusedId && rowIndex.has(focusedId)
      ? focusedId
      : selectedPath && rowIndex.has(selectedPath)
        ? selectedPath
        : (rows[0]?.node.id ?? null)

  useEffect(() => {
    if (!pendingFocus.current || !activeId) return
    pendingFocus.current = false
    rowRefs.current.get(activeId)?.focus()
  }, [activeId, focusedId])

  const setExpanded = (id: string, expanded: boolean) => {
    setOverrides((current) => {
      const next = new Map(current)
      next.set(id, expanded)
      return next
    })
  }

  const moveFocus = (id: string | null | undefined) => {
    if (!id) return
    pendingFocus.current = true
    setFocusedId(id)
  }

  const activate = (row: WorkbenchTreeRow<T>) => {
    if (row.node.kind === 'folder') {
      setExpanded(row.node.id, !isExpanded(row.node.id))
    } else {
      onOpenFile(row.node.item.path)
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    if (rows.length === 0 || !activeId) return
    const index = rowIndex.get(activeId) ?? 0
    const row = rows[index]
    let handled = true
    switch (event.key) {
      case 'ArrowDown':
        moveFocus(rows[Math.min(rows.length - 1, index + 1)]?.node.id)
        break
      case 'ArrowUp':
        moveFocus(rows[Math.max(0, index - 1)]?.node.id)
        break
      case 'Home':
        moveFocus(rows[0]?.node.id)
        break
      case 'End':
        moveFocus(rows[rows.length - 1]?.node.id)
        break
      case 'ArrowRight':
        if (row.node.kind === 'folder') {
          if (!isExpanded(row.node.id)) setExpanded(row.node.id, true)
          else moveFocus(row.node.children[0]?.id)
        }
        break
      case 'ArrowLeft':
        if (row.node.kind === 'folder' && isExpanded(row.node.id) && !filtering) setExpanded(row.node.id, false)
        else moveFocus(row.parentId)
        break
      case 'Enter':
      case ' ':
        activate(row)
        break
      default:
        handled = false
    }
    if (handled) {
      event.preventDefault()
      event.stopPropagation()
    }
  }

  const collapseAll = () => {
    setDefaultOpen(false)
    setOverrides(new Map())
    setFocusedId(null)
  }

  const refresh = () => {
    setDefaultOpen(files.length <= AUTO_EXPAND_LIMIT)
    setOverrides(new Map())
    setRevealedPath(null)
    onRefresh?.()
  }

  const renderNodes = (nodes: readonly WorkbenchTreeNode<T>[], level: number, parentId: string | null) =>
    nodes.map((node, index) => {
      const isFolder = node.kind === 'folder'
      const expanded = isFolder && isExpanded(node.id)
      const isSelected = !isFolder && node.id === selectedPath
      const isFocused = node.id === activeId
      const status = isFolder ? undefined : node.item.status
      const letter = isFolder ? '' : statusLetterFor(status)
      const icon = isFolder ? null : fileIconFor(node.name)
      const title = isFolder ? node.id : describeFile ? describeFile(node.item) : node.item.path
      const rowClass = [
        'wb-tree__row',
        isSelected ? 'is-selected' : '',
        isFocused ? 'is-focused' : '',
        isFolder && node.changed ? 'has-changes' : '',
        !isFolder ? `is-${status ?? 'modified'}` : '',
      ]
        .filter(Boolean)
        .join(' ')
      const row: WorkbenchTreeRow<T> = { node, level, parentId, posInSet: index + 1, setSize: nodes.length }
      return (
        <li
          key={node.id}
          role="treeitem"
          aria-level={level}
          aria-posinset={index + 1}
          aria-setsize={nodes.length}
          aria-expanded={isFolder ? expanded : undefined}
          aria-selected={isSelected}
          aria-label={isFolder ? node.name : `${node.name}${letter ? ` (${status === 'added' ? 'adicionado' : status === 'deleted' ? 'removido' : 'modificado'})` : ''}`}
          tabIndex={isFocused ? 0 : -1}
          className="wb-tree__item"
          data-testid={isFolder ? `workbench-folder-item-${node.id}` : `workbench-file-item-${node.id}`}
          ref={(element) => {
            if (element) rowRefs.current.set(node.id, element)
            else rowRefs.current.delete(node.id)
          }}
          onClick={(event) => {
            event.stopPropagation()
            setFocusedId(node.id)
            activate(row)
          }}
        >
          <div className={rowClass} style={{ paddingLeft: `calc(var(--wb-tree-base) + ${level - 1} * var(--wb-tree-indent))` }} title={title}>
            <span className={isFolder ? (expanded ? 'wb-tree__chevron is-open' : 'wb-tree__chevron') : 'wb-tree__chevron is-empty'} aria-hidden="true">
              {isFolder && <ChevronIcon />}
            </span>
            {isFolder ? (
              <span className="wb-tree__folder-icon" aria-hidden="true">
                <FolderIcon open={expanded} />
              </span>
            ) : (
              <span className={`wb-file-icon is-${icon!.tone}`} aria-hidden="true">
                {icon!.label}
              </span>
            )}
            <span className="wb-tree__label">{node.name}</span>
            {letter && <span className={`wb-tree__decoration is-${status ?? 'modified'}`} aria-hidden="true">{letter}</span>}
            {isFolder && node.changed && !expanded && <span className="wb-tree__dot" aria-hidden="true" />}
          </div>
          {isFolder && expanded && node.children.length > 0 && (
            <ul role="group" className="wb-tree__group" style={{ ['--wb-guide-level' as string]: level }}>
              {renderNodes(node.children, level + 1, node.id)}
            </ul>
          )}
        </li>
      )
    })

  return (
    <section className="wb-explorer" data-testid="workbench-explorer" aria-label="Explorador de arquivos">
      <header className="wb-explorer__header">
        <span className="wb-explorer__title" title={rootName ?? undefined} data-testid="workbench-explorer-root">
          {rootName || 'Workspace'}
        </span>
        <div className="wb-explorer__actions">
          <button type="button" className="wb-explorer__action" aria-label="Atualizar explorador" title="Atualizar" data-testid="workbench-explorer-refresh" onClick={refresh}>
            <RefreshIcon />
          </button>
          <button type="button" className="wb-explorer__action" aria-label="Recolher todas as pastas" title="Recolher tudo" data-testid="workbench-explorer-collapse" onClick={collapseAll}>
            <CollapseAllIcon />
          </button>
        </div>
      </header>
      {filterSlot}
      {rows.length === 0 ? (
        <p className="wb-explorer__empty">{emptyMessage}</p>
      ) : (
        <ul role="tree" aria-label={rootName ? `Arquivos de ${rootName}` : 'Arquivos'} className="wb-tree" data-testid="workbench-file-tree" onKeyDown={handleKeyDown}>
          {renderNodes(visibleTree, 1, null)}
        </ul>
      )}
    </section>
  )
}
