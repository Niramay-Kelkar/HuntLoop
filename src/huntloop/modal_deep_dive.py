"""
Modal dispatch + reconciliation for the Resume Deep-Dive - the
spawn-and-poll half of the feature. The analysis itself lives in
huntloop.deep_dive (and runs inside the Modal container defined in
huntloop.modal_resume_functions.deep_dive_remote); the row lifecycle
lives in huntloop.api.routers.deep_dive. This module is only the bridge
between them.

A separate module from huntloop.modal_resume_processing, which it reuses
rather than duplicates: ModalUnavailable, _modal_configured() and
_prepare_invocation() (function lookup, then the row-locked
modal_usage budget reservation) all come from there unchanged. The split
is by concern, not by convenience - that module is the resume
parse/embed path with its local in-process fallback, and this one has
NO fallback by design (see below), so folding them together would mean
one module with two contradictory failure contracts.

WHY SPAWN RATHER THAN CALL. Every other Modal call in this project is
synchronous: .remote() / .remote.aio(), inside the request, result in
hand before the response. A Deep-Dive runs a three-provider LLM chain
and can take minutes, which no HTTP request should hold open, so this
one uses Modal's .spawn() - fire the call, get back a call id
immediately, return 202, and let the frontend poll. This is the first
poll-by-id flow anywhere in this codebase; there was no existing
submitter-facing poll endpoint to copy from.

WHY THE RESULT IS COPIED INTO OUR OWN DATABASE. A Modal FunctionCall's
output is NOT durable storage - it expires, and reading an expired one
raises modal.exception.OutputExpiredError. So resume_deep_dives.
modal_call_id is purely a handle, valid for the window in which the
call is still resolvable; the moment a poll sees a real result, that
result is validated and written into resume_deep_dives.result, after
which the row stands on its own forever and the call id is of
historical interest only.

NO FALLBACK, IN ANY ENVIRONMENT. huntloop.modal_resume_processing falls
back to running the same work in-process whenever Modal is unusable.
This feature deliberately does not, and that is not an oversight: the
whole reason the Deep-Dive runs on Modal is that the LLM provider keys
live ONLY in the `huntloop-llm` Modal Secret, never in the API's own
environment (the public demo API has no LLM keys at all - see
render.yaml). There is therefore no local path that could even make the
call. Modal being unconfigured, undeployed, unreachable, or over budget
is surfaced as a structured 503, never as a silent degradation.

Budget: a Deep-Dive reserves MODAL_DEEP_DIVE_WEIGHT (default 5) slots
from the shared modal_usage ledger instead of 1 - see
huntloop.modal_resume_processing._reserve_invocation_slot's own
docstring for the weighting semantics (refuse rather than overshoot the
cap; alert thresholds stay correct). Reserved once per DISPATCH, which
is also why deep_dive_remote sets retries=0: a silent Modal-side retry
would spend compute against a slot nobody reserved.

Nothing here logs resume text, job text, a provider response, or any
key/token value - only row ids, call ids, and exception types.
"""
import logging
import os

import modal

from huntloop.modal_resume_functions import DEEP_DIVE_FUNCTION, MODAL_APP_NAME
from huntloop.modal_resume_processing import ModalUnavailable, _prepare_invocation

logger = logging.getLogger(__name__)

# How many modal_usage ledger slots one Deep-Dive reserves. An
# APPROXIMATION of its heavier credit use relative to a resume
# parse/embed call (seconds of CPU) - a Deep-Dive is a multi-provider
# LLM chain with a 10-minute timeout - not a measured per-call cost;
# Modal reports no per-call cost figure to reconcile a ledger against
# (same situation the Tavily ledger is in). A plain env var so it can be
# retuned once real per-invocation cost is observed, without a code
# change.
DEFAULT_DEEP_DIVE_WEIGHT = 5

# How long a `running` row may sit before a new POST is allowed to
# dispatch again rather than returning 202 forever. Covers the real case
# where a spawn succeeded but the call never resolves (a Modal-side
# failure that leaves no retrievable output, a container killed at its
# timeout). Comfortably longer than deep_dive_remote's own 600s timeout.
DEFAULT_STALE_MINUTES = 10


def deep_dive_weight() -> int:
    try:
        weight = int(os.getenv("MODAL_DEEP_DIVE_WEIGHT", str(DEFAULT_DEEP_DIVE_WEIGHT)))
    except ValueError:
        logger.warning(
            "MODAL_DEEP_DIVE_WEIGHT is not an integer - falling back to %s",
            DEFAULT_DEEP_DIVE_WEIGHT,
        )
        return DEFAULT_DEEP_DIVE_WEIGHT
    return max(1, weight)


def stale_minutes() -> float:
    try:
        return max(0.0, float(os.getenv("DEEP_DIVE_STALE_MINUTES", str(DEFAULT_STALE_MINUTES))))
    except ValueError:
        logger.warning(
            "DEEP_DIVE_STALE_MINUTES is not a number - falling back to %s", DEFAULT_STALE_MINUTES
        )
        return float(DEFAULT_STALE_MINUTES)


def spawn_deep_dive(db, payload: dict) -> str:
    """Reserves budget and spawns one deep_dive_remote call. Returns the
    Modal call id.

    Order matters and matches _prepare_invocation's existing contract:
    look the function up FIRST (a lookup failure means nothing was ever
    dispatched, so it must not be charged), then reserve the weighted
    slot under the row lock, then spawn. A spawn that fails after the
    reservation stays charged - real compute may already have been
    started, and under-counting is the worse error.

    Raises ModalUnavailable with reason set to not_configured /
    lookup_failed / budget_exhausted / invocation_error. There is no
    fallback - see the module docstring."""
    fn = _prepare_invocation(db, DEEP_DIVE_FUNCTION, weight=deep_dive_weight())
    try:
        call = fn.spawn(payload)
    except Exception as e:
        raise ModalUnavailable(
            f"Spawning the Modal Deep-Dive function failed ({type(e).__name__}): {e}",
            reason="invocation_error",
        ) from e

    call_id = getattr(call, "object_id", None)
    if not call_id:
        raise ModalUnavailable(
            "Modal accepted the Deep-Dive spawn but returned no call id to poll",
            reason="invocation_error",
        )
    logger.info("Deep-Dive spawned on Modal (call id %s)", call_id)
    return str(call_id)


def reconcile_deep_dive(call_id: str) -> tuple[str, dict | None, str | None]:
    """Non-blocking check on a spawned Deep-Dive call. Returns
    (status, result, error) where status is one of "running" / "done" /
    "failed":

      * "running"  - the call hasn't finished; result and error are both
                     None.
      * "done"     - a result came back AND passed
                     huntloop.deep_dive.validate_deep_dive_result().
                     That re-validation is deliberate: this process did
                     not produce that JSON, so a successful Modal call
                     is not on its own a reason to trust its shape.
      * "failed"   - the output has expired
                     (modal.exception.OutputExpiredError), the remote
                     function raised (e.g.
                     AllDeepDiveProvidersFailed, surfaced here as the
                     real exception), or the payload came back in an
                     unusable shape. `error` is a SHORT SANITIZED
                     message - the exception type plus a fixed human
                     sentence, never the raw exception text, which for a
                     provider error can contain echoed prompt content
                     (and therefore resume text).

    Never raises; every failure mode is reported through the return
    value so the caller's row always reaches a definite state."""
    from huntloop.deep_dive import validate_deep_dive_result

    try:
        output = modal.FunctionCall.from_id(call_id).get(timeout=0)
    # ORDER AND TYPES HERE ARE BOTH LOAD-BEARING - verified directly
    # against the installed modal client (1.6.1), not assumed:
    #   1. An unfinished call raises modal.exception.TimeoutError, which
    #      is NOT a subclass of the builtin TimeoutError (its MRO is
    #      modal.exception.Error -> Exception). A bare
    #      `except TimeoutError` would therefore never fire, and every
    #      still-running poll would fall through to the generic handler
    #      below and be recorded as a permanent failure. Both are caught
    #      anyway, in case a future client version raises the builtin.
    #   2. modal.exception.OutputExpiredError IS a subclass of
    #      modal.exception.TimeoutError, so it must be caught FIRST or
    #      an expired output would be misread as "still running" and
    #      polled forever.
    except modal.exception.OutputExpiredError:
        logger.warning("Deep-Dive call %s expired on Modal before it was polled", call_id)
        return (
            "failed",
            None,
            "The analysis result expired on Modal before it could be saved. Please run it again.",
        )
    except (modal.exception.TimeoutError, TimeoutError):
        return "running", None, None
    except Exception as e:
        # Includes huntloop.deep_dive.AllDeepDiveProvidersFailed, raised
        # inside the remote function and re-raised here by the Modal
        # client. Type name only - see the docstring.
        logger.warning(
            "Deep-Dive call %s failed on Modal (%s)", call_id, type(e).__name__
        )
        return (
            "failed",
            None,
            f"The analysis did not complete ({type(e).__name__}). Please run it again.",
        )

    result = validate_deep_dive_result(output)
    if result is None:
        logger.warning("Deep-Dive call %s returned a result that failed shape validation", call_id)
        return (
            "failed",
            None,
            "The analysis finished but returned an unexpected result shape. Please run it again.",
        )
    return "done", result, None
