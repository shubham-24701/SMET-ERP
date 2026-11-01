"""Evidence files: photographs, test reports, signed acknowledgements.

The type is read from the file's first bytes, never from its name or the browser's claim, and only
JPEG, PNG, WebP and PDF are kept. If a virus scanner is set (MALWARE_SCAN_COMMAND) every file passes
it first; a file it refuses, or any scanner error, means no upload (fail closed). Bytes are stored
under a random name, in `FILES_DIR` or an S3-compatible bucket (FILES_BACKEND=s3); the database keeps
the original name, size, SHA-256 and scan result. Files are added, never edited, and kept with their
record (no automatic deletion: they are business evidence). Downloads check the permission of the
record the file belongs to.
"""

import hashlib
import os
import re
import shlex
import subprocess  # noqa: S404 - the configured scanner, fixed arguments, no shell
import uuid
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response

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


class LocalStore:
    """Files on this computer's disk, outside the code folder."""

    name = "local"

    def put(self, f_id: uuid.UUID, data: bytes) -> None:
        target = path_of(f_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    def get(self, f_id: uuid.UUID) -> bytes | None:
        p = path_of(f_id)
        return p.read_bytes() if p.exists() else None

    def delete(self, f_id: uuid.UUID) -> None:
        path_of(f_id).unlink(missing_ok=True)

    def check(self) -> str:
        root = Path(get_settings().files_dir)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".health"
        probe.write_bytes(b"ok")
        probe.unlink()
        free = __import__("shutil").disk_usage(root).free // 2**30
        return f"Writable, {free} GB free"


class S3Store:
    """An S3-compatible bucket (AWS S3, Cloudflare R2, MinIO...). Needs `uv add boto3` when switched on;
    tested against a stand-in client only (docs/INTEGRATION_REGISTER.md)."""

    name = "s3"

    def __init__(self, client=None):
        s = get_settings()
        if client is None:
            import boto3  # noqa: PLC0415 - optional: only when FILES_BACKEND=s3

            client = boto3.client("s3", endpoint_url=s.s3_endpoint_url or None, region_name=s.s3_region or None,
                                  aws_access_key_id=s.s3_access_key_id or None, aws_secret_access_key=s.s3_secret_access_key or None)  # fmt: skip
        self.c, self.bucket = client, s.s3_bucket

    def put(self, f_id: uuid.UUID, data: bytes) -> None:
        self.c.put_object(
            Bucket=self.bucket, Key=f"files/{f_id.hex}", Body=data, ServerSideEncryption="AES256"
        )

    def get(self, f_id: uuid.UUID) -> bytes | None:
        try:
            return self.c.get_object(Bucket=self.bucket, Key=f"files/{f_id.hex}")["Body"].read()
        except Exception as e:  # botocore NoSuchKey; any other error surfaces as missing
            if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e):
                return None
            raise

    def delete(self, f_id: uuid.UUID) -> None:
        self.c.delete_object(Bucket=self.bucket, Key=f"files/{f_id.hex}")

    def check(self) -> str:
        self.c.head_bucket(Bucket=self.bucket)
        return f"Bucket {self.bucket} reachable"


def storage():
    return S3Store() if get_settings().files_backend == "s3" else LocalStore()


def scan(data: bytes) -> str:
    """'clean', or 'not scanned' when no scanner is set; refuses the upload otherwise."""
    cmd = get_settings().malware_scan_command
    if not cmd:
        return "not scanned"
    try:
        r = subprocess.run(  # noqa: S603 - the scanner the server owner configured
            shlex.split(cmd), input=data, capture_output=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise HTTPException(503, "The virus scanner isn't answering; try again later") from e
    if r.returncode == 0:
        return "clean"
    if r.returncode == 1:  # ClamAV and most scanners: 1 = found something
        raise HTTPException(
            422,
            [{"loc": ["body", "file"], "msg": "The virus scanner refused this file", "type": "x"}],
        )
    raise HTTPException(503, "The virus scanner failed; the file was not stored")


def safe_name(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = re.sub(r"[\x00-\x1f\"<>|:*?]", "", base).strip(" .")
    return base[-160:] or "file"


def read_checked(file: UploadFile, kind: str) -> tuple[bytes, str]:
    """The file's bytes and real type, or a 413/422 that says what is wrong with it."""
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
    if kind in ("photo", "installation_photo") and not ctype.startswith("image/"):
        raise HTTPException(
            422, [{"loc": ["body", "file"], "msg": "A photograph must be an image", "type": "x"}]
        )
    return data, ctype


def store(
    db,
    user,
    owner_type: str,
    owner_id,
    file: UploadFile,
    kind: str,
    caption: str,
    entity: tuple[str, object, str],
) -> StoredFile:
    """Checks, writes and records one file in the caller's transaction, then commits; if the commit
    fails the bytes are removed again (no row, no file). `entity` = (audit type, id, label)."""
    data, ctype = read_checked(file, kind)
    scanned = scan(data)
    f = StoredFile(id=uuid.uuid4(), scan=scanned, owner_type=owner_type, owner_id=owner_id, kind=kind, name=safe_name(file.filename),
                   content_type=ctype, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                   caption=caption.strip(), uploaded_by=user.id if user else None)  # fmt: skip
    store_ = storage()
    store_.put(f.id, data)
    try:
        db.add(f)
        record(
            db,
            user,
            "file.upload",
            entity[0],
            entity[1],
            f"{entity[2]}: {kind.replace('_', ' ')} {f.name} added",
        )
        db.commit()
    except Exception:
        store_.delete(f.id)
        raise
    return f


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
    f = store(db, p.user, "visit", v.id, file, kind, caption, ("site_visit", v.id, v.number))
    return file_row(f, {p.user.id: p.user.name})


# Who may open a file: the permission that shows the record it belongs to.
OWNER_PERM = {"visit": "installation.view", "warranty": "warranty.view", "service": "service.view"}


@router.get("/files/{file_id}")
def download(file_id: uuid.UUID, p: require(), db: DB) -> FileResponse:
    if not any(p.can(x) for x in OWNER_PERM.values()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission for this")
    f = get_or_404(db, StoredFile, file_id, "File")
    if not p.can(OWNER_PERM[f.owner_type]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission for this")
    inline = f.content_type.startswith("image/")
    s = storage()
    if isinstance(s, LocalStore):
        path = path_of(f.id)
        if not path.exists():
            raise HTTPException(
                status.HTTP_410_GONE, "The file is missing from storage; restore it from the backup"
            )
        return FileResponse(path, media_type=f.content_type, filename=f.name,
                            content_disposition_type="inline" if inline else "attachment")  # fmt: skip
    data = s.get(f.id)
    if data is None:
        raise HTTPException(
            status.HTTP_410_GONE, "The file is missing from storage; restore it from the backup"
        )
    disp = "inline" if inline else "attachment"
    return Response(data, media_type=f.content_type, headers={"Content-Disposition": f"{disp}; filename*=UTF-8''{quote(f.name)}",
                                                              "X-Content-Type-Options": "nosniff"})  # fmt: skip
