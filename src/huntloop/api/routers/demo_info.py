"""
GET /demo-info - only mounted when DEMO_MODE is on (see
huntloop.demo_mode, huntloop.api.main). Tells the frontend banner what
snapshot date the demo data is from, so a stranger looking at the live
demo understands it is a fixed sample, not a live scrape.

Reads the single row scripts/build_demo_dataset.py writes to demo_meta.
If that table is empty (demo mode turned on against a database nobody
ran the builder against), snapshot_date comes back null rather than
erroring - the frontend banner is written to handle that.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from huntloop.api.dependencies import get_db
from huntloop.api.schemas.demo import DemoInfoResponse
from huntloop.db_models import DemoMeta

router = APIRouter(tags=["demo"])

# Kept accurate against what huntloop.api.main.create_app() actually
# does in demo mode, not a blanket "AI is off": the only things really
# unavailable are resume upload/activation (resumes.unsafe_router is
# never mounted), BYOK answer drafting (drafting.router, likewise), and
# saved tracker changes (PATCH /jobs/{id}/application is a no-op that
# writes nothing). The Resume Deep-Dive IS mounted here and runs for
# real - its LLM keys live in Modal's own secret, never in this API's
# environment - as do match scores, matched/missing skills and the
# precomputed ATS report.
_MESSAGE = (
    "This is a live demo running on a small sample of real job postings "
    "and sponsorship data. Resume upload and bring-your-own-key answer "
    "drafting are turned off, and tracker changes are not saved. The "
    "Resume Deep-Dive works here."
)


@router.get("/demo-info", response_model=DemoInfoResponse)
def get_demo_info(db: Session = Depends(get_db)) -> DemoInfoResponse:
    meta = db.query(DemoMeta).order_by(DemoMeta.id.desc()).first()
    return DemoInfoResponse(
        snapshot_date=meta.snapshot_date if meta is not None else None,
        message=_MESSAGE,
    )
