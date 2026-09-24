import type { Citations } from '../../api/types'
import { CITE_PROTOCOL } from '../Markdown'

// [[locator]] markers, with the spaces before them so dropped markers leave no gap.
const MARKER = /([ \t]*)\[\[([^[\]\n]+?)\]\]/g
// While streaming, a marker may be cut off mid-way ("…loss [[r42@41"); hide it until it completes.
const PARTIAL_MARKER = /[ \t]*\[\[?[^[\]\n]*\]?$/

function escapeLinkText(label: string): string {
  return label.replace(/[\\[\]*_`<>]/g, (ch) => `\\${ch}`)
}

/**
 * Rewrites [[locator]] markers into markdown links `[label](cite:LOCATOR)` for locators present in
 * `citations`, and drops the rest. Markdown renders those links as citation chips.
 */
export function linkCitations(text: string, citations: Citations, streaming: boolean): string {
  const linked = text.replace(MARKER, (_match, space: string, raw: string) => {
    const locator = raw.trim()
    const citation = citations[locator]
    if (!citation) return ''
    return `${space}[${escapeLinkText(citation.label)}](${CITE_PROTOCOL}${encodeURIComponent(locator)})`
  })
  return streaming ? linked.replace(PARTIAL_MARKER, '') : linked
}
