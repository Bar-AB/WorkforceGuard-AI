const DATE_FORMAT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

const DATE_TIME_OPTIONS: Intl.DateTimeFormatOptions = {
  timeZone: 'UTC',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZoneName: 'short',
}

const DATE_TIME_FORMAT = new Intl.DateTimeFormat('en-US', DATE_TIME_OPTIONS)

const DATE_TIME_SECONDS_FORMAT = new Intl.DateTimeFormat('en-US', {
  ...DATE_TIME_OPTIONS,
  second: '2-digit',
})

const DATE_TIME_MILLISECONDS_FORMAT = new Intl.DateTimeFormat('en-US', {
  ...DATE_TIME_OPTIONS,
  second: '2-digit',
  fractionalSecondDigits: 3,
})

const ID_HEAD = 8
const ID_TAIL = 5

function formatWith(format: Intl.DateTimeFormat, value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : format.format(date)
}

export function formatDate(value: string): string {
  return formatWith(DATE_FORMAT, value)
}

export function formatDateTime(value: string): string {
  return formatWith(DATE_TIME_FORMAT, value)
}

export function formatDateTimeSeconds(value: string): string {
  const offTheSecond = new Date(value).getUTCMilliseconds() > 0
  return formatWith(offTheSecond ? DATE_TIME_MILLISECONDS_FORMAT : DATE_TIME_SECONDS_FORMAT, value)
}

export function hasSeconds(value: string): boolean {
  const date = new Date(value)
  return (
    !Number.isNaN(date.getTime()) && (date.getUTCSeconds() !== 0 || date.getUTCMilliseconds() !== 0)
  )
}

export function shortId(value: string): string {
  return value.length <= ID_HEAD + ID_TAIL + 1
    ? value
    : `${value.slice(0, ID_HEAD)}…${value.slice(-ID_TAIL)}`
}

export function countOf(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}
