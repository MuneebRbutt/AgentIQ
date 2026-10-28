// The business-profile form (FR-08-01/04/06). Every setting of the profile schema has a
// place here, grouped as the operator thinks about them; nothing is domain-specific
// (FR-08-07): a hotel, restaurant or clinic is just different values.
import type { ReactNode } from 'react'

import { countUnder, type ErrorMap } from '../../lib/errors'
import { getIn, type Path } from '../../lib/paths'
import { Badge, Button, Card } from '../ui'
import {
  ArrayEditor,
  CheckboxField,
  ListField,
  NumberField,
  SelectField,
  TextField,
  TimeField,
  type Option,
} from './fields'
import { EditorContext, useEditor } from './context'

const DOMAINS: Option[] = [
  { value: 'hotel', label: 'Hotel' },
  { value: 'restaurant', label: 'Restaurant' },
  { value: 'clinic', label: 'Clinic' },
]

const WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const

const SECTIONS = [
  { id: 'basics', title: 'Business', paths: ['name', 'domain', 'description', 'timezone', 'currency', 'contact'] },
  { id: 'hours', title: 'Opening hours', paths: ['opening_hours'] },
  { id: 'services', title: 'Services', paths: ['services'] },
  { id: 'resources', title: 'Bookable resources', paths: ['resources'] },
  { id: 'booking', title: 'Booking rules', paths: ['booking_rules'] },
  { id: 'policies', title: 'Policies and FAQs', paths: ['policies', 'faqs'] },
  { id: 'escalation', title: 'Escalation', paths: ['escalation'] },
  { id: 'follow-up', title: 'Follow-ups', paths: ['follow_up'] },
  { id: 'assistant', title: 'Assistant', paths: ['assistant'] },
] as const

type Props = {
  draft: unknown
  errors: ErrorMap
  onChange: (path: Path, value: unknown) => void
}

export function ProfileEditor({ draft, errors, onChange }: Props) {
  const whole = errors.get('') ?? []
  return (
    <EditorContext.Provider value={{ draft, errors, setValue: onChange }}>
      <nav aria-label="Profile sections" className="mb-4 flex flex-wrap gap-2">
        {SECTIONS.map((section) => {
          const count = section.paths.reduce((sum, path) => sum + countUnder(errors, path), 0)
          return (
            <a
              key={section.id}
              href={`#${section.id}`}
              className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 px-3 py-1 text-sm hover:bg-white dark:border-slate-700 dark:hover:bg-slate-900"
            >
              {section.title}
              {count > 0 && <Badge tone="bad">{count}</Badge>}
            </a>
          )
        })}
      </nav>
      {whole.length > 0 && (
        <div role="alert" className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-200">
          {whole.join(' ')}
        </div>
      )}
      <div className="space-y-4">
        <Section id="basics" title="Business">
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField path={['name']} label="Business name" />
            <SelectField
              path={['domain']}
              label="Type of business"
              options={DOMAINS}
              placeholder="Choose…"
              hint="Only labels the profile; switching it changes no behaviour."
            />
            <TextField path={['description']} label="Description" optional multiline className="sm:col-span-2" />
            <TextField path={['timezone']} label="Time zone" hint="For example Asia/Karachi." />
            <TextField path={['currency']} label="Currency" hint="Three letters, for example PKR." />
            <TextField path={['contact', 'phone']} label="Phone" type="tel" optional />
            <TextField path={['contact', 'email']} label="Email" type="email" optional />
            <TextField path={['contact', 'address']} label="Address" optional />
            <TextField path={['contact', 'website']} label="Website" type="url" optional />
          </div>
        </Section>

        <Section id="hours" title="Opening hours" hint="A day with no hours is closed. Closing earlier than opening runs past midnight.">
          <OpeningHours />
        </Section>

        <Section id="services" title="Services" hint="What customers can book or ask about.">
          <ArrayEditor
            path={['services']}
            addLabel="Add a service"
            newItem={() => ({ id: '', name: '' })}
            itemTitle={(item, index) => nameOf(item) ?? `Service ${index + 1}`}
          >
            {(path) => (
              <>
                <TextField path={[...path, 'name']} label="Name" />
                <TextField path={[...path, 'id']} label="Short id" hint="Lowercase, e.g. dine-in." />
                <NumberField path={[...path, 'duration_minutes']} label="Duration (minutes)" hint="Leave empty to use the default." />
                <NumberField path={[...path, 'price']} label="Price" step={0.01} />
                <TextField path={[...path, 'description']} label="Description" optional multiline className="sm:col-span-2" />
              </>
            )}
          </ArrayEditor>
        </Section>

        <Section id="resources" title="Bookable resources" hint="Rooms, tables, doctors: how many there are and how many people each takes.">
          <ArrayEditor
            path={['resources']}
            addLabel="Add a resource"
            newItem={() => ({ id: '', name: '', kind: '', quantity: 1, capacity: 1, service_ids: [] })}
            itemTitle={(item, index) => nameOf(item) ?? `Resource ${index + 1}`}
          >
            {(path) => (
              <>
                <TextField path={[...path, 'name']} label="Name" />
                <TextField path={[...path, 'id']} label="Short id" hint="Lowercase, e.g. table-4." />
                <TextField path={[...path, 'kind']} label="Kind" hint='What you call it: "table", "room type", "doctor".' />
                <NumberField path={[...path, 'quantity']} label="How many" />
                <NumberField path={[...path, 'capacity']} label="People each" />
                <ListField path={[...path, 'service_ids']} label="For services" hint="Service ids, separated by commas. Empty means all." />
              </>
            )}
          </ArrayEditor>
        </Section>

        <Section id="booking" title="Booking rules">
          <BookingRules />
        </Section>

        <Section id="policies" title="Policies and FAQs" hint="The assistant answers from these and from uploaded documents.">
          <h3 className="mb-2 text-sm font-semibold">Policies</h3>
          <ArrayEditor
            path={['policies']}
            addLabel="Add a policy"
            newItem={() => ({ title: '', text: '' })}
            itemTitle={(item, index) => textOf(item, 'title') ?? `Policy ${index + 1}`}
          >
            {(path) => (
              <>
                <TextField path={[...path, 'title']} label="Title" className="sm:col-span-2" />
                <TextField path={[...path, 'text']} label="Text" multiline className="sm:col-span-2" />
              </>
            )}
          </ArrayEditor>
          <h3 className="mt-5 mb-2 text-sm font-semibold">Frequently asked questions</h3>
          <ArrayEditor
            path={['faqs']}
            addLabel="Add a question"
            newItem={() => ({ question: '', answer: '' })}
            itemTitle={(item, index) => textOf(item, 'question') ?? `Question ${index + 1}`}
          >
            {(path) => (
              <>
                <TextField path={[...path, 'question']} label="Question" className="sm:col-span-2" />
                <TextField path={[...path, 'answer']} label="Answer" multiline className="sm:col-span-2" />
              </>
            )}
          </ArrayEditor>
        </Section>

        <Section id="escalation" title="Escalation" hint="When to hand a conversation to your team, and who to tell.">
          <div className="grid gap-3 sm:grid-cols-2">
            <NumberField path={['escalation', 'negative_sentiment_threshold']} label="Upset threshold (0–1)" step={0.05} hint="Hand over when the customer sounds this upset." />
            <NumberField path={['escalation', 'negative_turns_in_a_row']} label="Unhappy messages in a row" />
            <NumberField path={['escalation', 'max_clarification_attempts']} label="Clarifying questions before handing over" />
            <ListField path={['escalation', 'keywords']} label="Keywords" hint='Words that always hand over, e.g. "manager, refund".' />
            <TextField path={['escalation', 'notify_phone']} label="Notify phone" type="tel" optional />
            <TextField path={['escalation', 'notify_email']} label="Notify email" type="email" optional />
            <TextField path={['escalation', 'handoff_message']} label="What the customer hears" multiline className="sm:col-span-2" />
          </div>
        </Section>

        <Section id="follow-up" title="Follow-ups" hint="Messages sent automatically after bookings and conversations.">
          <div className="grid gap-3 sm:grid-cols-2">
            <CheckboxField path={['follow_up', 'confirmation']} label="Send booking confirmations" />
            <CheckboxField path={['follow_up', 'feedback']} label="Ask for feedback after conversations" />
            <ListField path={['follow_up', 'reminder_hours_before']} label="Reminders (hours before)" numbers hint="Up to three, e.g. 24, 2." />
            <NumberField path={['follow_up', 'feedback_delay_minutes']} label="Ask for feedback after (minutes)" />
            <SelectField
              path={['follow_up', 'channel']}
              label="Send by"
              options={[
                { value: 'sms', label: 'SMS' },
                { value: 'email', label: 'Email' },
              ]}
            />
            <p className="text-xs text-slate-500 sm:col-span-2 dark:text-slate-400">
              Messages may use {'{business}'}, {'{name}'}, {'{reference}'}, {'{service}'}, {'{when}'}, {'{party_size}'} and{' '}
              {'{business_phone}'}. The feedback message may use only {'{business}'}, {'{name}'} and {'{business_phone}'}.
            </p>
            <TextField path={['follow_up', 'confirmation_message']} label="Confirmation" multiline className="sm:col-span-2" />
            <TextField path={['follow_up', 'update_message']} label="Booking changed" multiline className="sm:col-span-2" />
            <TextField path={['follow_up', 'reminder_message']} label="Reminder" multiline className="sm:col-span-2" />
            <TextField path={['follow_up', 'feedback_message']} label="Feedback request" multiline className="sm:col-span-2" />
          </div>
        </Section>

        <Section id="assistant" title="Assistant">
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField path={['assistant', 'greeting']} label="Greeting" multiline className="sm:col-span-2" />
            <TextField path={['assistant', 'tone']} label="Tone" />
            <TextField path={['assistant', 'extra_instructions']} label="Extra instructions" optional multiline className="sm:col-span-2" />
          </div>
        </Section>
      </div>
    </EditorContext.Provider>
  )
}

function Section({ id, title, hint, children }: { id: string; title: string; hint?: string; children: ReactNode }) {
  return (
    <div id={id} className="scroll-mt-4">
      <Card title={title}>
        {hint && <p className="-mt-2 mb-3 text-sm text-slate-500 dark:text-slate-400">{hint}</p>}
        {children}
      </Card>
    </div>
  )
}

function OpeningHours() {
  const { draft, setValue, errors } = useEditor()
  const message = errors.get('opening_hours')
  return (
    <div className="space-y-2">
      {message && <p className="text-sm font-medium text-rose-600 dark:text-rose-400">{message.join(' ')}</p>}
      {WEEKDAYS.map((day) => {
        const path: Path = ['opening_hours', day]
        const intervals = getIn(draft, path)
        const list = Array.isArray(intervals) ? intervals : []
        const dayErrors = errors.get(`opening_hours.${day}`)
        return (
          <div key={day} className="flex flex-wrap items-end gap-3 border-b border-slate-100 pb-2 dark:border-slate-800">
            <span className="w-24 pb-1.5 text-sm font-medium capitalize">{day}</span>
            {list.length === 0 && <span className="pb-1.5 text-sm text-slate-500">Closed</span>}
            {list.map((_, index) => (
              <div key={index} className="flex items-end gap-1">
                <TimeField path={[...path, index, 'opens']} label="Opens" className="w-28" />
                <TimeField path={[...path, index, 'closes']} label="Closes" className="w-28" />
                <Button variant="ghost" aria-label={`Remove ${day} hours ${index + 1}`} onClick={() => setValue(path, list.filter((__, i) => i !== index))}>
                  ✕
                </Button>
              </div>
            ))}
            <Button variant="ghost" onClick={() => setValue(path, [...list, { opens: '09:00', closes: '17:00' }])}>
              Add hours
            </Button>
            {dayErrors && <p className="w-full text-xs font-medium text-rose-600 dark:text-rose-400">{dayErrors.join(' ')}</p>}
          </div>
        )
      })}
    </div>
  )
}

function BookingRules() {
  const { draft } = useEditor()
  const overnight = getIn(draft, ['booking_rules', 'mode']) === 'overnight'
  const rules: Path = ['booking_rules']
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField
          path={[...rules, 'mode']}
          label="Bookings are"
          placeholder="Choose…"
          options={[
            { value: 'time_slot', label: 'Time slots (tables, appointments)' },
            { value: 'overnight', label: 'Overnight stays (rooms)' },
          ]}
        />
        {overnight ? (
          <>
            <TimeField path={[...rules, 'check_in_time']} label="Check-in time" optional />
            <TimeField path={[...rules, 'check_out_time']} label="Check-out time" optional />
            <NumberField path={[...rules, 'max_nights']} label="Longest stay (nights)" />
          </>
        ) : (
          <>
            <NumberField path={[...rules, 'slot_minutes']} label="Slot length (minutes)" />
            <NumberField path={[...rules, 'default_duration_minutes']} label="Default booking length (minutes)" />
          </>
        )}
        <NumberField path={[...rules, 'min_party_size']} label="Smallest party" />
        <NumberField path={[...rules, 'max_party_size']} label="Largest party" />
        <NumberField path={[...rules, 'min_notice_minutes']} label="Book at least (minutes ahead)" />
        <NumberField path={[...rules, 'max_advance_days']} label="Book at most (days ahead)" />
        <NumberField path={[...rules, 'cancellation_notice_hours']} label="Changes allowed until (hours before)" />
        <ListField path={[...rules, 'required_details']} label="Details to collect" hint="Any of: name, phone, email." />
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Extra questions</h3>
        <ArrayEditor
          path={[...rules, 'custom_fields']}
          addLabel="Add a question"
          newItem={() => ({ key: '', label: '', required: true })}
          itemTitle={(item, index) => textOf(item, 'label') ?? `Question ${index + 1}`}
        >
          {(path) => (
            <>
              <TextField path={[...path, 'label']} label="Question" />
              <TextField path={[...path, 'key']} label="Short id" hint="Lowercase, e.g. reason." />
              <CheckboxField path={[...path, 'required']} label="Required" />
            </>
          )}
        </ArrayEditor>
      </div>
    </div>
  )
}

function textOf(item: unknown, key: string): string | undefined {
  const value = getIn(item, [key])
  return typeof value === 'string' && value.trim() ? value : undefined
}

function nameOf(item: unknown): string | undefined {
  return textOf(item, 'name')
}
