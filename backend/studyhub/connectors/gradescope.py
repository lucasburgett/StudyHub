"""Gradescope, through the unofficial `gradescopeapi` client (it logs in and reads pages).

Gradescope has no public API, so this connector is the most likely to break when the
site changes. Scores and due dates come from the course page. Per-question rubric
feedback is read best-effort from the submission page's embedded data; if that fails,
the assignment still syncs with its total score.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from ..store import FeedbackIn, ensure_course, find_course, replace_feedback, upsert_assignment
from ..util import iso, title_key
from .base import SyncContext, log

BASE_URL = "https://www.gradescope.com"
_SEASONS = {"winter": 0, "spring": 1, "summer": 2, "fall": 3, "autumn": 3}


def _login(settings: Settings):
    from gradescopeapi.classes.connection import GSConnection

    gs = GSConnection()
    try:
        gs.login(settings.gradescope_email, settings.gradescope_password)
    except ValueError as e:
        raise RuntimeError(
            "Gradescope login failed. With Stanford SSO, set a Gradescope password first "
            "(gradescope.com → Log in → Forgot password)."
        ) from e
    return gs


def _term_rank(course: Any) -> tuple[int, int]:
    year = int(course.year) if str(getattr(course, "year", "")).isdigit() else 0
    return year, _SEASONS.get(str(getattr(course, "semester", "")).lower(), -1)


def _status(a: Any) -> str:
    if a.grade is not None:
        return "graded"
    state = (a.submissions_status or "").lower()
    if "submitted" in state and "no" not in state:
        return "submitted"
    if a.due_date is not None:
        due = a.due_date if a.due_date.tzinfo else a.due_date.replace(tzinfo=timezone.utc)
        return "upcoming" if due > datetime.now(timezone.utc) else "missing"
    return "unknown"


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_submission_props(props: dict[str, Any]) -> list[FeedbackIn]:
    """Per-question scores, applied rubric items and comments from a submission viewer's props."""
    questions = props.get("questions") or []
    scored = {str(q.get("question_id")): q for q in props.get("question_submissions") or []}
    rubric: dict[str, list[dict]] = {}
    for item in props.get("rubric_items") or []:
        rubric.setdefault(str(item.get("question_id")), []).append(item)

    applied: set[str] = set()
    for ev in props.get("evaluations") or props.get("question_submission_evaluations") or []:
        for ri in ev.get("rubric_items") or []:
            if isinstance(ri, dict) and ri.get("present"):
                applied.add(str(ri.get("rubric_item_id") or ri.get("id")))
            elif isinstance(ri, (int, str)):
                applied.add(str(ri))
        for rid in ev.get("rubric_item_ids") or []:
            applied.add(str(rid))

    out: list[FeedbackIn] = []
    for q in questions:
        qid = str(q.get("id"))
        sub = scored.get(qid, {})
        items = [
            f"{i.get('description') or i.get('title') or 'Rubric item'} ({i.get('weight')})"
            for i in rubric.get(qid, [])
            if i.get("present") or str(i.get("id")) in applied
        ]
        comments = [c.get("text") for c in (sub.get("annotations") or sub.get("comments") or []) if isinstance(c, dict)]
        name = q.get("full_index") or q.get("numbered_title") or q.get("title") or f"Question {len(out) + 1}"
        if q.get("title") and q.get("full_index"):
            name = f"Q{q['full_index']}: {q['title']}"
        out.append(FeedbackIn(
            question=str(name),
            score=_num(sub.get("score")),
            max_score=_num(q.get("weight")),
            rubric_items=items,
            comment="\n".join(c for c in comments if c) or None,
        ))
    return out


def fetch_feedback(session: Any, course_id: str, assignment_id: str) -> list[FeedbackIn] | None:
    resp = session.get(f"{BASE_URL}/courses/{course_id}/assignments/{assignment_id}")
    if resp.status_code != 200:
        return None
    m = re.search(r'data-react-class="AssignmentSubmissionViewer"[^>]*data-react-props="([^"]+)"', resp.text)
    if not m:
        m = re.search(r'data-react-props="([^"]+)"[^>]*data-react-class="AssignmentSubmissionViewer"', resp.text)
    if not m:
        return None
    return parse_submission_props(json.loads(html.unescape(m.group(1))))


class GradescopeConnector:
    source = "gradescope"

    def check(self, settings: Settings) -> str:
        gs = _login(settings)
        student = gs.account.get_courses().get("student", {})
        names = ", ".join(c.name for c in student.values()) or "none"
        return f"Signed in. {len(student)} courses as a student: {names}."

    def sync(self, ctx: SyncContext) -> None:
        gs = _login(ctx.settings)
        student: dict[str, Any] = gs.account.get_courses().get("student", {})
        if not student:
            return
        has_canvas = ctx.conn.execute("SELECT 1 FROM courses WHERE canvas_id IS NOT NULL").fetchone()
        latest = max(_term_rank(c) for c in student.values())
        for gid, course in student.items():
            label = f"{course.name} {course.full_name}"
            course_id = find_course(ctx.conn, label)
            if course_id is None and not has_canvas and _term_rank(course) == latest:
                course_id = ensure_course(ctx.conn, label, title=course.full_name or None,
                                          term=f"{course.semester} {course.year}".strip())
            if course_id is None:
                continue
            ctx.conn.execute("UPDATE courses SET gradescope_id = ? WHERE id = ?", (gid, course_id))
            ctx.mark(course_id, changed=False)
            try:
                self._assignments(ctx, gs, gid, course_id, course.name)
            except Exception as e:  # one course's page format shouldn't stop the others
                ctx.warn(f"{course.name}: couldn't read assignments from Gradescope ({e}).")
            ctx.conn.commit()

    def _assignments(self, ctx: SyncContext, gs: Any, gid: str, course_id: int, name: str) -> None:
        feedback_failed = False
        for a in gs.account.get_assignments(gid):
            aid = a.assignment_id
            status = _status(a)
            assignment_id, changed = upsert_assignment(
                ctx.conn, course_id=course_id, source=self.source,
                external_id=f"{gid}:{aid or title_key(a.name)}", title=a.name.strip(),
                due_at=iso(a.due_date), points=_num(a.max_grade), score=_num(a.grade), status=status,
                url=f"{BASE_URL}/courses/{gid}/assignments/{aid}" if aid else f"{BASE_URL}/courses/{gid}",
            )
            ctx.mark(course_id, changed)
            if status != "graded" or not aid or (not changed and _has_feedback(ctx, assignment_id)):
                continue
            try:
                items = fetch_feedback(gs.session, gid, aid)
            except Exception as e:  # page format changes should not stop the sync
                log.debug("feedback parse failed for %s/%s: %s", gid, aid, e)
                items = None
            if items:
                replace_feedback(ctx.conn, assignment_id, items)
            elif items is None:
                feedback_failed = True
        if feedback_failed:
            ctx.warn(f"{name}: couldn't read per-question feedback from Gradescope; totals synced.")


def _has_feedback(ctx: SyncContext, assignment_id: int) -> bool:
    return ctx.conn.execute("SELECT 1 FROM feedback WHERE assignment_id = ?", (assignment_id,)).fetchone() is not None
