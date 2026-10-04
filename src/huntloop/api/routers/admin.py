"""
GET /admin/whoami - a trivial "am I an admin" check, used by the
frontend nav (frontend/src/components/NavBar.tsx) to decide whether to
show a link to /admin/feedback at all.

Reuses huntloop.api.admin_auth.require_admin completely unchanged - the
exact same two-layer check (valid Clerk session + email allowlist)
every other /admin/* route already enforces. This exists specifically
so the frontend never needs its own copy of ADMIN_ALLOWED_EMAILS (which
would mean duplicating a config value across two services that could
silently drift) - it asks the backend "would you let me in?" instead of
re-implementing the allowlist check client-side. Not itself sensitive:
on success it only echoes the caller's own email back to them; on
failure it's the same 401/403 any other /admin/* route already gives an
unauthorized caller.

A separate module/router from huntloop.api.routers.admin_feedback
(prefix "/admin/feedback") rather than one more route nested under it -
this is general /admin/* UI-gating, not feedback-specific, so it gets
its own top-level /admin/whoami path.
"""
from fastapi import APIRouter, Depends

from huntloop.api.admin_auth import AdminUser, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/whoami")
def whoami(admin: AdminUser = Depends(require_admin)) -> dict[str, str]:
    return {"email": admin.email}
