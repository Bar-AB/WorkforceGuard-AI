import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Correction } from '../../api/corrections.ts'
import { correction } from '../../test/correctionFixtures.ts'
import { ProposalCard } from './ProposalCard.tsx'

function renderCard(proposal: Correction) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ProposalCard correction={proposal} />
    </QueryClientProvider>,
  )
}

function judgeVerdict(): HTMLElement {
  return screen.getByRole('group', { name: 'Judge verdict' })
}

const DRIFT_WARNING =
  'This row changed after the fix was suggested. Approving will be refused. Reject it and ask for a new suggestion.'

describe('ProposalCard', () => {
  it('says the row no longer exists when current is null', () => {
    renderCard(correction({ current: null }))

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('row no longer exists')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 17:00 UTC')).toBeInTheDocument()
  })

  it('shows plain values and empty values as they are', () => {
    renderCard(
      correction({
        target_table: 'attendance_events',
        patch: { kind: 'clock_out', minutes: 30 },
        current: { kind: 'clock_in', minutes: null },
      }),
    )

    expect(screen.getByRole('article', { name: /Attendance event/ })).toBeInTheDocument()
    const [kind, minutes] = screen.getAllByRole('listitem')
    expect(kind).toHaveTextContent('kind')
    expect(within(kind).getByText('clock_in')).toBeInTheDocument()
    expect(within(kind).getByText('clock_out')).toBeInTheDocument()
    expect(within(minutes).getByText('empty')).toBeInTheDocument()
    expect(within(minutes).getByText('30')).toBeInTheDocument()
  })

  it('shows the live value while the proposal is pending', () => {
    renderCard(
      correction({
        before: { ends_at: '2026-03-02T22:00:00Z' },
        current: { ends_at: '2026-03-02T23:00:00Z' },
      }),
    )

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('Mar 2, 2026, 23:00 UTC')).toBeInTheDocument()
    expect(within(change).queryByText('Mar 2, 2026, 22:00 UTC')).toBeNull()
  })

  it.each(['approved', 'rejected'])('shows the stored before value once %s', (status) => {
    renderCard(
      correction({
        status,
        before: { ends_at: '2026-03-02T23:00:00Z' },
        current: { ends_at: '2026-03-02T19:00:00Z' },
      }),
    )

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('Mar 2, 2026, 23:00 UTC')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 17:00 UTC')).toBeInTheDocument()
    expect(within(change).queryByText('Mar 2, 2026, 19:00 UTC')).toBeNull()
  })

  it.each([null, undefined])(
    'says the old value was not recorded when a decided before is %s',
    (before) => {
      renderCard(correction({ status: 'approved', before }))

      const change = screen.getByRole('listitem')
      expect(within(change).getByText('not recorded')).toBeInTheDocument()
      expect(within(change).getByText('Mar 2, 2026, 17:00 UTC')).toBeInTheDocument()
      expect(within(change).queryByText('Mar 2, 2026, 23:00 UTC')).toBeNull()
    },
  )

  it('shows seconds on both values when either has seconds', () => {
    renderCard(
      correction({
        patch: { occurred_at: '2026-03-02T07:00:00Z' },
        current: { occurred_at: '2026-03-02T07:00:20Z' },
      }),
    )

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('Mar 2, 2026, 07:00:20 UTC')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 07:00:00 UTC')).toBeInTheDocument()
  })

  it('shows seconds on a decided card when the stored before has seconds', () => {
    renderCard(
      correction({
        status: 'approved',
        patch: { occurred_at: '2026-03-02T07:00:00Z' },
        before: { occurred_at: '2026-03-02T07:00:20Z' },
        current: { occurred_at: '2026-03-02T07:00:00Z' },
      }),
    )

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('Mar 2, 2026, 07:00:20 UTC')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 07:00:00 UTC')).toBeInTheDocument()
  })

  it('leaves seconds out for a whole-minute pair', () => {
    renderCard(correction())

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('Mar 2, 2026, 23:00 UTC')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 17:00 UTC')).toBeInTheDocument()
  })

  it('shows a sub-second change as two different values', () => {
    renderCard(
      correction({
        patch: { occurred_at: '2026-03-02T07:00:00Z' },
        before: { occurred_at: '2026-03-02T07:00:00.500Z' },
        current: { occurred_at: '2026-03-02T07:00:00.500Z' },
      }),
    )

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('Mar 2, 2026, 07:00:00.500 UTC')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 07:00:00 UTC')).toBeInTheDocument()
  })

  it('shows seconds on both values when only the new value has seconds', () => {
    renderCard(
      correction({
        patch: { occurred_at: '2026-03-02T07:00:20Z' },
        before: { occurred_at: '2026-03-02T07:00:00Z' },
        current: { occurred_at: '2026-03-02T07:00:00Z' },
      }),
    )

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('Mar 2, 2026, 07:00:00 UTC')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 07:00:20 UTC')).toBeInTheDocument()
  })

  it('shows seconds on the new value when the row no longer exists', () => {
    renderCard(correction({ patch: { occurred_at: '2026-03-02T07:00:20Z' }, current: null }))

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('row no longer exists')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 07:00:20 UTC')).toBeInTheDocument()
  })

  it('decides seconds for each column on its own', () => {
    renderCard(
      correction({
        patch: { starts_at: '2026-03-02T07:00:00Z', ends_at: '2026-03-02T17:00:00Z' },
        before: { starts_at: '2026-03-02T07:00:20Z', ends_at: '2026-03-02T23:00:00Z' },
        current: { starts_at: '2026-03-02T07:00:20Z', ends_at: '2026-03-02T23:00:00Z' },
      }),
    )

    const [startsAt, endsAt] = screen.getAllByRole('listitem')
    expect(within(startsAt).getByText('Mar 2, 2026, 07:00:20 UTC')).toBeInTheDocument()
    expect(within(startsAt).getByText('Mar 2, 2026, 07:00:00 UTC')).toBeInTheDocument()
    expect(within(endsAt).getByText('Mar 2, 2026, 23:00 UTC')).toBeInTheDocument()
    expect(within(endsAt).getByText('Mar 2, 2026, 17:00 UTC')).toBeInTheDocument()
  })

  it('says the old value was not recorded when a decided before lacks the column', () => {
    renderCard(correction({ status: 'approved', before: {} }))

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('not recorded')).toBeInTheDocument()
    expect(within(change).queryByText('empty')).toBeNull()
  })

  it('shows a decided plain value as it is', () => {
    renderCard(
      correction({
        status: 'approved',
        target_table: 'attendance_events',
        patch: { kind: 'clock_out' },
        before: { kind: 'clock_in' },
        current: { kind: 'clock_out' },
      }),
    )

    const change = screen.getByRole('listitem')
    expect(within(change).getByText('clock_in')).toBeInTheDocument()
    expect(within(change).getByText('clock_out')).toBeInTheDocument()
  })

  it('shows the stored before and no decision buttons for an unknown status', () => {
    renderCard(
      correction({
        status: 'superseded',
        before: { ends_at: '2026-03-02T23:00:00Z' },
        current: { ends_at: '2026-03-02T19:00:00Z' },
      }),
    )

    expect(screen.getByText('superseded')).toBeInTheDocument()
    expect(screen.getByText('Mar 2, 2026, 23:00 UTC')).toBeInTheDocument()
    expect(screen.queryByText('Mar 2, 2026, 19:00 UTC')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('strikes through a real old value', () => {
    renderCard(correction())

    expect(screen.getByText('Mar 2, 2026, 23:00 UTC')).toHaveClass('line-through')
  })

  it.each([
    ['not recorded', correction({ status: 'approved', before: null })],
    ['row no longer exists', correction({ current: null })],
    ['empty', correction({ before: { ends_at: null }, current: { ends_at: null } })],
  ])('shows the placeholder "%s" in italics without a strike-through', (placeholder, proposal) => {
    renderCard(proposal)

    const text = screen.getByText(placeholder)
    expect(text).toHaveClass('italic')
    expect(text).not.toHaveClass('line-through')
  })

  it.each([
    ['pending', 'changes to'],
    ['approved', 'changed to'],
    ['rejected', 'would have changed to'],
  ])('reads a %s change as "%s"', (status, phrase) => {
    renderCard(correction({ status }))

    expect(within(screen.getByRole('listitem')).getByText(phrase)).toBeInTheDocument()
  })

  it('warns on a pending card when the row changed after the suggestion', () => {
    renderCard(
      correction({
        before: { ends_at: '2026-03-02T23:00:00Z' },
        current: { ends_at: '2026-03-02T22:00:00Z' },
      }),
    )

    expect(screen.getByText(DRIFT_WARNING)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('does not warn when the row still holds the recorded instant', () => {
    renderCard(
      correction({
        before: { ends_at: '2026-03-02T23:00:00Z' },
        current: { ends_at: '2026-03-03T01:00:00+02:00' },
      }),
    )

    expect(screen.queryByText(DRIFT_WARNING)).toBeNull()
  })

  it.each([null, undefined])('does not warn when a pending before is %s', (before) => {
    renderCard(correction({ before, current: { ends_at: '2026-03-02T22:00:00Z' } }))

    expect(screen.queryByText(DRIFT_WARNING)).toBeNull()
  })

  it('does not warn when the row no longer exists', () => {
    renderCard(correction({ current: null }))

    expect(screen.queryByText(DRIFT_WARNING)).toBeNull()
  })

  it.each(['approved', 'rejected'])('does not warn on a %s card', (status) => {
    renderCard(
      correction({
        status,
        before: { ends_at: '2026-03-02T23:00:00Z' },
        current: { ends_at: '2026-03-02T17:00:00Z' },
      }),
    )

    expect(screen.queryByText(DRIFT_WARNING)).toBeNull()
  })

  it('renders the reason markup as plain text', () => {
    const markup = '<img src=x onerror=alert(1)>'

    const { container } = renderCard(correction({ reason: markup }))

    expect(screen.getByText(markup)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
  })
  it.each([
    [true, 'The new end time fits the shift.', 'Judge: looks right', 'accent'],
    [false, 'The patch keeps the 14h shift.', 'Judge: looks wrong', 'high'],
  ])('shows a judge verdict of %s with its reason', (supported, reason, label, tone) => {
    renderCard(
      correction({
        judge_supported: supported,
        judge_reason: reason,
        judged_by: 'judge_proposal.v1',
      }),
    )

    expect(screen.getByText(label)).toHaveAttribute('data-tone', tone)
    expect(screen.getByText(reason)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it.each([
    ['judge_proposal.v1', 'Judge unavailable'],
    [null, 'Not judged'],
  ])(
    'shows a neutral badge with no reason when judged_by is %s and no verdict',
    (judgedBy, label) => {
      const { container } = renderCard(
        correction({ judge_supported: null, judge_reason: null, judged_by: judgedBy }),
      )

      expect(screen.getByText(label)).toHaveAttribute('data-tone', 'neutral')
      expect(container.querySelectorAll('[data-tone]')).toHaveLength(2)
      expect(screen.queryByText(/^Judge:/)).toBeNull()
      expect(judgeVerdict().querySelector('p')).toBeNull()
      expect(screen.getByRole('listitem')).toBeInTheDocument()
    },
  )

  it('says not judged and shows no reason when the payload has no judge keys', () => {
    const { judge_supported, judge_reason, judged_by, ...legacy } = correction()
    expect([judge_supported, judge_reason, judged_by]).toEqual([null, null, null])

    renderCard(legacy as Correction)

    expect(within(judgeVerdict()).getByText('Not judged')).toHaveAttribute('data-tone', 'neutral')
    expect(judgeVerdict().querySelector('p')).toBeNull()
  })

  it('shows no reason paragraph for an empty judge reason', () => {
    renderCard(
      correction({ judge_supported: true, judge_reason: '', judged_by: 'judge_proposal.v1' }),
    )

    expect(within(judgeVerdict()).getByText('Judge: looks right')).toBeInTheDocument()
    expect(judgeVerdict().querySelector('p')).toBeNull()
  })

  it('renders the judge reason markup as plain text', () => {
    const markup = '<b onclick=alert(1)>looks fine</b>'

    const { container } = renderCard(
      correction({ judge_supported: true, judge_reason: markup, judged_by: 'judge_proposal.v1' }),
    )

    expect(screen.getByText(markup)).toBeInTheDocument()
    expect(container.querySelector('b')).toBeNull()
  })
})
