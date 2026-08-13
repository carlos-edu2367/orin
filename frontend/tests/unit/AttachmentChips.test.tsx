import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AttachmentChips, type ComposerAttachment } from '../../src/features/conversations/AttachmentChips'
import { Composer } from '../../src/features/conversations/Composer'

const ready: ComposerAttachment = { id: 'a1', filename: 'foto.png', kind: 'image', bytes: 2048, state: 'ready', upload_id: 'upl_1' }

describe('AttachmentChips', () => {
  it('lists each attachment with its name and size', () => {
    render(<AttachmentChips items={[ready]} onRemove={() => {}} />)
    expect(screen.getByText('foto.png')).toBeInTheDocument()
    expect(screen.getByText('2 KB')).toBeInTheDocument()
  })

  it('removes an attachment', () => {
    const onRemove = vi.fn()
    render(<AttachmentChips items={[ready]} onRemove={onRemove} />)
    fireEvent.click(screen.getByRole('button', { name: 'Remover foto.png' }))
    expect(onRemove).toHaveBeenCalledWith('a1')
  })

  it('shows the failure of one file without hiding the others', () => {
    const failed: ComposerAttachment = { id: 'a2', filename: 'setup.exe', kind: 'text', bytes: 10, state: 'failed', error: 'Tipo não aceito' }
    render(<AttachmentChips items={[ready, failed]} onRemove={() => {}} />)
    expect(screen.getByText('Tipo não aceito')).toBeInTheDocument()
    expect(screen.getByText('foto.png')).toBeInTheDocument()
  })
})

describe('Composer with attachments', () => {
  it('allows sending with no text when a file is attached', () => {
    const onSubmit = vi.fn()
    render(<Composer value="" onChange={() => {}} onSubmit={onSubmit} attachments={[ready]} onAttach={() => {}} onRemoveAttachment={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Enviar mensagem' }))
    expect(onSubmit).toHaveBeenCalled()
  })

  it('still refuses to send an empty composer', () => {
    const onSubmit = vi.fn()
    render(<Composer value="   " onChange={() => {}} onSubmit={onSubmit} attachments={[]} onAttach={() => {}} onRemoveAttachment={() => {}} />)
    expect(screen.getByRole('button', { name: 'Enviar mensagem' })).toBeDisabled()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('attaches an image pasted from the clipboard', () => {
    const onAttach = vi.fn()
    render(<Composer value="" onChange={() => {}} onSubmit={() => {}} attachments={[]} onAttach={onAttach} onRemoveAttachment={() => {}} />)
    const file = new File([new Uint8Array([1])], 'print.png', { type: 'image/png' })
    fireEvent.paste(screen.getByLabelText('Mensagem'), { clipboardData: { files: [file], items: [] } })
    expect(onAttach).toHaveBeenCalledWith([file])
  })
})
