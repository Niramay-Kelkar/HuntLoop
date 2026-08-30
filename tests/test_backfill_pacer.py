"""
Unit test for backfill_skills_matching.TokenPacer's request-rate (RPM)
bound - added in Step K because Gemini's small batches would otherwise
let the loop run past its 15 RPM cap (the TPM bound alone doesn't hold
it). Time is monkeypatched so the test doesn't actually sleep.
"""
import os
import sys

os.environ.setdefault("GROQ_API_KEY", "test-dummy-key")
os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import backfill_skills_matching as bf  # noqa: E402


def test_rpm_bound_forces_a_sleep_even_when_tokens_are_cheap(monkeypatch):
    clock = [0.0]
    sleeps = []
    monkeypatch.setattr(bf.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bf.time, "sleep", lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)))

    # Tiny token cost, generous TPM, but only 3 requests/min allowed.
    pacer = bf.TokenPacer(target_tpm=1_000_000, max_rpm=3)
    for _ in range(3):
        pacer.wait_for_budget(10)          # 3 quick requests, no sleep
    assert sleeps == []

    pacer.wait_for_budget(10)              # 4th within the same minute -> must sleep
    assert len(sleeps) == 1 and sleeps[0] > 0
    assert clock[0] >= 60                  # slept until the window rolled


def test_tpm_bound_still_applies(monkeypatch):
    clock = [0.0]
    sleeps = []
    monkeypatch.setattr(bf.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bf.time, "sleep", lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)))

    pacer = bf.TokenPacer(target_tpm=6_000, max_rpm=100)
    pacer.wait_for_budget(6_000)           # fills the token budget in one go
    pacer.wait_for_budget(6_000)           # next one must wait for the window
    assert len(sleeps) == 1 and sleeps[0] > 0
