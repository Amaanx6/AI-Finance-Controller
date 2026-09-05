import { describe, expect, it } from 'vitest'
import { formatMetric, readableKey } from './view-models'

describe('view models', () => {
  it('formats backend keys for evidence labels', () => {
    expect(readableKey('fast_path_match')).toBe('Fast Path Match')
  })

  it('formats normalized and percentage metrics without inventing values', () => {
    expect(formatMetric(0.987)).toBe('98.70%')
    expect(formatMetric(undefined)).toBe('Not returned')
  })
})
