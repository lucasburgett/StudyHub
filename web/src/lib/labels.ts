import type { AssignmentStatus, Kind, LectureGap, ResourceSummary, Source } from '../api/types'

export const SOURCES: Source[] = ['canvas', 'gradescope', 'goodnotes', 'granola']

export const SOURCE_NAMES: Record<Source, string> = {
  canvas: 'Canvas',
  gradescope: 'Gradescope',
  goodnotes: 'GoodNotes',
  granola: 'Granola',
}

export const KIND_NAMES: Record<Kind, string> = {
  slides: 'Slides',
  file: 'File',
  page: 'Page',
  spec: 'Spec',
  notes: 'Notes',
  transcript: 'Recording',
  submission: 'Submission',
  announcement: 'Announcement',
}

/** "p.4" or "p.4–6" */
function pageRangeLabel([first, last]: [number, number]): string {
  return first === last ? `p.${first}` : `p.${first}–${last}`
}

/**
 * Short chip text for a resource inside a lecture card: "Slides · 48 pp", "Recording · 78 min",
 * or "My notes · p.4–6" when only a page range of a longer notebook belongs to the lecture.
 */
export function resourceChipLabel(r: ResourceSummary, range: [number, number] | null = null): string {
  const pages = range ? ` · ${pageRangeLabel(range)}` : r.page_count ? ` · ${r.page_count} pp` : ''
  switch (r.kind) {
    case 'slides':
      return `Slides${pages}`
    case 'notes':
      return `My notes${pages}`
    case 'transcript':
      return r.duration_min ? `Recording · ${r.duration_min} min` : 'Recording'
    case 'spec':
      return 'Spec'
    case 'submission':
      return 'Submission'
    default:
      return `${r.title}${range ? pages : ''}`
  }
}

/** Size or length of a resource: "48 pp", "78 min", or "". */
export function resourceSize(r: ResourceSummary): string {
  if (r.page_count) return `${r.page_count} pp`
  if (r.duration_min) return `${r.duration_min} min`
  return ''
}

export const GAP_INFO: Record<LectureGap, { label: string; source: Source }> = {
  recording: { label: 'Not recorded', source: 'granola' },
  notes: { label: 'No notes', source: 'goodnotes' },
  slides: { label: 'No slides', source: 'canvas' },
}

export const STATUS_INFO: Record<
  AssignmentStatus,
  { label: string; tone: 'accent' | 'ok' | 'warn' | 'bad' | 'muted' }
> = {
  upcoming: { label: 'Upcoming', tone: 'accent' },
  submitted: { label: 'Submitted', tone: 'warn' },
  graded: { label: 'Graded', tone: 'ok' },
  missing: { label: 'Missing', tone: 'bad' },
  unknown: { label: 'Unknown', tone: 'muted' },
}

export function lectureTitle(l: { number: number | null; title: string | null }): string {
  if (l.number !== null && l.title) return `Lecture ${l.number} · ${l.title}`
  if (l.number !== null) return `Lecture ${l.number}`
  return l.title ?? 'Lecture'
}
