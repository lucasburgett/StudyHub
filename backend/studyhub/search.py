"""Keyword search over chunks (SQLite FTS5, BM25 ranking)."""

from __future__ import annotations

import html
import re
import sqlite3
from dataclasses import dataclass

_STOPWORDS = set(
    "a an and are as at be by did do does for from how i in is it me my of on or so that the this to "
    "was we what when where which who why will with you your about into can could should would".split()
)


def fts_query(text: str, prefix_last: bool = False) -> str | None:
    """Turn free text into an FTS5 query: quoted terms joined by OR, ranked by BM25."""
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", text.lower()) if t not in _STOPWORDS]
    if not tokens:
        return None
    terms = [f'"{t}"' for t in dict.fromkeys(tokens)]
    if prefix_last and len(tokens[-1]) >= 2:
        terms[-1] += "*"
    return " OR ".join(terms)


@dataclass
class Hit:
    chunk_id: int
    resource_id: int
    course_id: int
    course_code: str
    source: str
    kind: str
    title: str
    page: int | None
    seconds: int | None
    lecture_id: int | None
    header: str
    snippet: str      # plain text with \x02 … \x03 around matches


def search(
    conn: sqlite3.Connection,
    text: str,
    *,
    course_id: int | None = None,
    sources: list[str] | None = None,
    kinds: list[str] | None = None,
    limit: int = 10,
    per_resource: int = 3,
    snippet_tokens: int = 32,
    prefix_last: bool = False,
) -> list[Hit]:
    query = fts_query(text, prefix_last=prefix_last)
    if not query:
        return []
    where = ["chunks_fts MATCH ?"]
    params: list = [query]
    if course_id is not None:
        where.append("r.course_id = ?")
        params.append(course_id)
    if sources:
        where.append(f"r.source IN ({', '.join('?' for _ in sources)})")
        params += sources
    if kinds:
        where.append(f"r.kind IN ({', '.join('?' for _ in kinds)})")
        params += kinds
    rows = conn.execute(
        f"""
        SELECT c.id AS chunk_id, c.resource_id, c.page, c.seconds, c.header,
               COALESCE(c.lecture_id, r.lecture_id) AS lecture_id,
               r.course_id, r.source, r.kind, r.title, co.code AS course_code,
               snippet(chunks_fts, 1, char(2), char(3), ' … ', {int(snippet_tokens)}) AS snippet
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN resources r ON r.id = c.resource_id
        JOIN courses co ON co.id = r.course_id
        WHERE {' AND '.join(where)}
        ORDER BY bm25(chunks_fts, 2.0, 1.0)
        LIMIT ?
        """,
        (*params, limit * 4),
    ).fetchall()
    hits: list[Hit] = []
    per: dict[int, int] = {}
    for r in rows:
        if per.get(r["resource_id"], 0) >= per_resource:
            continue
        per[r["resource_id"]] = per.get(r["resource_id"], 0) + 1
        hits.append(Hit(**dict(r)))
        if len(hits) >= limit:
            break
    return hits


def snippet_html(snippet: str) -> str:
    return html.escape(snippet).replace("\x02", "<mark>").replace("\x03", "</mark>")


def snippet_plain(snippet: str) -> str:
    return snippet.replace("\x02", "").replace("\x03", "")
