/**
 * The delegation tree of a turn's subagents (a subagent may start its own). Items come back depth-first — each
 * parent followed by the agents it started — with their depth: 0 for an agent the main agent started.
 * An item whose parent is not in the list (the main agent, or an agent not shown) is a root.
 */
export function orderAgentTree<T>(
  items: readonly T[],
  idOf: (item: T) => string,
  parentOf: (item: T) => string | null | undefined,
): Array<{ item: T; depth: number }> {
  const ids = new Set(items.map(idOf))
  const children = new Map<string, T[]>()
  const roots: T[] = []
  for (const item of items) {
    const parent = parentOf(item)
    if (parent && parent !== idOf(item) && ids.has(parent)) {
      const siblings = children.get(parent) ?? []
      siblings.push(item)
      children.set(parent, siblings)
    } else {
      roots.push(item)
    }
  }
  const ordered: Array<{ item: T; depth: number }> = []
  const visited = new Set<string>()
  const visit = (item: T, depth: number) => {
    const id = idOf(item)
    if (visited.has(id)) return
    visited.add(id)
    ordered.push({ item, depth })
    for (const child of children.get(id) ?? []) visit(child, depth + 1)
  }
  for (const root of roots) visit(root, 0)
  // A cycle in bad data has no root: its agents still show, at the top level.
  for (const item of items) visit(item, 0)
  return ordered
}
