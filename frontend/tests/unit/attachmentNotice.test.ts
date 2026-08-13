import { describe, expect, it } from 'vitest'
import { attachmentNotice } from '../../src/features/conversations/attachmentNotice'

describe('attachmentNotice', () => {
  it('says nothing when there is no attachment', () => {
    expect(attachmentNotice({ hasVisualAttachment: false, modelSeesImages: false, modelCallsTools: true, visionModelName: 'qwen2.5-vl' })).toBeNull()
  })

  it('says nothing when the model can see', () => {
    expect(attachmentNotice({ hasVisualAttachment: true, modelSeesImages: true, modelCallsTools: true, visionModelName: 'qwen2.5-vl' })).toBeNull()
  })

  it('names the model that will read for a text-only model', () => {
    expect(attachmentNotice({ hasVisualAttachment: true, modelSeesImages: false, modelCallsTools: true, visionModelName: 'qwen2.5-vl' }))
      .toBe('Este modelo não enxerga; o Orin vai ler com qwen2.5-vl.')
  })

  it('warns that the read happens before sending when the model has no tools', () => {
    expect(attachmentNotice({ hasVisualAttachment: true, modelSeesImages: false, modelCallsTools: false, visionModelName: 'qwen2.5-vl' }))
      .toBe('Este modelo não enxerga; o Orin vai ler com qwen2.5-vl antes de enviar.')
  })

  it('points to settings when no vision model is available', () => {
    expect(attachmentNotice({ hasVisualAttachment: true, modelSeesImages: false, modelCallsTools: true, visionModelName: null }))
      .toBe('Nenhum modelo de leitura visual disponível: configure um em Configurações.')
  })
})
