from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.scheduler import run_due_briefings

router = APIRouter(prefix="/internal")


@router.post("/tick")
async def tick(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    settings = get_settings()
    expected = f"Bearer {settings.cron_secret}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Unauthorized")
    return await run_due_briefings(db, settings)
