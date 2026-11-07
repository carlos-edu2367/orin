/**
 * Pure helpers behind the Bancada "Arquivos" explorer: path normalization, tree building
 * (folders first, case-insensitive, compact single-child folders like VS Code), visibility
 * flattening for keyboard navigation and file-type badges.
 */

export type WorkbenchTreeFileItem = {
  path: string
  status?: 'modified' | 'added' | 'deleted' | 'inspected'
}

export type WorkbenchTreeFolder<T extends WorkbenchTreeFileItem> = {
  kind: 'folder'
  /** Full path of the deepest folder of a compacted chain; unique across the tree. */
  id: string
  /** Label shown in the row, `src/pages` when single-child folders are compacted. */
  name: string
  children: WorkbenchTreeNode<T>[]
  /** True when any descendant was modified/added/deleted (not only inspected). */
  changed: boolean
}

export type WorkbenchTreeFile<T extends WorkbenchTreeFileItem> = {
  kind: 'file'
  id: string
  name: string
  item: T
}

export type WorkbenchTreeNode<T extends WorkbenchTreeFileItem> = WorkbenchTreeFolder<T> | WorkbenchTreeFile<T>

export type WorkbenchTreeRow<T extends WorkbenchTreeFileItem> = {
  node: WorkbenchTreeNode<T>
  level: number
  parentId: string | null
  posInSet: number
  setSize: number
}

function isAbsoluteLike(path: string): boolean {
  return /^[a-zA-Z]:\//.test(path) || path.startsWith('/')
}

/**
 * Normalizes a path reported by a tool event: Windows separators, `./` prefixes, duplicate
 * slashes, and absolute paths inside the conversation workspace become workspace-relative
 * (case-insensitive drive/prefix match so `C:\Repo\src\a.ts` and `src/a.ts` are one file).
 */
export function normalizeWorkbenchPath(raw: string, workspaceRoot?: string | null): string {
  let path = raw.trim().replace(/\\/g, '/').replace(/\/{2,}/g, '/')
  while (path.startsWith('./')) path = path.slice(2)
  if (workspaceRoot) {
    const root = workspaceRoot.trim().replace(/\\/g, '/').replace(/\/{2,}/g, '/').replace(/\/+$/, '')
    if (root && isAbsoluteLike(path)) {
      const lowerPath = path.toLowerCase()
      const lowerRoot = root.toLowerCase()
      if (lowerPath === lowerRoot) return ''
      if (lowerPath.startsWith(`${lowerRoot}/`)) path = path.slice(root.length + 1)
    }
  }
  return path.replace(/\/+$/, '')
}

const collator = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true })

function compareNodes<T extends WorkbenchTreeFileItem>(a: WorkbenchTreeNode<T>, b: WorkbenchTreeNode<T>): number {
  if (a.kind !== b.kind) return a.kind === 'folder' ? -1 : 1
  const byName = collator.compare(a.name, b.name)
  return byName !== 0 ? byName : a.name < b.name ? -1 : a.name > b.name ? 1 : 0
}

type MutableFolder<T extends WorkbenchTreeFileItem> = {
  path: string
  name: string
  folders: Map<string, MutableFolder<T>>
  files: Map<string, T>
}

function freeze<T extends WorkbenchTreeFileItem>(folder: MutableFolder<T>, compact: boolean): WorkbenchTreeNode<T>[] {
  const nodes: WorkbenchTreeNode<T>[] = []
  for (const child of folder.folders.values()) {
    let current = child
    let label = child.name
    while (compact && current.files.size === 0 && current.folders.size === 1) {
      const only = current.folders.values().next().value as MutableFolder<T>
      label = `${label}/${only.name}`
      current = only
    }
    const children = freeze(current, compact)
    nodes.push({
      kind: 'folder',
      id: current.path,
      name: label,
      children,
      changed: children.some((node) => (node.kind === 'folder' ? node.changed : node.item.status !== 'inspected')),
    })
  }
  for (const [name, item] of folder.files) {
    nodes.push({ kind: 'file', id: item.path, name, item })
  }
  return nodes.sort(compareNodes)
}

/** Builds the explorer tree from a flat list of workspace-relative file paths. */
export function buildWorkbenchTree<T extends WorkbenchTreeFileItem>(
  files: readonly T[],
  options: { compact?: boolean } = {},
): WorkbenchTreeNode<T>[] {
  const root: MutableFolder<T> = { path: '', name: '', folders: new Map(), files: new Map() }
  for (const item of files) {
    const segments = item.path.split('/').filter(Boolean)
    if (segments.length === 0) continue
    let folder = root
    for (const segment of segments.slice(0, -1)) {
      let next = folder.folders.get(segment)
      if (!next) {
        next = { path: folder.path ? `${folder.path}/${segment}` : segment, name: segment, folders: new Map(), files: new Map() }
        folder.folders.set(segment, next)
      }
      folder = next
    }
    folder.files.set(segments[segments.length - 1], item)
  }
  return freeze(root, options.compact ?? true)
}

/** Folder ids that contain `path` (so the explorer can reveal a selected file). */
export function ancestorFolderIds<T extends WorkbenchTreeFileItem>(nodes: readonly WorkbenchTreeNode<T>[], path: string): string[] {
  for (const node of nodes) {
    if (node.kind === 'file') {
      if (node.id === path) return []
      continue
    }
    if (path.startsWith(`${node.id}/`)) {
      return [node.id, ...ancestorFolderIds(node.children, path)]
    }
  }
  return []
}

/** Keeps files whose path contains `query` (case-insensitive) and the folders leading to them. */
export function filterWorkbenchTree<T extends WorkbenchTreeFileItem>(nodes: readonly WorkbenchTreeNode<T>[], query: string): WorkbenchTreeNode<T>[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return [...nodes]
  const result: WorkbenchTreeNode<T>[] = []
  for (const node of nodes) {
    if (node.kind === 'file') {
      if (node.item.path.toLowerCase().includes(needle)) result.push(node)
    } else {
      const children = filterWorkbenchTree(node.children, needle)
      if (children.length > 0) result.push({ ...node, children })
    }
  }
  return result
}

/** Rows currently visible (children of collapsed folders are not rendered: lazy expand). */
export function flattenVisibleRows<T extends WorkbenchTreeFileItem>(
  nodes: readonly WorkbenchTreeNode<T>[],
  isExpanded: (id: string) => boolean,
  level = 1,
  parentId: string | null = null,
  into: WorkbenchTreeRow<T>[] = [],
): WorkbenchTreeRow<T>[] {
  nodes.forEach((node, index) => {
    into.push({ node, level, parentId, posInSet: index + 1, setSize: nodes.length })
    if (node.kind === 'folder' && isExpanded(node.id)) {
      flattenVisibleRows(node.children, isExpanded, level + 1, node.id, into)
    }
  })
  return into
}

export type WorkbenchFileIcon = { label: string; tone: string }

const ICONS_BY_EXTENSION: Record<string, WorkbenchFileIcon> = {
  ts: { label: 'TS', tone: 'ts' },
  mts: { label: 'TS', tone: 'ts' },
  cts: { label: 'TS', tone: 'ts' },
  tsx: { label: 'TS', tone: 'tsx' },
  js: { label: 'JS', tone: 'js' },
  mjs: { label: 'JS', tone: 'js' },
  cjs: { label: 'JS', tone: 'js' },
  jsx: { label: 'JS', tone: 'jsx' },
  json: { label: '{}', tone: 'json' },
  jsonc: { label: '{}', tone: 'json' },
  css: { label: '#', tone: 'css' },
  scss: { label: '#', tone: 'scss' },
  sass: { label: '#', tone: 'scss' },
  less: { label: '#', tone: 'css' },
  md: { label: 'MD', tone: 'md' },
  mdx: { label: 'MD', tone: 'md' },
  py: { label: 'PY', tone: 'py' },
  pyi: { label: 'PY', tone: 'py' },
  html: { label: '<>', tone: 'html' },
  htm: { label: '<>', tone: 'html' },
  vue: { label: 'V', tone: 'vue' },
  svelte: { label: 'S', tone: 'svelte' },
  svg: { label: '<>', tone: 'svg' },
  yml: { label: 'Y', tone: 'yaml' },
  yaml: { label: 'Y', tone: 'yaml' },
  toml: { label: 'T', tone: 'config' },
  ini: { label: 'T', tone: 'config' },
  env: { label: '$', tone: 'config' },
  sh: { label: '$', tone: 'shell' },
  bash: { label: '$', tone: 'shell' },
  ps1: { label: '$', tone: 'shell' },
  go: { label: 'GO', tone: 'go' },
  rs: { label: 'RS', tone: 'rust' },
  java: { label: 'J', tone: 'java' },
  rb: { label: 'RB', tone: 'ruby' },
  php: { label: 'P', tone: 'php' },
  sql: { label: 'DB', tone: 'config' },
  txt: { label: '≡', tone: 'text' },
  lock: { label: '≡', tone: 'text' },
  png: { label: '▣', tone: 'image' },
  jpg: { label: '▣', tone: 'image' },
  jpeg: { label: '▣', tone: 'image' },
  gif: { label: '▣', tone: 'image' },
  webp: { label: '▣', tone: 'image' },
  ico: { label: '▣', tone: 'image' },
}

/** Small colored text badge for a file name (no icon font/dependency). */
export function fileIconFor(name: string): WorkbenchFileIcon {
  const lower = name.toLowerCase()
  if (lower === 'dockerfile' || lower.startsWith('dockerfile.')) return { label: 'D', tone: 'docker' }
  if (lower.startsWith('.env')) return { label: '$', tone: 'config' }
  if (lower === '.gitignore' || lower === '.gitattributes') return { label: 'G', tone: 'git' }
  const dot = lower.lastIndexOf('.')
  const extension = dot > 0 ? lower.slice(dot + 1) : ''
  return ICONS_BY_EXTENSION[extension] ?? { label: '·', tone: 'default' }
}

export function statusLetterFor(status?: WorkbenchTreeFileItem['status']): string {
  switch (status) {
    case 'added':
      return 'A'
    case 'deleted':
      return 'D'
    case 'inspected':
      return ''
    default:
      return 'M'
  }
}
