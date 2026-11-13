from __future__ import annotations

import asyncio
import json
import logging

from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Briefing, PushSubscription

logger = logging.getLogger("classbrief.push")


async def send_briefing_push(db: Session, briefing: Briefing, settings: Settings, new_materials: int = 0) -> int:
    if not settings.vapid_private_key or not settings.vapid_public_key:
        return 0
    payload = json.dumps({
        "title": f"{briefing.subject.name} in {briefing.subject.preparation_minutes} Minuten",
        "body": f"{new_materials} neue Materialien wurden ausgewertet." if new_materials else "Dein Unterrichtsbriefing ist fertig.",
        "url": f"/briefings/{briefing.id}",
    }, ensure_ascii=False)
    sent = 0
    for subscription in list(db.scalars(select(PushSubscription))):
        info = {"endpoint": subscription.endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}}
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=info,
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": f"mailto:noreply@{settings.app_base_url.split('://')[-1].split('/')[0]}"},
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {404, 410}:
                db.delete(subscription)
                db.commit()
            logger.warning("push.failed")
    return sent
