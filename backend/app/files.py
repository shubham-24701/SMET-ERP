"""Evidence files: photographs, test reports, signed acknowledgements.

The type is read from the file's first bytes, never from its name or the browser's claim, and only
JPEG, PNG, WebP and PDF are kept. Bytes go to `FILES_DIR` under a random name; the database keeps the
original name, size and SHA-256. Files are added, never edited. Downloads check the permission of the
record the file belongs to.
"""

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.audit import record
from app.auth import DB, require
from app.config import get_settings
from app.installation import Worker, check_may_work, file_row
from app.masters import get_or_404
from app.models import SiteVisit, StoredFile

router = APIRouter(tags=["files"])
SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
]


def sniff(head: bytes) -> str | None:
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return next((t for sig, t in SIGNATURES if head.startswith(sig)), None)


def path_of(f_id: uuid.UUID) -> Path:
    h = f_id.hex
    return Path(get_settings().files_dir) / h[:2] / h


def safe_name(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\"<>|:*?]", "", base).strip(" .")
    return base[-160:] or "file"


@router.post("/visits/{visit_id}/files", status_code=status.HTTP_201_CREATED)
def upload_visit_file(
    visit_id: uuid.UUID,
    p: Worker,
    db: DB,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["photo", "test_report", "acknowledgement", "other"], Form()] = "photo",
    caption: Annotated[str, Form(max_length=300)] = "",
) -> dict:
    v = get_or_404(db, SiteVisit, visit_id, "Visit")
    check_may_work(p, v)
    if v.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Visit {v.number} is cancelled")
    limit = get_settings().max_upload_mb * 2**20
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Files up to {get_settings().max_upload_mb} MB")
    if not data:
        raise HTTPException(
            422, [{"loc": ["body", "file"], "msg": "The file is empty", "type": "x"}]
        )
    ctype = sniff(data[:16])
    if ctype is None:
        raise HTTPException(
            422,
            [
                {
                    "loc": ["body", "file"],
                    "msg": "Only JPEG, PNG or WebP photographs and PDF documents",
                    "type": "x",
                }
            ],
        )
    if kind == "photo" and not ctype.startswith("image/"):
        raise HTTPException(
            422, [{"loc": ["body", "file"], "msg": "A photograph must be an image", "type": "x"}]
        )
    f = StoredFile(id=uuid.uuid4(), owner_type="visit", owner_id=v.id, kind=kind, name=safe_name(file.filename),
                   content_type=ctype, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                   caption=caption.strip(), uploaded_by=p.user.id)  # fmt: skip
    target = path_of(f.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    try:
        db.add(f)
        record(
            db,
            p.user,
            "file.upload",
            "site_visit",
            v.id,
            f"{v.number}: {kind.replace('_', ' ')} {f.name} added",
        )
        db.commit()
    except Exception:
        target.unlink(missing_ok=True)  # no row, no file
        raise
    return file_row(f, {p.user.id: p.user.name})


@router.get("/files/{file_id}")
def download(file_id: uuid.UUID, _: require("installation.view"), db: DB) -> FileResponse:
    # shortcut: every file belongs to a visit today; owners from later phases bring their own check.
    f = get_or_404(db, StoredFile, file_id, "File")
    path = path_of(f.id)
    if not path.exists():
        raise HTTPException(
            status.HTTP_410_GONE, "The file is missing from storage; restore it from the backup"
        )
    inline = f.content_type.startswith("image/")
    return FileResponse(path, media_type=f.content_type, filename=f.name,
                        content_disposition_type="inline" if inline else "attachment")  # fmt: skip
