export type ComposerAttachment = {
  /** Client-side identity, stable from the moment the file is picked. */
  id: string
  filename: string
  kind: 'text' | 'image' | 'pdf' | 'office'
  bytes: number
  state: 'uploading' | 'ready' | 'failed'
  upload_id?: string
  previewUrl?: string
  error?: string
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const GLYPH: Record<ComposerAttachment['kind'], string> = { text: '≡', image: '▣', pdf: '❐', office: '▤' }

/** The files waiting to be sent, shown under the composer input. */
export function AttachmentChips({ items, onRemove }: { items: ComposerAttachment[]; onRemove: (id: string) => void }) {
  if (items.length === 0) return null
  return (
    <ul className="composer__attachments">
      {items.map((item) => (
        <li key={item.id} className={`attachment-chip attachment-chip--${item.state}`}>
          {item.previewUrl
            ? <img className="attachment-chip__thumb" src={item.previewUrl} alt="" />
            : <span className="attachment-chip__glyph" aria-hidden="true">{GLYPH[item.kind]}</span>}
          <span className="attachment-chip__name">{item.filename}</span>
          <span className="attachment-chip__size">{item.state === 'failed' ? item.error : formatBytes(item.bytes)}</span>
          <button type="button" className="attachment-chip__remove" aria-label={`Remover ${item.filename}`} onClick={() => onRemove(item.id)}>×</button>
        </li>
      ))}
    </ul>
  )
}
