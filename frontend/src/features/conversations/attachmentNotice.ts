export type AttachmentNoticeInput = {
  hasVisualAttachment: boolean
  modelSeesImages: boolean
  modelCallsTools: boolean
  visionModelName: string | null
}

/** What the composer says about how this file will actually be read. */
export function attachmentNotice(input: AttachmentNoticeInput): string | null {
  if (!input.hasVisualAttachment || input.modelSeesImages) return null
  if (!input.visionModelName) return 'Nenhum modelo de leitura visual disponível: configure um em Configurações.'
  const tail = input.modelCallsTools ? '.' : ' antes de enviar.'
  return `Este modelo não enxerga; o Orin vai ler com ${input.visionModelName}${tail}`
}
