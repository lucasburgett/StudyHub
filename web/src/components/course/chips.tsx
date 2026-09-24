import type { AssignmentSummary, LectureGap, ResourceSummary } from '../../api/types'
import { formatScore } from '../../lib/format'
import { GAP_INFO, resourceChipLabel, STATUS_INFO } from '../../lib/labels'
import { href } from '../../lib/router'
import { Link, SourceDot } from '../ui'

interface ResourceChipProps {
  resource: ResourceSummary
  /** Page range of a longer notebook that belongs to this lecture; the chip opens at its first page. */
  range?: [number, number] | null
  /** Show the full title instead of the short kind label. */
  full?: boolean
}

export function ResourceChip({ resource, range = null, full = false }: ResourceChipProps) {
  return (
    <Link to={href.resource(resource.id, { page: range?.[0] })} className="chip" title={resource.title}>
      <SourceDot source={resource.source} />
      {full ? resource.title : resourceChipLabel(resource, range)}
    </Link>
  )
}

export function GapChip({ gap }: { gap: LectureGap }) {
  const info = GAP_INFO[gap]
  return (
    <span className="chip gap">
      <SourceDot source={info.source} />
      {info.label}
    </span>
  )
}

function statusText(a: AssignmentSummary): string {
  switch (a.status) {
    case 'submitted':
      return 'Submitted · not graded'
    case 'graded': {
      const score = formatScore(a.score, a.points)
      return score ? `Graded · ${score}` : 'Graded'
    }
    default:
      return STATUS_INFO[a.status].label
  }
}

export function AssignmentStatusChip({ assignment }: { assignment: AssignmentSummary }) {
  return (
    <Link to={href.assignment(assignment.id)} className={`chip status ${STATUS_INFO[assignment.status].tone}`}>
      <SourceDot source={assignment.source} />
      {statusText(assignment)}
    </Link>
  )
}
