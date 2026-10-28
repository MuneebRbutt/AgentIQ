import { countUnder, errorPath, friendlyMessage, toErrorMap } from './errors'

const error = (loc: (string | number)[], type: string, msg = '') => ({ loc, type, msg })

describe('validation messages (NFR02-02)', () => {
  it('drops the request wrapper from paths', () => {
    expect(errorPath(error(['body', 'config', 'services', 0, 'name'], 'missing'))).toEqual(['services', 0, 'name'])
    expect(errorPath(error(['domain'], 'missing'))).toEqual(['domain'])
  })

  it.each([
    [error(['domain'], 'missing', 'Field required'), 'This is required.'],
    [error(['name'], 'string_too_short'), 'This is required.'],
    [error(['domain'], 'enum'), 'Choose one of the options.'],
    [error(['booking_rules', 'slot_minutes'], 'int_parsing'), 'Enter a number.'],
    [error(['opening_hours', 'monday', 0, 'opens'], 'time_parsing'), 'Enter a time, for example 09:00.'],
    [error(['services', 0, 'id'], 'string_pattern_mismatch'), 'Use lowercase letters, numbers and dashes, like "deluxe-room".'],
    [error(['currency'], 'string_pattern_mismatch'), 'Use a three-letter code, like PKR.'],
    [error(['resources'], 'value_error', 'Value error, resource "x" names unknown service "y"'), 'Resource "x" names unknown service "y"'],
    [error(['booking_rules', 'max_party_size'], 'greater_than', 'Input should be greater than 0'), 'Should be greater than 0'],
  ])('%o reads "%s"', (input, expected) => {
    expect(friendlyMessage(input)).toBe(expected)
  })

  it('groups messages by field and counts them per section', () => {
    const map = toErrorMap([
      error(['services', 0, 'name'], 'missing'),
      error(['services', 1, 'id'], 'string_pattern_mismatch'),
      error([], 'value_error', 'Value error, add at least one service'),
    ])
    expect(map.get('services.0.name')).toEqual(['This is required.'])
    expect(map.get('')).toEqual(['Add at least one service'])
    expect(countUnder(map, 'services')).toBe(2)
    expect(countUnder(map, 'service')).toBe(0)
  })
})
