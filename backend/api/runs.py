"""PATCH /runs/{run_id}/rating - human feedback + calibration flagging."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()
CALIBRATION_GAP = 0.3


class RatingBody(BaseModel):
    rating: int = Field(ge=1, le=5)


@router.patch("/runs/{run_id}/rating")
async def rate_run(run_id: UUID, body: RatingBody, request: Request):
    db = request.app.state.db        # ADAPT: asyncpg pool
    row = await db.fetchrow(
        "UPDATE evaluations SET user_rating = $2 WHERE run_id = $1 RETURNING overall_score",
        run_id, body.rating)
    if row is None:
        raise HTTPException(404, "no evaluation for this run yet")
    gap = abs(row["overall_score"] - body.rating / 5)
    flagged = gap > CALIBRATION_GAP
    if flagged:
        await db.execute(
            """UPDATE evaluations
               SET metadata = COALESCE(metadata, '{}'::jsonb) || '{"calibration_needed": true}'::jsonb
               WHERE run_id = $1""", run_id)
    return {"run_id": str(run_id), "user_rating": body.rating,
            "automated_overall": row["overall_score"], "gap": round(gap, 3),
            "calibration_needed": flagged}
