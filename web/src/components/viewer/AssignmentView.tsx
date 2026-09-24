import { useEffect, useRef } from 'react'
import type { AssignmentDetail, Feedback } from '../../api/types'
import { formatDateTime, formatNumber, formatScore } from '../../lib/format'
import { SOURCE_NAMES } from '../../lib/labels'
import { href } from '../../lib/router'
import { Markdown } from '../Markdown'
import { ExternalLink, Link, StatusPill } from '../ui'
import { ViewerHeader } from './ViewerHeader'

/**
 * Which feedback entry a locator like `a7/q2` points at: the entry whose question label carries
 * that number ("Q2", "2: Softmax"), else the 2nd entry.
 */
function feedbackIndex(feedback: Feedback[], question: number): number {
  const byLabel = feedback.findIndex((f) => {
    const m = /\d+/.exec(f.question)
    return m !== null && Number(m[0]) === question
  })
  if (byLabel !== -1) return byLabel
  return question >= 1 && question <= feedback.length ? question - 1 : -1
}

function lostPoints(f: Feedback): number | null {
  return f.score !== null && f.max_score !== null && f.max_score > f.score ? f.max_score - f.score : null
}

interface AssignmentViewProps {
  assignment: AssignmentDetail
  question: number | null
  backTo: string
}

export function AssignmentView({ assignment: a, question, backTo }: AssignmentViewProps) {
  const highlight = question === null ? -1 : feedbackIndex(a.feedback, question)
  const listRef = useRef<HTMLOListElement>(null)

  useEffect(() => {
    if (highlight < 0) return
    listRef.current?.querySelector<HTMLElement>('[aria-current="true"]')?.scrollIntoView({ block: 'center' })
  }, [highlight, a.id])

  const score = formatScore(a.score, a.points)

  return (
    <article className="viewer">
      <ViewerHeader
        source={a.source}
        meta={['Assignment']}
        title={a.title}
        backTo={backTo}
        links={
          a.url || a.spec_resource_id !== null ? (
            <>
              {a.spec_resource_id !== null && <Link to={href.resource(a.spec_resource_id)}>Open spec</Link>}
              {a.url && <ExternalLink href={a.url}>Open in {SOURCE_NAMES[a.source]}</ExternalLink>}
            </>
          ) : null
        }
      />

      <dl className="facts">
        <div>
          <dt>Due</dt>
          <dd className="date">{a.due_at ? formatDateTime(a.due_at) : 'No due date'}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>
            <StatusPill status={a.status} />
          </dd>
        </div>
        <div>
          <dt>Score</dt>
          <dd className="num">{score || '—'}</dd>
        </div>
      </dl>

      {a.description && (
        <section className="section" aria-labelledby="desc-h">
          <h3 id="desc-h" className="section-h">
            Description
          </h3>
          <Markdown text={a.description} className="doc" />
        </section>
      )}

      <section className="section" aria-labelledby="fb-h">
        <h3 id="fb-h" className="section-h">
          Feedback
        </h3>
        {a.feedback.length === 0 ? (
          <p className="muted">No feedback yet.</p>
        ) : (
          <ol className="feedback" ref={listRef}>
            {a.feedback.map((f, i) => {
              const lost = lostPoints(f)
              return (
                <li
                  key={`${f.question}-${i}`}
                  className={i === highlight ? 'fb on' : 'fb'}
                  aria-current={i === highlight || undefined}
                >
                  <div className="fb-h">
                    <b>{f.question}</b>
                    <span className="num">
                      {formatScore(f.score, f.max_score) || '—'}
                      {lost !== null && <span className="lost"> −{formatNumber(lost)}</span>}
                    </span>
                  </div>
                  {f.rubric_items.length > 0 && (
                    <ul className="rubric">
                      {f.rubric_items.map((item, j) => (
                        <li key={j}>{item}</li>
                      ))}
                    </ul>
                  )}
                  {f.comment && <Markdown text={f.comment} className="fb-comment" />}
                </li>
              )
            })}
          </ol>
        )}
      </section>
    </article>
  )
}
