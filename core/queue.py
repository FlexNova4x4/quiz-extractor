import asyncio
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.config import PROJECT_DIR
from core.engine import TelethonEngine
from core.models import QuizJob

logger = logging.getLogger("QuizJobQueue")


class JobQueueManager:
    """
    Serial Job Queue Manager for Telegram Quiz Extraction.
    Ensures:
    - Exactly one active quiz extraction per Telethon session.
    - FIFO sequential processing.
    - Isolated state per job.
    - Deduplication of pending/running requests.
    - Safe JSON state persistence.
    - Pacing between consecutive jobs to mitigate FloodWait.
    """

    def __init__(
        self,
        engine: TelethonEngine,
        state_file: Optional[Path] = None,
        job_completed_callback: Optional[Callable[[QuizJob], Any]] = None,
        status_update_callback: Optional[Callable[[QuizJob, str], Any]] = None,
        progress_update_callback: Optional[Callable[[QuizJob, int, int], Any]] = None,
        inter_job_delay: float = 2.0,
    ):
        self.engine = engine
        self.state_file = state_file or (PROJECT_DIR / "jobs_state.json")
        self.job_completed_callback = job_completed_callback
        self.status_update_callback = status_update_callback
        self.progress_update_callback = progress_update_callback
        self.inter_job_delay = inter_job_delay

        self._queue: asyncio.Queue[QuizJob] = asyncio.Queue()
        self._jobs: Dict[str, QuizJob] = {}
        self._active_job: Optional[QuizJob] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False

        self.load_state()

    def load_state(self):
        """Loads persistent job records from disk."""
        if not self.state_file.exists():
            return
        try:
            raw = self.state_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            for j_data in data.get("jobs", []):
                job = QuizJob.from_dict(j_data)
                # If interrupted during last run, mark partial/interrupted
                if job.status in {"running", "waiting_flood"}:
                    job.status = "partial" if job.questions_done > 0 else "failed"
                    job.error_message = "Process restarted while job was running."
                self._jobs[job.job_id] = job
        except Exception as e:
            logger.warning(f"Could not load state file {self.state_file}: {e}")

    def save_state(self):
        """Atomically persists job states to disk."""
        try:
            data = {
                "updated_at": time.time(),
                "jobs": [j.to_dict() for j in self._jobs.values()],
            }
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_file)
        except Exception as e:
            logger.warning(f"Could not save state file: {e}")

    def find_duplicate_active_job(
        self, provider: str, payload: str
    ) -> Optional[Tuple[QuizJob, int]]:
        """
        Checks if an identical job is currently running or queued.
        Returns (job, queue_position) if found.
        """
        if self._active_job and self._active_job.provider == provider and self._active_job.payload == payload:
            return self._active_job, 0

        pos = 1
        for job in self._jobs.values():
            if job.status == "queued" and job.provider == provider and job.payload == payload:
                return job, pos
            if job.status == "queued":
                pos += 1
        return None

    def find_completed_job(self, provider: str, payload: str) -> Optional[QuizJob]:
        """Checks if this exact quiz was already successfully extracted."""
        for job in reversed(list(self._jobs.values())):
            if (
                job.provider == provider
                and job.payload == payload
                and job.status == "completed"
                and job.output_path
                and Path(job.output_path).exists()
            ):
                return job
        return None

    def add_job(
        self,
        user_id: int,
        chat_id: int,
        provider: str,
        payload: str,
        title: str = "",
        expected_questions: int = 0,
        raw_input: str = "",
    ) -> Tuple[QuizJob, int, bool]:
        """
        Enqueues a new quiz extraction job.
        Returns (QuizJob, queue_position, is_new).
        """
        # Deduplication check: active or queued
        dup = self.find_duplicate_active_job(provider, payload)
        if dup:
            return dup[0], dup[1], False

        # Generate unique job ID
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        uid = secrets.token_hex(3)
        job_id = f"job_{timestamp}_{uid}"

        job = QuizJob(
            job_id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            provider=provider,
            payload=payload,
            raw_input=raw_input,
            title=title,
            expected_questions=expected_questions,
            status="queued",
            created_at=time.time(),
        )

        self._jobs[job_id] = job
        self._queue.put_nowait(job)
        self.save_state()

        queue_pos = self._queue.qsize()
        logger.info(f"Enqueued {job_id} at position {queue_pos}")
        return job, queue_pos, True

    def get_job(self, job_id: str) -> Optional[QuizJob]:
        return self._jobs.get(job_id)

    def get_status_summary(self) -> Tuple[Optional[QuizJob], List[QuizJob]]:
        pending = [j for j in self._jobs.values() if j.status == "queued"]
        return self._active_job, pending

    def start_worker(self):
        """Starts the background worker loop if not already running."""
        if self._worker_task is None or self._worker_task.done():
            self._running = True
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop_worker(self):
        """Stops the worker loop gracefully."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def _worker_loop(self):
        """
        Sequential execution loop.
        Processes one job at a time in FIFO order.
        """
        logger.info("Serial Job Queue worker started.")
        while self._running:
            try:
                job = await self._queue.get()
                self._active_job = job
                self.save_state()

                # Callbacks for status and progress
                async def on_status(msg: str):
                    self.save_state()
                    if self.status_update_callback:
                        try:
                            res = self.status_update_callback(job, msg)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as e:
                            logger.debug(f"Status callback error: {e}")

                async def on_progress(done: int, total: int):
                    if self.progress_update_callback:
                        try:
                            res = self.progress_update_callback(job, done, total)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as e:
                            logger.debug(f"Progress callback error: {e}")

                try:
                    await self.engine.execute_job(
                        job=job,
                        status_callback=on_status,
                        progress_callback=on_progress,
                    )
                except Exception as e:
                    logger.error(f"Uncaught exception in job {job.job_id}: {e}", exc_info=True)
                    job.status = "failed"
                    job.error_message = str(e)
                finally:
                    self._active_job = None
                    self.save_state()
                    self._queue.task_done()

                    # Notify completion
                    if self.job_completed_callback:
                        try:
                            res = self.job_completed_callback(job)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as exc:
                            logger.error(f"Error in job_completed_callback: {exc}")

                    # Conservative inter-job pacing to protect against FloodWait on back-to-back jobs
                    if self.inter_job_delay > 0:
                        await asyncio.sleep(self.inter_job_delay)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected queue error: {e}", exc_info=True)
                await asyncio.sleep(1.0)
