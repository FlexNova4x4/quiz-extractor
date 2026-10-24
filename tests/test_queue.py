import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from core.models import QuizJob
from core.queue import JobQueueManager


class MockEngine:
    def __init__(self):
        self.executed_jobs = []
        self.lock = asyncio.Lock()

    async def initialize(self):
        pass

    async def disconnect(self):
        pass

    async def execute_job(self, job: QuizJob, status_callback=None, progress_callback=None):
        # Record execution order
        self.executed_jobs.append(job.job_id)
        # Simulate brief work
        await asyncio.sleep(0.05)
        job.status = "completed"
        job.questions_done = job.expected_questions or 10
        job.output_path = f"/tmp/{job.payload}.txt"
        return job


@pytest.mark.asyncio
async def test_job_queue_five_jobs_serial_execution(tmp_path: Path):
    engine = MockEngine()
    state_file = tmp_path / "test_jobs.json"
    completed_jobs = []

    def on_complete(job):
        completed_jobs.append(job)

    mgr = JobQueueManager(
        engine=engine,
        state_file=state_file,
        job_completed_callback=on_complete,
        inter_job_delay=0.01,
    )
    mgr.start_worker()

    # Enqueue 5 jobs
    job_ids = []
    for i in range(1, 6):
        job, pos, is_new = mgr.add_job(
            user_id=100 + i,
            chat_id=200 + i,
            provider="quizbot",
            payload=f"payload_{i}",
            title=f"Quiz {i}",
            expected_questions=10,
        )
        assert is_new is True
        job_ids.append(job.job_id)

    # Wait for all 5 jobs to finish
    await asyncio.sleep(0.5)
    await mgr.stop_worker()

    # Verify FIFO order
    assert len(engine.executed_jobs) == 5
    assert engine.executed_jobs == job_ids
    assert len(completed_jobs) == 5

    # Verify state persistence
    assert state_file.exists()
    state_data = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(state_data["jobs"]) == 5


@pytest.mark.asyncio
async def test_job_deduplication(tmp_path: Path):
    engine = MockEngine()
    state_file = tmp_path / "test_dup.json"

    mgr = JobQueueManager(engine=engine, state_file=state_file)

    # Add first job
    job1, pos1, is_new1 = mgr.add_job(
        user_id=111,
        chat_id=222,
        provider="quizbot",
        payload="same_payload_xyz",
        title="Title 1",
    )
    assert is_new1 is True

    # Try adding identical job while first is queued
    job2, pos2, is_new2 = mgr.add_job(
        user_id=111,
        chat_id=222,
        provider="quizbot",
        payload="same_payload_xyz",
        title="Title 1",
    )
    assert is_new2 is False
    assert job2.job_id == job1.job_id


@pytest.mark.asyncio
async def test_restart_recovery_from_state_file(tmp_path: Path):
    state_file = tmp_path / "jobs_state.json"

    # Pre-populate state file with 1 completed and 1 interrupted job
    initial_data = {
        "jobs": [
            {
                "job_id": "job_done_1",
                "user_id": 1,
                "chat_id": 2,
                "provider": "quizbot",
                "payload": "p1",
                "raw_input": "link1",
                "title": "Completed Quiz",
                "expected_questions": 5,
                "questions_done": 5,
                "status": "completed",
                "created_at": 1000.0,
                "output_path": "/tmp/dummy.txt",
            },
            {
                "job_id": "job_interrupted_2",
                "user_id": 1,
                "chat_id": 2,
                "provider": "quizbot",
                "payload": "p2",
                "raw_input": "link2",
                "title": "Interrupted Quiz",
                "expected_questions": 5,
                "questions_done": 2,
                "status": "running",  # was running when crash happened
                "created_at": 1001.0,
            },
        ]
    }
    state_file.write_text(json.dumps(initial_data), encoding="utf-8")

    engine = MockEngine()
    mgr = JobQueueManager(engine=engine, state_file=state_file)

    # Verify loaded jobs
    j1 = mgr.get_job("job_done_1")
    assert j1 is not None
    assert j1.status == "completed"

    j2 = mgr.get_job("job_interrupted_2")
    assert j2 is not None
    assert j2.status == "partial"  # Recovered as partial, not silently re-running!
    assert "restarted" in j2.error_message
