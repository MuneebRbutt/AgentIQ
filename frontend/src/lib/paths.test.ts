import { getIn, pathKey, setIn } from './paths'

describe('paths', () => {
  const draft = { name: 'Spice Garden', services: [{ id: 'dine-in', name: 'Table' }] }

  it('reads nested values and misses quietly', () => {
    expect(getIn(draft, ['services', 0, 'name'])).toBe('Table')
    expect(getIn(draft, ['services', 3, 'name'])).toBeUndefined()
    expect(getIn(draft, ['name', 'first'])).toBeUndefined()
  })

  it('writes a copy and leaves the original alone', () => {
    const next = setIn(draft, ['services', 0, 'name'], 'Dine in')
    expect(getIn(next, ['services', 0, 'name'])).toBe('Dine in')
    expect(draft.services[0].name).toBe('Table')
    expect(next.services).not.toBe(draft.services)
  })

  it('creates missing containers and removes a key set to undefined', () => {
    expect(setIn({}, ['contact', 'phone'], '+92 42')).toEqual({ contact: { phone: '+92 42' } })
    expect(setIn({}, ['services', 0], 'x')).toEqual({ services: ['x'] })
    expect(setIn(draft, ['name'], undefined)).not.toHaveProperty('name')
  })

  it('keys paths with dots', () => {
    expect(pathKey(['services', 0, 'name'])).toBe('services.0.name')
    expect(pathKey([])).toBe('')
  })
})
