"""An example data set for trying StudyHub before connecting real accounts.

It goes through the same store and linking code as a real sync. The app shows a banner
while it's loaded, and the first real sync replaces it. Dates are relative to today so
the timeline always has recent weeks.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta

import pymupdf

from .config import get_settings
from .db import get_meta, set_meta
from .ingest.text import (
    Utterance,
    html_to_markdown,
    markdown_chunks,
    pdf_chunks,
    pdf_markdown,
    read_pdf,
    transcript_chunks,
    transcript_markdown,
)
from .store import FeedbackIn, ensure_course, replace_feedback, save_file, upsert_assignment, upsert_resource
from .sync import rebuild_course
from .util import iso, monday_of, parse_clock

# (day offset from the term's first Monday, title, slide pages)
LECTURES = [
    (1, "Introduction", [
        ("Introduction to computer vision", "CS 231N · Lecture 1"),
        ("Why vision is hard", "Viewpoint variation · illumination · deformation · occlusion · background clutter · intra-class variation"),
        ("A brief history", "1966 Summer Vision Project · Marr's stages of vision · feature-based recognition (SIFT) · ImageNet (2009) · AlexNet (2012)"),
        ("Course logistics", "Assignments 1–3 in Python/PyTorch · midterm · final project · Ed for questions · office hours on Canvas"),
    ]),
    (3, "Image classification with nearest neighbors", [
        ("Image classification", "The data-driven approach: collect labeled images, train a classifier, evaluate on new images"),
        ("Nearest neighbor", "L1 (Manhattan) distance: d1(I1, I2) = sum_p |I1_p - I2_p|. Training is O(1), prediction is O(N): the wrong way around."),
        ("k-nearest neighbors", "Take a majority vote among the k closest training images. Larger k smooths the decision boundaries."),
        ("Hyperparameters", "Choose k and the distance metric on a validation set, never on the test set. Cross-validation for small datasets."),
        ("Why kNN is never used on images", "Pixel distances aren't semantic, and the curse of dimensionality: covering the space needs exponentially many examples."),
    ]),
    (8, "Linear classifiers and loss functions", [
        ("Parametric approach", "f(x, W) = Wx + b. For CIFAR-10: x is 3072×1, W is 10×3072, b is 10×1."),
        ("Three views of a linear classifier", "Algebraic: matrix multiply. Visual: one template per class. Geometric: one hyperplane per class."),
        ("Multiclass SVM loss", "L_i = sum_{j != y_i} max(0, s_j - s_{y_i} + 1). Zero once the correct class wins by a margin of 1."),
        ("Softmax classifier", "P(y = k | x) = exp(s_k) / sum_j exp(s_j). Scores are unnormalized log probabilities."),
        ("Cross-entropy loss", "L_i = -log P(y_i | x_i). At initialization with small random W, L_i ≈ log(C): log(10) ≈ 2.3 for CIFAR-10."),
        ("Numerical stability", "Subtract max_j s_j from every score before exponentiating; the probabilities don't change."),
    ]),
    (10, "Regularization and optimization", [
        ("Regularization", "L(W) = (1/N) sum_i L_i + λR(W). L2: R(W) = sum W^2 prefers spreading weight; L1 prefers sparse weights."),
        ("Gradient descent", "W ← W − α ∇L(W). Numerical gradient for checking, analytic gradient in practice."),
        ("Stochastic gradient descent", "Estimate the gradient on a minibatch of 32–256 examples. Much faster per step, noisier."),
        ("Problems with SGD", "Poor conditioning causes zig-zagging, saddle points and local minima stall it, and minibatch noise."),
        ("Momentum and Adam", "Momentum keeps a velocity: v ← ρv + ∇L, W ← W − αv. Adam combines momentum with per-parameter scaling (RMSProp) and bias correction."),
    ]),
    (15, "Neural networks and backpropagation", [
        ("Neural networks", "Two-layer net: f = W2 max(0, W1 x). The nonlinearity is what makes depth useful."),
        ("Computational graphs", "Break a function into simple nodes; each node knows its local gradient."),
        ("Backpropagation", "Upstream gradient × local gradient = downstream gradient (the chain rule, applied node by node)."),
        ("Gradient patterns", "Add gate: distributes gradient. Mul gate: swaps inputs. Max gate: routes gradient to the max. Copy gate: adds gradients."),
        ("Vectorized backprop", "For y = Wx: dL/dW = (dL/dy) xᵀ and dL/dx = Wᵀ (dL/dy). Shapes must match the variable's shape."),
    ]),
    (17, "Convolutional neural networks", [
        ("Convolution layer", "Slide a 5×5×3 filter over a 32×32×3 image: each position gives one number, one filter gives one activation map."),
        ("Output size", "(N − F + 2P) / S + 1. A 7×7 input with a 3×3 filter, stride 1, no padding gives 5×5."),
        ("Pooling", "Max pooling 2×2 with stride 2 halves width and height and keeps depth."),
        ("A typical ConvNet", "[(CONV − RELU) × N − POOL] × M − (FC − RELU) × K − SOFTMAX"),
    ]),
]

# Recordings, as (mm:ss, text). Lecture 1 was not recorded.
TRANSCRIPTS = {
    2: [
        ("0:00", "Okay, let's get started. Today is image classification, which is the core task for this whole course."),
        ("3:40", "The problem is that the computer just sees a grid of numbers, a 3D array of pixel values, and a small change in viewpoint changes every one of those numbers."),
        ("9:15", "So instead of hand-writing rules for what a cat is, we take the data-driven approach: collect a dataset of labeled images, train a model, and evaluate it on new images."),
        ("16:30", "The simplest classifier is nearest neighbor. At training time you just memorize the data. At test time you find the most similar training image and copy its label."),
        ("22:05", "To compare images we need a distance. The L1 distance sums absolute pixel differences. The L2 distance is the square root of the sum of squared differences, and it doesn't depend on the coordinate frame the way L1 does."),
        ("31:50", "Notice training is constant time and prediction is linear in the training set size. That's backwards from what we want: we're happy to train slowly if prediction is fast."),
        ("38:20", "k nearest neighbors takes a majority vote over the k closest points. With k equals one you get jagged boundaries and islands of noise; larger k smooths them."),
        ("47:45", "k and the distance metric are hyperparameters. Never pick them using the test set. Split off a validation set, or use cross-validation if you have little data, and only touch the test set once at the very end."),
        ("58:10", "In practice nobody uses kNN on raw pixels. Distances between pixel vectors don't capture meaning: shifting, tinting or occluding an image can leave the L2 distance unchanged."),
        ("66:30", "And there's the curse of dimensionality. To cover the space densely you need a number of training points that grows exponentially with the dimension."),
        ("74:00", "Next time: linear classifiers, which are the building block for neural networks. Assignment 1 comes out Thursday."),
    ],
    3: [
        ("0:00", "Last time we saw kNN. Today we move to the parametric approach, the linear classifier: f of x and W equals W x plus b."),
        ("6:30", "For CIFAR-10 each image is 32 by 32 by 3, so 3072 numbers. W is 10 by 3072 and we get one score per class."),
        ("12:10", "You can read each row of W as a template for its class. If you visualize the learned horse template it has two heads, because it's averaging left-facing and right-facing horses."),
        ("19:00", "Geometrically each class score is a hyperplane in pixel space. Anything that isn't linearly separable is going to be hard."),
        ("26:40", "Now we need a loss function to tell us how bad W is. The multiclass SVM loss sums over the incorrect classes max of zero and s j minus s y i plus one."),
        ("33:15", "Quick question for you: what's the SVM loss at initialization when all scores are about zero? It's the number of classes minus one. That's a useful sanity check."),
        ("41:12", "The other common choice is the softmax classifier. We interpret the scores as unnormalized log probabilities, exponentiate them, normalize, and the loss is the negative log probability of the correct class. That's cross-entropy."),
        ("47:30", "Sanity check again: with small random weights all classes are equally likely, so the loss should be about log of C. For ten classes that's log 10, about 2.3. If your first loss is far from that, you have a bug."),
        ("53:05", "The SVM is happy once the correct score beats the others by the margin. Softmax is never fully satisfied; it always wants more probability on the right class."),
        ("61:40", "One practical note for Assignment 1: exponentials overflow. Subtract the max score from all scores before you exponentiate. It doesn't change the probabilities."),
        ("70:20", "Next lecture: regularization, and how we actually find a good W with optimization."),
    ],
    4: [
        ("0:00", "So far we have a score function and a loss. A W that fits the training data perfectly isn't unique, and we want the simpler one. That's regularization."),
        ("7:45", "We add lambda times R of W to the data loss. L2 regularization prefers spreading weight across all inputs; L1 prefers sparse weights."),
        ("15:20", "Optimization: follow the slope. The gradient is the vector of partial derivatives, and the negative gradient is the direction of steepest descent."),
        ("22:10", "Always compute the analytic gradient, then check it against the numerical gradient. That's the gradient check you'll do in the assignment."),
        ("30:05", "Computing the loss over the full training set for every step is expensive, so we use stochastic gradient descent on minibatches, typically 32 to 256 examples."),
        ("39:50", "SGD has problems. If the loss changes quickly in one direction and slowly in another, it zig-zags. It also stalls at saddle points, which are very common in high dimensions."),
        ("48:30", "Momentum fixes a lot of this: keep a running velocity of gradients and step along the velocity. Rho is usually 0.9 or 0.99."),
        ("57:15", "Adam combines momentum with RMSProp's per-parameter scaling, plus bias correction for the first steps. Beta1 0.9, beta2 0.999, learning rate 1e-3 is a great default."),
        ("68:40", "The learning rate is the first hyperparameter to tune. Decay it over training, step or cosine schedules both work."),
    ],
    5: [
        ("0:00", "Today: neural networks, and backpropagation, which is how we get their gradients."),
        ("5:30", "A two-layer network is W2 times max of zero and W1 x. Without the max, the two matrices collapse into one linear classifier."),
        ("14:20", "Deriving gradients by hand on paper doesn't scale. Instead we draw a computational graph and apply the chain rule node by node."),
        ("23:45", "At each node: the downstream gradient equals the upstream gradient times the local gradient. That's all backprop is."),
        ("34:10", "Some patterns: an add gate distributes the gradient to both inputs. A multiply gate swaps the inputs. A max gate routes the gradient to whichever input was larger. A copy gate adds up the gradients."),
        ("45:00", "With vectors, the local gradients are Jacobians, but you never form them explicitly. For y equals W x, dL dW is the upstream gradient times x transpose."),
        ("55:30", "Trick for getting it right: the gradient with respect to a variable always has the same shape as the variable. Match the shapes and the formula usually falls out."),
        ("66:00", "Assignment 1 asks you to backprop through a two-layer net. Check every gradient numerically before you trust it."),
    ],
}

NOTES = {
    1: ["{d} Lecture 1 — intro\nvision is hard: viewpoint, illumination, occlusion, clutter, deformation\nImageNet 2009 -> AlexNet 2012 (GPU + big data)\nAssignments: A1 kNN/SVM/softmax, A2 nets, A3 CNNs"],
    2: ["{d} image classification\ndata-driven approach: dataset -> train -> predict\nNN: memorize, predict = copy label of closest\nL1 = sum |I1 - I2|   L2 = sqrt(sum (I1-I2)^2)",
        "kNN: vote among k closest, bigger k = smoother boundary\nhyperparams -> pick on VALIDATION, test only once!!\ncurse of dimensionality: # points grows exponentially w/ dim"],
    3: ["{d} linear classifiers\nf(x,W) = Wx + b   W: 10 x 3072\ntemplates: horse w/ two heads (?)\nSVM loss: L_i = sum_{j!=y} max(0, s_j - s_y + 1)",
        "softmax: P = e^s_k / sum e^s_j\ncross-entropy L_i = -log P(y_i)\ninit sanity check: L ~ log(C) = log 10 ~ 2.3\nsubtract max score before exp (overflow)"],
    4: ["{d} regularization + optimization\nL = data loss + lambda R(W)\nL2 spreads weights, L1 sparse\nSGD minibatch 32-256\nmomentum rho=0.9   Adam b1=.9 b2=.999 lr=1e-3"],
    5: ["{d} backprop\ndownstream = upstream x local\nadd: distribute  mul: swap  max: route  copy: add\ndL/dW = dL/dy x^T -- shape of grad = shape of var"],
}

SYLLABUS_HTML = """
<h2>CS 231N: Deep Learning for Computer Vision (example syllabus)</h2>
<p>Lectures are Tuesdays and Thursdays. Slides are posted on Canvas before each lecture.</p>
<h3>Grading</h3>
<ul><li>Assignments 1–3: 45%</li><li>Quizzes: 10%</li><li>Midterm: 15%</li><li>Final project: 30%</li></ul>
<h3>Collaboration and AI policy</h3>
<p>You may discuss concepts with classmates and with AI tools. Code and written answers you submit must be your own,
and you may not paste assignment questions into an AI tool to get solutions.</p>
"""


# The built-in PDF fonts only cover Latin-1, so spell out the few symbols the slides use.
_ASCII = str.maketrans({"−": "-", "≈": "~", "←": "<-", "→": "->", "λ": "lambda ", "α": "alpha ", "∇": "grad ",
                        "ρ": "rho", "ᵀ": "^T", "–": "-", "’": "'"})


def _slides_pdf(title: str, pages: list[tuple[str, str]]) -> bytes:
    doc = pymupdf.open()
    for heading, body in pages:
        heading, body = heading.translate(_ASCII), body.translate(_ASCII)
        page = doc.new_page(width=720, height=405)
        page.insert_textbox(pymupdf.Rect(40, 40, 680, 110), heading, fontsize=26, fontname="helv")
        page.insert_textbox(pymupdf.Rect(40, 120, 680, 380), body, fontsize=16, fontname="helv")
        page.insert_textbox(pymupdf.Rect(40, 380, 680, 400), f"CS 231N · {title} · example slides", fontsize=8,
                            fontname="helv", color=(0.5, 0.5, 0.5))
    data = doc.tobytes()
    doc.close()
    return data


def _notes_pdf(pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        text = text.translate(_ASCII)
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(pymupdf.Rect(50, 50, 562, 742), text, fontsize=15, fontname="tiro", color=(0.1, 0.15, 0.45))
    data = doc.tobytes()
    doc.close()
    return data


def has_real_data(conn: sqlite3.Connection) -> bool:
    return get_meta(conn, "demo") != "1" and conn.execute("SELECT 1 FROM courses").fetchone() is not None


def load_demo(conn: sqlite3.Connection, today: date | None = None) -> int:
    """Load the example data set. Returns the course id."""
    tz = get_settings().tz
    today = today or datetime.now(tz).date()
    term_monday = monday_of(today) - timedelta(days=14)

    def at(day: date, hh: int, mm: int = 0) -> str:
        return iso(datetime.combine(day, time(hh, mm), tzinfo=tz))

    course_id = ensure_course(
        conn, "CS 231N", title="Deep Learning for Computer Vision", term="Autumn 2026 (example)",
        term_start=term_monday.isoformat(), canvas_url="https://canvas.stanford.edu",
        site_url="https://cs231n.stanford.edu",
    )
    assert course_id is not None

    syllabus = html_to_markdown(SYLLABUS_HTML)
    upsert_resource(conn, course_id=course_id, source="canvas", kind="page", external_id="demo:syllabus",
                    title="Syllabus", occurred_at=at(term_monday, 9), markdown=syllabus,
                    chunks=markdown_chunks(syllabus))

    notebook_pages: list[str] = []
    for n, (offset, title, pages) in enumerate(LECTURES, start=1):
        day = term_monday + timedelta(days=offset)
        pdf = _slides_pdf(title, pages)
        parsed = read_pdf(pdf)
        slug = title.lower().replace(" ", "_").replace(",", "")
        upsert_resource(
            conn, course_id=course_id, source="canvas", kind="slides", external_id=f"demo:slides:{n}",
            title=f"lecture_{n}_{slug}.pdf", occurred_at=at(day - timedelta(days=1), 17), file_path=save_file(pdf),
            page_count=len(parsed), markdown=pdf_markdown(parsed), chunks=pdf_chunks(parsed),
            meta={"module": f"Week {offset // 7 + 1}"},
        )
        if day > today:
            continue
        for text in NOTES.get(n, []):
            notebook_pages.append(text.replace("{d}", f"{day.month}/{day.day}"))
        if n in TRANSCRIPTS:
            utterances = [Utterance(seconds=parse_clock(t) or 0, text=s) for t, s in TRANSCRIPTS[n]]
            chunks = transcript_chunks(utterances)
            summary = f"Lecture {n} covered {title.lower()}. " + TRANSCRIPTS[n][1][1]
            upsert_resource(
                conn, course_id=course_id, source="granola", kind="transcript", external_id=f"demo:rec:{n}",
                title=f"CS 231N Lecture {n}", occurred_at=at(day, 10, 30), summary=summary,
                markdown=transcript_markdown(summary, chunks), duration_min=utterances[-1].seconds // 60 + 4,
                chunks=chunks,
            )

    if notebook_pages:
        pdf = _notes_pdf(notebook_pages)
        parsed = read_pdf(pdf, hash_images=True)
        upsert_resource(
            conn, course_id=course_id, source="goodnotes", kind="notes", external_id="demo:notes",
            title="CS 231N lecture notes", occurred_at=at(today, 8), file_path=save_file(pdf),
            page_count=len(parsed), markdown=pdf_markdown(parsed), chunks=pdf_chunks(parsed),
        )

    for offset, title, text in [
        (0, "Welcome to CS 231N", "<p>Welcome! Lecture slides go up on Canvas the evening before each lecture. "
                                  "Assignment 1 is released at the end of week 1.</p>"),
        (4, "Assignment 1 released", "<p>Assignment 1 (kNN, SVM, softmax, two-layer net) is out. It is due at the end "
                                     "of week 4. Start early: the vectorized gradients take time.</p>"),
        (12, "Quiz 1 grades posted", "<p>Quiz 1 grades are on Gradescope. Regrade requests are open for one week.</p>"),
    ]:
        day = term_monday + timedelta(days=offset)
        if day > today:
            continue
        md = html_to_markdown(text)
        upsert_resource(conn, course_id=course_id, source="canvas", kind="announcement",
                        external_id=f"demo:ann:{offset}", title=title, occurred_at=at(day, 12), markdown=md,
                        chunks=markdown_chunks(md))

    a1_spec = html_to_markdown(
        "<p>In this assignment you will implement a kNN classifier, a multiclass SVM, a softmax classifier and a "
        "two-layer neural network on CIFAR-10.</p><ul><li>Q1: k-nearest neighbors (20 points)</li>"
        "<li>Q2: SVM loss and gradient, fully vectorized (25 points)</li>"
        "<li>Q3: Softmax loss and gradient, numerically stable (25 points)</li>"
        "<li>Q4: Two-layer network with backprop (30 points)</li></ul>"
    )
    spec_id, _ = upsert_resource(conn, course_id=course_id, source="canvas", kind="spec",
                                 external_id="demo:spec:a1", title="Assignment 1",
                                 occurred_at=at(term_monday + timedelta(days=4), 12), markdown=a1_spec,
                                 chunks=markdown_chunks(a1_spec))
    due_a1 = term_monday + timedelta(days=25)
    upsert_assignment(conn, course_id=course_id, source="canvas", external_id="demo:canvas:a1",
                      title="Assignment 1", due_at=at(due_a1, 23, 59), points=100, score=None,
                      status="upcoming", url=None, spec_resource_id=spec_id)
    upsert_assignment(conn, course_id=course_id, source="gradescope", external_id="demo:gs:a1",
                      title="Assignment 1", due_at=at(due_a1, 23, 59), points=100, score=None,
                      status="upcoming", url=None)
    upsert_assignment(conn, course_id=course_id, source="canvas", external_id="demo:canvas:a2",
                      title="Assignment 2", due_at=at(term_monday + timedelta(days=46), 23, 59), points=100,
                      score=None, status="upcoming", url=None)

    quiz_day = term_monday + timedelta(days=11)
    if quiz_day <= today:
        quiz_id, _ = upsert_assignment(conn, course_id=course_id, source="gradescope", external_id="demo:gs:quiz1",
                                       title="Quiz 1", due_at=at(quiz_day, 11, 50), points=10, score=8.5,
                                       status="graded", url=None)
        replace_feedback(conn, quiz_id, [
            FeedbackIn("Q1: Choosing k", 3, 3, ["Correct: tune k on a validation set (0)"]),
            FeedbackIn("Q2: Softmax loss at initialization", 1.5, 3,
                       ["Used log base 10 instead of the natural log (-1.5)"],
                       "The expected loss is ln(10) ≈ 2.3, not log10(10) = 1."),
            FeedbackIn("Q3: L1 vs L2 regularization", 4, 4, ["Correct (0)"]),
        ])

    rebuild_course(conn, course_id)
    set_meta(conn, "demo", "1")
    conn.commit()
    return course_id
