// Turns the API's validation errors into messages an operator can act on (NFR02-02).
import type { FieldError } from '../api/client'
import { pathKey, type Path } from './paths'

// FastAPI prefixes request-body errors with where the value came from.
const WRAPPERS = new Set(['body', 'config'])

export function errorPath(error: FieldError): Path {
  const loc = [...error.loc]
  while (loc.length > 0 && WRAPPERS.has(String(loc[0]))) loc.shift()
  return loc
}

export function friendlyMessage(error: FieldError): string {
  switch (error.type) {
    case 'missing':
      return 'This is required.'
    case 'string_too_short':
      return 'This is required.'
    case 'enum':
    case 'literal_error':
      return 'Choose one of the options.'
    case 'int_parsing':
    case 'int_type':
    case 'float_parsing':
      return 'Enter a number.'
    case 'time_parsing':
    case 'time_type':
      return 'Enter a time, for example 09:00.'
    case 'string_pattern_mismatch':
      return patternMessage(errorPath(error))
    default:
      return error.msg
        .replace(/^Value error, /, '')
        .replace(/^Input should be/, 'Should be')
        .replace(/^String should have/, 'Should have')
        .replace(/^./, (first) => first.toUpperCase())
  }
}

function patternMessage(path: Path): string {
  const field = String(path[path.length - 1] ?? '')
  if (field === 'id' || field === 'key' || field.endsWith('_ids')) {
    return 'Use lowercase letters, numbers and dashes, like "deluxe-room".'
  }
  if (field.includes('phone')) return 'Enter a phone number, like +92 300 1234567.'
  if (field.includes('email')) return 'Enter an email address, like name@example.com.'
  if (field === 'currency') return 'Use a three-letter code, like PKR.'
  return 'This is not in the expected format.'
}

/** Field errors keyed by path ("services.0.name"); errors about the whole profile use "". */
export type ErrorMap = Map<string, string[]>

export function toErrorMap(errors: FieldError[]): ErrorMap {
  const map: ErrorMap = new Map()
  for (const error of errors) {
    const key = pathKey(errorPath(error))
    map.set(key, [...(map.get(key) ?? []), friendlyMessage(error)])
  }
  return map
}

/** How many errors sit at or under a path, for badges on editor sections. */
export function countUnder(map: ErrorMap, prefix: string): number {
  let count = 0
  for (const [key, messages] of map) {
    if (key === prefix || key.startsWith(`${prefix}.`)) count += messages.length
  }
  return count
}
