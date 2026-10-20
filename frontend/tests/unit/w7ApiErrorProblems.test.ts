import { describe, expect, it } from 'vitest'
import { ApiError, parseApiError } from '../../src/api/errors'

describe('W7: an API error keeps what the server named as wrong', () => {
  it('reads details.problems from a 422', () => {
    const error = parseApiError(422, { error: { code: 'invalid_rule', category: 'VALIDATION', details: { problems: ["'Bash(' is not a permission rule"] } } })
    expect(error.problems).toEqual(["'Bash(' is not a permission rule"])
    expect(error.code).toBe('invalid_rule')
  })

  it('bounds and cleans what it keeps', () => {
    const many = Array.from({ length: 80 }, (_, index) => `${'x'.repeat(600)}${index}`)
    const error = parseApiError(422, { error: { code: 'invalid_hooks', details: { problems: [...many, 3, '', null] } } })
    expect(error.problems).toHaveLength(50)
    expect(error.problems.every((item) => item.length === 500)).toBe(true)
  })

  it('is empty without details, and absent from the public details', () => {
    const error = parseApiError(404, { error: { code: 'resource_not_found' } })
    expect(error.problems).toEqual([])
    expect(new ApiError({ status: 500 }).problems).toEqual([])
    expect('problems' in error.toPublicDetails()).toBe(false)
  })
})
