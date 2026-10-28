// Inputs bound to one value of the profile draft by its path. Each shows the API's
// validation messages for that path, so a missing or wrong field is highlighted where it
// is (FR-08-05, UC-08 exception).
import type { ReactNode } from 'react'

import { getIn, pathKey, type Path } from '../../lib/paths'
import { Button, Field } from '../ui'
import { useEditor } from './context'

function useBinding(path: Path) {
  const { draft, errors, setValue } = useEditor()
  return {
    value: getIn(draft, path),
    errors: errors.get(pathKey(path)) ?? [],
    set: (value: unknown) => setValue(path, value),
  }
}

type Common = { path: Path; label: string; hint?: string; className?: string }

export function TextField({
  path,
  label,
  hint,
  className,
  optional = false,
  multiline = false,
  placeholder,
  type = 'text',
}: Common & { optional?: boolean; multiline?: boolean; placeholder?: string; type?: 'text' | 'email' | 'tel' | 'url' }) {
  const { value, errors, set } = useBinding(path)
  const text = typeof value === 'string' ? value : ''
  // An empty optional field is left out, so the profile falls back to its default.
  const onChange = (next: string) => set(optional && next === '' ? undefined : next)
  return (
    <Field label={label} hint={hint} errors={errors} className={className}>
      {(props) =>
        multiline ? (
          <textarea {...props} rows={3} value={text} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        ) : (
          <input {...props} type={type} value={text} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        )
      }
    </Field>
  )
}

export function NumberField({ path, label, hint, className, step = 1 }: Common & { step?: number }) {
  const { value, errors, set } = useBinding(path)
  return (
    <Field label={label} hint={hint} errors={errors} className={className}>
      {(props) => (
        <input
          {...props}
          type="number"
          step={step}
          // Prices arrive as text ("1500.00"); show them like any other number.
          value={typeof value === 'number' || (typeof value === 'string' && value !== '') ? String(value) : ''}
          onChange={(e) => set(e.target.value === '' ? undefined : Number(e.target.value))}
        />
      )}
    </Field>
  )
}

export function TimeField({ path, label, hint, className, optional = false }: Common & { optional?: boolean }) {
  const { value, errors, set } = useBinding(path)
  return (
    <Field label={label} hint={hint} errors={errors} className={className}>
      {(props) => (
        <input
          {...props}
          type="time"
          value={typeof value === 'string' ? value.slice(0, 5) : ''}
          onChange={(e) => set(e.target.value === '' ? (optional ? undefined : '') : e.target.value)}
        />
      )}
    </Field>
  )
}

export type Option = { value: string; label: string }

export function SelectField({
  path,
  label,
  hint,
  className,
  options,
  placeholder,
}: Common & { options: Option[]; placeholder?: string }) {
  const { value, errors, set } = useBinding(path)
  return (
    <Field label={label} hint={hint} errors={errors} className={className}>
      {(props) => (
        <select
          {...props}
          value={typeof value === 'string' ? value : ''}
          // Choosing the placeholder leaves the field out, so a missing value is reported.
          onChange={(e) => set(e.target.value === '' ? undefined : e.target.value)}
        >
          {placeholder !== undefined && <option value="">{placeholder}</option>}
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  )
}

export function CheckboxField({ path, label, hint }: Common) {
  const { value, set } = useBinding(path)
  return (
    <label className="flex items-start gap-2 text-sm">
      <input
        type="checkbox"
        className="mt-0.5 size-4 rounded border-slate-300 accent-indigo-600"
        checked={value === true}
        onChange={(e) => set(e.target.checked)}
      />
      <span>
        <span className="font-medium text-slate-700 dark:text-slate-300">{label}</span>
        {hint && <span className="block text-xs text-slate-500 dark:text-slate-400">{hint}</span>}
      </span>
    </label>
  )
}

/** A list of short words or numbers typed as "a, b, c". */
export function ListField({
  path,
  label,
  hint,
  className,
  numbers = false,
}: Common & { numbers?: boolean }) {
  const { value, errors: own, set } = useBinding(path)
  const { errors } = useEditor()
  // Errors on single entries ("keywords.2") are shown on the whole list.
  const prefix = `${pathKey(path)}.`
  const itemErrors = [...errors].filter(([key]) => key.startsWith(prefix)).flatMap(([, messages]) => messages)
  const items = Array.isArray(value) ? value.map(String) : []
  return (
    <Field label={label} hint={hint ?? 'Separate entries with commas.'} errors={[...own, ...itemErrors]} className={className}>
      {(props) => (
        <input
          {...props}
          defaultValue={items.join(', ')}
          key={items.join('|')}
          onBlur={(e) => {
            const parts = e.target.value
              .split(',')
              .map((part) => part.trim())
              .filter(Boolean)
            set(numbers ? parts.map(Number) : parts)
          }}
        />
      )}
    </Field>
  )
}

/** Repeated groups (services, resources, policies...) with add and remove. */
export function ArrayEditor({
  path,
  addLabel,
  newItem,
  itemTitle,
  children,
}: {
  path: Path
  addLabel: string
  newItem: () => unknown
  itemTitle: (item: unknown, index: number) => string
  children: (itemPath: Path, index: number) => ReactNode
}) {
  const { value, errors, set } = useBinding(path)
  const items = Array.isArray(value) ? value : []
  return (
    <div className="space-y-3">
      {errors.length > 0 && <p className="text-sm font-medium text-rose-600 dark:text-rose-400">{errors.join(' ')}</p>}
      {items.map((item, index) => (
        <fieldset
          key={index}
          className="rounded-md border border-slate-200 p-3 dark:border-slate-800"
          aria-label={itemTitle(item, index)}
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <legend className="text-sm font-semibold text-slate-800 dark:text-slate-200">{itemTitle(item, index)}</legend>
            <Button variant="ghost" onClick={() => set(items.filter((_, i) => i !== index))}>
              Remove
            </Button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">{children([...path, index], index)}</div>
        </fieldset>
      ))}
      <Button variant="secondary" onClick={() => set([...items, newItem()])}>
        {addLabel}
      </Button>
    </div>
  )
}
