// Reading and writing nested values by path, e.g. ['services', 0, 'name'], for the profile
// editor. Writes return a copy; the draft passed in is never changed.

export type Path = (string | number)[]

export function getIn(value: unknown, path: Path): unknown {
  let current: unknown = value
  for (const key of path) {
    if (current === null || typeof current !== 'object') return undefined
    current = (current as Record<string | number, unknown>)[key]
  }
  return current
}

/** Sets `path` to `next`; `undefined` removes the key, so the API sees it as missing. */
export function setIn<T>(value: T, path: Path, next: unknown): T {
  if (path.length === 0) return next as T
  const [key, ...rest] = path
  const container: unknown = value ?? (typeof key === 'number' ? [] : {})
  const copy = (
    Array.isArray(container) ? [...container] : { ...(container as object) }
  ) as Record<string | number, unknown>
  const child = setIn(copy[key], rest, next)
  if (child === undefined && !Array.isArray(copy)) delete copy[key]
  else copy[key] = child
  return copy as T
}

export function pathKey(path: Path): string {
  return path.join('.')
}
