import { useMemo } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components, type Options } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import type { Citations } from '../api/types'
import { citationHref } from '../lib/router'
import { Link } from './ui'

/** Links with this pseudo-protocol are citation chips (see chat/citations.ts). */
export const CITE_PROTOCOL = 'cite:'

const remarkPlugins: Options['remarkPlugins'] = [remarkGfm, remarkMath]
const rehypePlugins: Options['rehypePlugins'] = [[rehypeKatex, { strict: 'ignore', throwOnError: false }]]

// react-markdown drops unknown protocols by default, which would strip cite: links.
function urlTransform(url: string): string {
  return url.startsWith(CITE_PROTOCOL) ? url : defaultUrlTransform(url)
}

function decodeLocator(href: string): string | null {
  try {
    return decodeURIComponent(href.slice(CITE_PROTOCOL.length))
  } catch {
    return null
  }
}

interface MarkdownProps {
  text: string
  /** When given, cite:LOCATOR links render as chips that open the cited item. */
  citations?: Citations
  onCitationClick?: () => void
  className?: string
}

export function Markdown({ text, citations, onCitationClick, className }: MarkdownProps) {
  const components = useMemo<Components>(
    () => ({
      a: ({ href, children, node: _node, ...rest }) => {
        if (href?.startsWith(CITE_PROTOCOL)) {
          const locator = decodeLocator(href)
          const citation = locator ? citations?.[locator] : undefined
          const to = citation ? citationHref(citation) : null
          if (!citation) return null
          if (!to) return <span className="cite">{citation.label}</span>
          return (
            <Link to={to} className="cite" title={`Open ${citation.label}`} onClick={onCitationClick}>
              {citation.label}
            </Link>
          )
        }
        return (
          <a href={href} target="_blank" rel="noreferrer noopener" {...rest}>
            {children}
          </a>
        )
      },
    }),
    [citations, onCitationClick],
  )

  return (
    <div className={className ? `md ${className}` : 'md'}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        urlTransform={urlTransform}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}
