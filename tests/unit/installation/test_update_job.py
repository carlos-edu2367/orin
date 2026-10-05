from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from agentos.installation import versions
from agentos.installation.profile import RuntimeProfile
from agentos.installation.update_job import UpdateJob
from agentos.installation.updater import Updater

from tests.unit.installation.test_updater import BASE, Server, _archive, _installed

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake runtimes are POSIX shell scripts")


def _job(tmp_path: Path, server: Server, monkeypatch: pytest.MonkeyPatch) -> tuple[UpdateJob, Path]:
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    root = _installed(tmp_path)
    profile = RuntimeProfile("installed", root / "0.4.0" / "resources" / "runtime", "0.4.0", None)

    def factory(**kwargs):
        return Updater(base_url=BASE, platform="linux-x64", opener=server, sleep=lambda _: None, **kwargs)

    return UpdateJob(profile, updater_factory=factory), root


def _wait(job: UpdateJob) -> dict:
    deadline = time.time() + 10
    while job.status()["state"] in {"checking", "downloading", "verifying", "extracting", "validating"} and time.time() < deadline:
        time.sleep(0.02)
    return job.status()


def test_job_downloads_in_the_background_and_ends_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, root = _job(tmp_path, Server("0.5.0", _archive("0.5.0"), notes="novidades"), monkeypatch)
    assert job.status()["state"] == "idle"

    job.start()
    final = _wait(job)

    assert (final["state"], final["version"], final["notes"]) == ("ready", "0.5.0", "novidades")
    assert (root / "current").resolve() == (root / "0.4.0").resolve()


def test_job_reports_up_to_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, _ = _job(tmp_path, Server("0.4.0", _archive("0.4.0")), monkeypatch)
    job.start()
    assert _wait(job)["state"] in {"up_to_date", "idle"}


def test_job_failure_carries_the_message_and_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, _ = _job(tmp_path, Server("0.5.0", _archive("0.5.0"), sha="0" * 64), monkeypatch)
    job.start()
    final = _wait(job)

    assert final["state"] == "failed"
    assert "assinatura" in final["error"]["message"] and final["error"]["hint"]


def test_a_ready_update_survives_a_new_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job, root = _job(tmp_path, Server("0.5.0", _archive("0.5.0")), monkeypatch)
    job.start()
    _wait(job)

    fresh = UpdateJob(job._profile, updater_factory=job._factory)

    assert fresh.status()["state"] == "ready" and fresh.status()["version"] == "0.5.0"


def test_job_is_unsupported_outside_a_packaged_install() -> None:
    job = UpdateJob(RuntimeProfile("development", Path("/repo"), "0.4.0", Path("/repo")))
    assert job.status()["state"] == "unsupported" and job.start()["state"] == "unsupported"
