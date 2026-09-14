"""Generic background jobs for streaming LLM calls over SSE.

Mirrors :mod:`mimama.core.transcribe`'s ``TranscriptionJob``/``Registry``
shape, but the actual work (which LLM call(s) to make, and what to do with
the result once it's ready — write a note, render a result fragment, etc.)
is supplied by the caller as a plain callable. This lets a single job/registry
pair back the "new file" AI generation, ad-hoc inference, and natural-language
query flows in ``mimama/server/gui.py``, each with a live console of streamed
text and a cooperative Stop button.

``work`` is called with the job itself; it should call ``job.emit(chunk)``
for each piece of streamed text (typically by passing ``job.emit`` as the LLM
client's ``on_chunk`` callback and ``job.cancel_event`` as its
``cancel_event``), and set ``job.html`` to the final rendered fragment before
returning. Raising :class:`mimama.core.llm.LLMCancelled` or any other
exception is caught and surfaced as ``job.error``.
"""

from __future__ import annotations

import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from mimama.core.llm import LLMCancelled

# sentinel put on a job's queue once no more output chunks will follow
DONE = object()


@dataclass
class InferenceJob:
    """A background LLM call whose streamed text and outcome are exposed
    over ``lines`` (for SSE) and ``html``/``error`` (once done)."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    lines: queue.Queue[object] = field(default_factory=queue.Queue)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    html: str | None = None
    error: str | None = None
    cancelled: bool = False

    def emit(self, chunk: str) -> None:
        self.lines.put(chunk)

    def start(self, work: Callable[[InferenceJob], None]) -> None:
        threading.Thread(target=self._run, args=(work,), daemon=True).start()

    def _run(self, work: Callable[[InferenceJob], None]) -> None:
        try:
            work(self)
        except LLMCancelled:
            self.cancelled = True
        except Exception as exc:  # noqa: BLE001 - surfaced to the GUI, not swallowed
            self.error = str(exc)
        finally:
            self.lines.put(DONE)


class InferenceJobRegistry:
    """In-memory registry of inference jobs (single-process, trusted-network tool)."""

    def __init__(self) -> None:
        self._jobs: dict[str, InferenceJob] = {}

    def create(self, work: Callable[[InferenceJob], None]) -> InferenceJob:
        job = InferenceJob()
        self._jobs[job.id] = job
        job.start(work)
        return job

    def get(self, job_id: str) -> InferenceJob | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.cancel_event.set()

    def discard(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
