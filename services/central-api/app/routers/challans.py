"""E-challan for a confirmed incident: the operator types the plate they read on the evidence, the
owner is looked up, the challan is issued and the SMS sent (simulated until a gateway is configured).

    POST /api/v1/incidents/{id}/challan/lookup   {plate}  owner (masked mobile), offence, fine, SMS text
    POST /api/v1/incidents/{id}/challan          {plate}  issue it: challan row, SMS, incident CONFIRMED
    GET  /api/v1/challans                                  challans issued, newest first

Traffic enforcement only (CHALLAN_ISSUE; never a Municipal Corporation account), within the cameras
the account may read. The plate is what a person read, not what the system guessed. Every owner
lookup and every challan is audited; the full mobile number never leaves this service.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import MUNICIPAL_DEPARTMENT, DemoUser, Permission
from ..database import get_db
from ..dependencies import client_ip, require_permission
from ..models import Camera as CameraRow
from ..models import Challan
from ..models import Incident as IncidentRow
from ..services import audit_service, challan_service, plate_matching
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.policy_service import may_read_detections

router = APIRouter(prefix="/api/v1", tags=["challan"])


class ChallanRequest(BaseModel):
    plate: str = Field(min_length=4, max_length=24)


async def _context(incident_id: str, body: ChallanRequest, user: DemoUser, db: AsyncSession):
    if user.department == MUNICIPAL_DEPARTMENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Challans are issued by Traffic Police")
    inc = (await db.execute(select(IncidentRow).where(IncidentRow.incident_id == incident_id))).scalar_one_or_none()
    if inc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown incident")
    offence = challan_service.OFFENCES.get(inc.kind)
    if offence is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{inc.kind} is not a challan offence")
    camera = (await db.execute(select(CameraRow).where(CameraRow.camera_id == inc.camera_id))).scalar_one_or_none()
    if camera is None or not may_read_detections(user, camera):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Out of scope")
    plate = plate_matching.clean(body.plate)
    if not plate_matching.is_plausible(plate):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a valid Indian registration number")
    return inc, offence, camera, plate


def _view(plate, owner, offence, text, camera) -> dict:
    return {"plate": plate, "owner_name": owner.owner_name, "mobile_masked": challan_service.mask(owner.mobile),
            "owner_source": challan_service.OWNER_SOURCE, "demo": owner.is_demo,
            "offence": offence["label"], "section": offence["section"], "fine_rupees": offence["fine"],
            "fine_note": offence["note"], "verify": challan_service.VERIFY, "sms_text": text,
            "sms_provider": challan_service.SMS_PROVIDER, "camera": camera.name}


@router.post("/incidents/{incident_id}/challan/lookup", summary="Owner and challan preview for a plate")
async def lookup(incident_id: str, body: ChallanRequest, request: Request,
                 user: DemoUser = Depends(require_permission(Permission.CHALLAN_ISSUE)),
                 db: AsyncSession = Depends(get_db)) -> dict:
    inc, offence, camera, plate = await _context(incident_id, body, user, db)
    owner = await challan_service.owner_of(db, plate)
    await db.commit()
    text = challan_service.sms_text("(on issue)", plate, offence, camera.name, inc.first_seen_utc, camera.owning_department)
    await audit_service.record(db, username=user.username, role=user.role, action=AuditAction.CHALLAN_OWNER_LOOKUP,
                               outcome=AuditOutcome.SUCCESS, resource_type=ResourceType.CHALLAN, resource_id=plate,
                               department=camera.owning_department, client_ip=client_ip(request),
                               details={"incident_id": incident_id, "source": challan_service.OWNER_SOURCE})
    return _view(plate, owner, offence, text, camera)


@router.post("/incidents/{incident_id}/challan", status_code=status.HTTP_201_CREATED, summary="Issue the e-challan")
async def issue(incident_id: str, body: ChallanRequest, request: Request,
                user: DemoUser = Depends(require_permission(Permission.CHALLAN_ISSUE)),
                db: AsyncSession = Depends(get_db)) -> dict:
    inc, offence, camera, plate = await _context(incident_id, body, user, db)
    existing = (await db.execute(select(Challan).where(Challan.incident_id == incident_id))).scalars().first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Challan {existing.challan_no} already issued for this incident")
    owner = await challan_service.owner_of(db, plate)
    now = datetime.now(timezone.utc)
    number = await challan_service.next_challan_no(db, now)
    text = challan_service.sms_text(number, plate, offence, camera.name, inc.first_seen_utc, camera.owning_department)
    sms = challan_service.send_sms(owner.mobile, text)
    db.add(Challan(challan_no=number, incident_id=incident_id, camera_id=inc.camera_id, plate_normalised=plate,
                   offence=inc.kind, section=offence["section"], fine_rupees=offence["fine"], owner_name=owner.owner_name,
                   mobile_masked=challan_service.mask(owner.mobile), owner_source=challan_service.OWNER_SOURCE,
                   sms_text=text, sms_status=sms, sms_provider=challan_service.SMS_PROVIDER, issued_by=user.username,
                   issued_at=now))
    inc.status, inc.reviewed_by, inc.reviewed_at = "CONFIRMED", user.username, now
    inc.review_note = f"Challan {number} to {plate}"
    await db.commit()
    await audit_service.record(db, username=user.username, role=user.role, action=AuditAction.CHALLAN_ISSUED,
                               outcome=AuditOutcome.SUCCESS, resource_type=ResourceType.CHALLAN, resource_id=number,
                               department=camera.owning_department, client_ip=client_ip(request),
                               details={"incident_id": incident_id, "plate": plate, "offence": inc.kind,
                                        "fine": offence["fine"], "sms": sms})
    return {"challan_no": number, "sms_status": sms, "issued_at": now, **_view(plate, owner, offence, text, camera)}


@router.get("/challans", summary="Challans issued")
async def list_challans(user: DemoUser = Depends(require_permission(Permission.CHALLAN_ISSUE)),
                        db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(Challan).order_by(Challan.issued_at.desc()).limit(200))).scalars().all()
    out = []
    for r in rows:
        camera = (await db.execute(select(CameraRow).where(CameraRow.camera_id == r.camera_id))).scalar_one_or_none()
        if camera is None or not may_read_detections(user, camera):
            continue
        out.append({"challan_no": r.challan_no, "incident_id": r.incident_id, "camera": camera.name,
                    "plate": r.plate_normalised, "offence": r.offence, "section": r.section, "fine_rupees": r.fine_rupees,
                    "owner_name": r.owner_name, "mobile_masked": r.mobile_masked, "sms_status": r.sms_status,
                    "issued_by": r.issued_by, "issued_at": r.issued_at})
    return out
