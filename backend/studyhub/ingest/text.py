"""Text normalization and chunking for PDFs, HTML, Markdown and transcripts."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf
from markdownify import markdownify

from ..store import ChunkIn
from ..util import clock, normalize_space, parse_dt, sha256


# ---------------------------------------------------------------- PDFs

@dataclass
class PdfPage:
    page: int          # 1-based
    text: str          # text layer (GoodNotes includes recognized handwriting here)
    image_hash: str | None


def read_pdf(data: bytes, *, hash_images: bool = False) -> list[PdfPage]:
    """Extract each page's text layer. With hash_images, also fingerprint a low-res render.

    The fingerprint lets handwriting transcriptions survive re-exports of a notebook:
    unchanged pages keep their transcription, edited pages get transcribed again.
    """
    pages: list[PdfPage] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for i, page in enumerate(doc, start=1):
            text = normalize_space(page.get_text("text"))
            image_hash = None
            if hash_images:
                pix = page.get_pixmap(dpi=36, colorspace=pymupdf.csGRAY)
                image_hash = sha256(pix.samples)
            pages.append(PdfPage(page=i, text=text, image_hash=image_hash))
    return pages


def pdf_chunks(pages: list[PdfPage]) -> list[ChunkIn]:
    return [ChunkIn(text=p.text, page=p.page, image_hash=p.image_hash) for p in pages]


def pdf_markdown(pages: list[PdfPage]) -> str:
    return "\n\n".join(f"--- page {p.page} ---\n{p.text}" for p in pages if p.text)


def render_page_png(data: bytes, page: int, dpi: int = 110) -> bytes:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")


# ---------------------------------------------------------------- HTML / Markdown

def html_to_markdown(html: str | None) -> str:
    if not html:
        return ""
    md = markdownify(html, heading_style="ATX", strip=["script", "style", "img"])
    return normalize_space(md)


def markdown_chunks(markdown: str, max_chars: int = 1800) -> list[ChunkIn]:
    """Split on headings, then pack paragraphs up to max_chars."""
    if not markdown.strip():
        return []
    sections = re.split(r"(?m)^(?=#{1,4} )", markdown)
    chunks: list[ChunkIn] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        buf = ""
        for para in re.split(r"\n{2,}", section):
            if buf and len(buf) + len(para) + 2 > max_chars:
                chunks.append(ChunkIn(text=buf))
                buf = ""
            while len(para) > max_chars:
                chunks.append(ChunkIn(text=para[:max_chars]))
                para = para[max_chars:]
            buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            chunks.append(ChunkIn(text=buf))
    return chunks


# ---------------------------------------------------------------- transcripts

@dataclass
class Utterance:
    seconds: int       # offset from the start of the recording
    text: str
    speaker: str | None = None


def utterances_from_granola(items: list[dict]) -> tuple[list[Utterance], str | None, int]:
    """Granola transcript items -> utterances with offsets, start time, duration in seconds."""
    times = [parse_dt(i.get("start_time")) for i in items]
    start = min((t for t in times if t), default=None)
    out: list[Utterance] = []
    for item, t in zip(items, times):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        speaker = item.get("speaker") or {}
        who = speaker.get("name") or speaker.get("diarization_label") or (
            "me" if speaker.get("attribution") == "me" else None
        )
        offset = int((t - start).total_seconds()) if (t and start) else (out[-1].seconds if out else 0)
        out.append(Utterance(seconds=max(offset, 0), text=text, speaker=who))
    ends = [parse_dt(i.get("end_time")) for i in items]
    end = max((t for t in ends if t), default=None)
    duration = int((end - start).total_seconds()) if (start and end) else (out[-1].seconds if out else 0)
    return out, (start.strftime("%Y-%m-%dT%H:%M:%SZ") if start else None), duration


def transcript_chunks(utterances: list[Utterance], window_s: int = 150, max_chars: int = 2200) -> list[ChunkIn]:
    """Group utterances into windows of ~2.5 minutes, each starting at its first utterance's time."""
    chunks: list[ChunkIn] = []
    buf: list[str] = []
    start: int | None = None
    size = 0
    for u in utterances:
        if start is not None and (u.seconds - start >= window_s or size + len(u.text) > max_chars):
            chunks.append(ChunkIn(text=" ".join(buf), seconds=start))
            buf, start, size = [], None, 0
        if start is None:
            start = u.seconds
        buf.append(u.text)
        size += len(u.text) + 1
    if buf and start is not None:
        chunks.append(ChunkIn(text=" ".join(buf), seconds=start))
    return chunks


def transcript_markdown(summary: str | None, chunks: list[ChunkIn]) -> str:
    parts = []
    if summary:
        parts.append(f"## Summary\n\n{summary.strip()}")
    parts.append("## Transcript\n\n" + "\n\n".join(f"[{clock(c.seconds or 0)}] {c.text}" for c in chunks))
    return "\n\n".join(parts)
