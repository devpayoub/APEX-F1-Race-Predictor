"""Render-ready web service for APEX.

The dashboard is static, but a prediction takes 10-25 s (network fetch + Monte Carlo
+ prior scoring), which is too long for one HTTP request to hold open comfortably.
So predictions run as queued background jobs:

    POST /api/predict        -> {job_id}
    GET  /api/jobs/<job_id>  -> {status, stage, progress, result?}

The page polls the job and re-renders when it lands.

Run locally:
    python app.py            (dev server, http://localhost:5000)
Production:
    gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

import run as engine
from src import config, ml_model
from src.sources import jolpica

BASE = Path(__file__).resolve().parent
DASH = BASE / "dashboard"

DEFAULT_SIMS = int(os.environ.get("F1_DEFAULT_SIMS", "8000"))
MAX_SIMS = int(os.environ.get("F1_MAX_SIMS", "40000"))
MAX_QUEUE = 6
JOB_TTL = 60 * 30          # forget finished jobs after 30 minutes

app = Flask(__name__, static_folder=str(DASH), static_url_path="")
app.config["JSON_SORT_KEYS"] = False

STARTED_AT = time.time()
LATEST: dict | None = None          # most recent successful prediction
LATEST_LOCK = threading.Lock()


# --------------------------------------------------------------------- jobs --
class Job:
    __slots__ = ("id", "params", "status", "stage", "progress", "result",
                 "error", "created", "started", "finished")

    def __init__(self, params: dict):
        self.id = uuid.uuid4().hex[:12]
        self.params = params
        self.status = "queued"        # queued | running | done | error
        self.stage = "queued"
        self.progress = 0.0
        self.result: dict | None = None
        self.error: str | None = None
        self.created = time.time()
        self.started: float | None = None
        self.finished: float | None = None

    def public(self, include_result: bool = True) -> dict:
        out = {
            "job_id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "params": self.params,
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - (self.started or self.created), 1),
        }
        if include_result and self.status == "done":
            out["result"] = self.result
        return out


class JobManager:
    """One worker thread: predictions are CPU-bound, so serialising them is right."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._q: queue.Queue[Job] = queue.Queue(maxsize=MAX_QUEUE)
        self._lock = threading.Lock()
        self._prior = None
        self._prior_lock = threading.Lock()
        self._prior_state = "cold"       # cold | training | ready | failed
        threading.Thread(target=self._worker, daemon=True, name="apex-worker").start()
        threading.Thread(target=self.warm, daemon=True, name="apex-warm").start()

    # -- prior ---------------------------------------------------------------
    def warm(self) -> None:
        try:
            self.get_prior()
        except Exception:  # noqa: BLE001
            pass

    def get_prior(self):
        if self._prior is not None:
            return self._prior
        with self._prior_lock:
            if self._prior is not None:
                return self._prior
            self._prior_state = "training"
            try:
                self._prior = ml_model.train([2022, 2023, 2024, 2025, 2026])
                self._prior_state = "ready" if self._prior.ok else "failed"
            except Exception:  # noqa: BLE001
                self._prior_state = "failed"
                raise
            return self._prior

    @property
    def prior_state(self) -> str:
        return self._prior_state

    # -- queue ---------------------------------------------------------------
    def submit(self, params: dict) -> Job:
        job = Job(params)
        with self._lock:
            self._prune()
            self._jobs[job.id] = job
        try:
            self._q.put_nowait(job)
        except queue.Full:
            with self._lock:
                self._jobs.pop(job.id, None)
            raise RuntimeError("busy") from None
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _prune(self) -> None:
        cutoff = time.time() - JOB_TTL
        for jid in [j for j, job in self._jobs.items()
                    if job.finished and job.finished < cutoff]:
            self._jobs.pop(jid, None)

    def _worker(self) -> None:
        while True:
            job = self._q.get()
            try:
                self._run(job)
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                job.error = str(exc) or exc.__class__.__name__
                job.finished = time.time()
                app.logger.warning("job %s failed: %s", job.id, traceback.format_exc())
            finally:
                self._q.task_done()

    def _run(self, job: Job) -> None:
        global LATEST
        p = job.params
        job.status = "running"
        job.started = time.time()

        def progress(stage: str, pct: float) -> None:
            job.stage = stage
            job.progress = pct

        prior = None
        try:
            prior = self.get_prior()
        except Exception:  # noqa: BLE001
            prior = None        # engine falls back to its own training

        payload, diag = engine.run_round(
            p["season"], p["round"], p["sims"], prior=prior, progress=progress
        )

        job.stage = "saving"
        job.progress = 0.995
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (config.OUTPUT_DIR / "prediction.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        DASH.mkdir(parents=True, exist_ok=True)
        (DASH / "prediction.js").write_text(
            "window.PREDICTION = " + json.dumps(payload, ensure_ascii=False) + ";\n",
            encoding="utf-8")

        with LATEST_LOCK:
            LATEST = payload

        job.result = payload
        job.status = "done"
        job.stage = "done"
        job.progress = 1.0
        job.finished = time.time()
        app.logger.info("job %s done in %.1fs (%s R%s, %d sims)",
                        job.id, job.finished - (job.started or job.created),
                        p["season"], p["round"], p["sims"])


MANAGER = JobManager()


# ------------------------------------------------------------------- helpers --
def _schedule(season: int) -> list[dict]:
    sched = jolpica.schedule(season)
    today = date.today().isoformat()
    out = []
    for r in sched:
        loc = ((r.get("Circuit") or {}).get("Location") or {})
        out.append({
            "round": int(r.get("round") or 0),
            "name": r.get("raceName"),
            "date": r.get("date"),
            "time": r.get("time"),
            "circuit": (r.get("Circuit") or {}).get("circuitName"),
            "location": loc.get("locality"),
            "country": loc.get("country"),
            "completed": (r.get("date") or "9999") < today,
        })
    return out


def _default_round(races: list[dict]) -> int:
    """Default to the next race that has not run yet, else the most recent one."""
    upcoming = [r for r in races if not r["completed"]]
    if upcoming:
        return upcoming[0]["round"]
    completed = [r for r in races if r["completed"]]
    if completed:
        return completed[-1]["round"]
    return races[0]["round"] if races else 1


# -------------------------------------------------------------------- routes --
@app.get("/")
def index() -> Response:
    return send_from_directory(str(DASH), "index.html")


@app.get("/api/health")
def health() -> Response:
    return jsonify({
        "status": "ok",
        "prior": MANAGER.prior_state,
        "has_prediction": LATEST is not None,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "queue": MANAGER._q.qsize(),
    })


@app.get("/api/races")
def races() -> Response:
    season = request.args.get("season", type=int) or date.today().year
    try:
        listing = _schedule(season)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"could not load {season} schedule: {exc}"}), 502
    return jsonify({
        "season": season,
        "races": listing,
        "default_round": _default_round(listing),
        "default_sims": DEFAULT_SIMS,
    })


@app.get("/api/prediction")
def prediction() -> Response:
    with LATEST_LOCK:
        payload = LATEST
    if payload is None:
        # fall back to the last result committed to disk, so a fresh instance
        # still shows something while the first job runs
        disk = config.OUTPUT_DIR / "prediction.json"
        if disk.exists():
            try:
                payload = json.loads(disk.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                payload = None
    if payload is None:
        return jsonify({"error": "no prediction yet", "hint": "POST /api/predict"}), 404
    return Response(json.dumps(payload, ensure_ascii=False), mimetype="application/json")


@app.post("/api/predict")
def predict() -> Response:
    body = request.get_json(silent=True) or {}

    season = int(body.get("season") or date.today().year)
    sims = int(body.get("sims") or DEFAULT_SIMS)
    sims = max(1000, min(MAX_SIMS, sims))

    rnd = body.get("round")
    if rnd in (None, "", "auto"):
        try:
            rnd = _default_round(_schedule(season))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"could not resolve round: {exc}"}), 502
    rnd = int(rnd)

    try:
        job = MANAGER.submit({"season": season, "round": rnd, "sims": sims})
    except RuntimeError:
        return jsonify({"error": "server busy, try again in a moment"}), 429

    return jsonify(job.public(include_result=False)), 202


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str) -> Response:
    job = MANAGER.get(job_id)
    if job is None:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job.public())


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(str(DASH), "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
