"""POST /api/upload-run: run the frozen engine on an uploaded data set (zip) and hand back the JSON.

For a judge data set that arrives after the code freeze: nothing here changes the engine. The zip is
unpacked into ``out/uploads/<run id>/`` (gitignored), then the repository's own CLI runs on it
exactly as from a terminal (``python -m prism run`` then ``python -m prism validate``). The body is
the raw zip bytes (no multipart dependency). One run at a time.

Zip layout: any folder named ``shared_folder`` (one subfolder per deal) and an ``arrivals.jsonl``
anywhere in the archive. ``history/``, ``contacts/`` and ``rules/`` are read from the shared folder's
parent when present there, otherwise from the sample data root.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
UPLOADS = REPO_ROOT / "out" / "uploads"
SAMPLE_ROOT = REPO_ROOT / "data" / "participant"
MAX_BYTES = 300 * 1024 * 1024
MAX_FILES = 20000
RUN_TIMEOUT_S = 900
_lock = threading.Lock()
_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


class UploadDeal(BaseModel):
    folder: str
    legal_name: Optional[str] = None
    final_stage: Optional[str] = None
    held: bool
    flagged: bool
    stage_at_flag: Optional[str] = None
    linked_deals: list[str]
    issues: list[str]


class UploadRunResult(BaseModel):
    run_id: str
    set: str
    ok: bool
    valid: bool
    seconds: float
    n_deals: int
    n_held: int
    n_flagged: int
    history_flags: list[str]
    deals: list[UploadDeal]
    validate_errors: int
    validate_warnings: int
    log_tail: str


def _safe_extract(data: bytes, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="the upload is not a zip file") from None
    infos = zf.infolist()
    if len(infos) > MAX_FILES:
        raise HTTPException(status_code=413, detail=f"too many files in the zip (max {MAX_FILES})")
    root = dest.resolve()
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("__MACOSX/") or "/__MACOSX/" in name or name.split("/")[-1].startswith("._"):
            continue
        target = (root / name).resolve()
        # Zip-slip guard: every entry must land inside dest.
        if target != root and root not in target.parents:
            raise HTTPException(status_code=400, detail="the zip contains a path outside its folder")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            out.write(src.read())


def _find(root: Path) -> tuple[Path, Path]:
    folders = sorted(p for p in root.rglob("shared_folder") if p.is_dir())
    arrivals = sorted(p for p in root.rglob("arrivals.jsonl") if p.is_file())
    if not folders:
        raise HTTPException(status_code=422, detail="no shared_folder/ directory found in the zip")
    if not arrivals:
        raise HTTPException(status_code=422, detail="no arrivals.jsonl found in the zip")
    return folders[0], arrivals[0]


def _data_root(folder: Path) -> Path:
    parent = folder.parent
    if (parent / "history").is_dir():
        return parent
    return SAMPLE_ROOT


def _summarise(sub: dict[str, Any]) -> list[UploadDeal]:
    rows: list[UploadDeal] = []
    for folder, d in sorted((sub.get("deals") or {}).items()):
        fraud = d.get("fraud") or {}
        log = d.get("stage_log") or []
        ex = d.get("extracted") or {}
        rows.append(UploadDeal(
            folder=folder,
            legal_name=ex.get("legal_name") if isinstance(ex.get("legal_name"), str) else None,
            final_stage=(log[-1] or {}).get("stage") if log else None,
            held=bool(fraud.get("held")),
            flagged=bool(fraud.get("flagged")),
            stage_at_flag=fraud.get("stage_at_flag"),
            linked_deals=[str(x) for x in (fraud.get("linked_deals") or [])],
            issues=[str(x) for x in (d.get("issues") or [])],
        ))
    return rows


def build_upload_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/upload-run", response_model=UploadRunResult)
    async def upload_run(request: Request, set: str = "hidden") -> Any:  # noqa: A002 (wire name)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", set):
            raise HTTPException(status_code=422, detail="set must be letters, digits, - or _")
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty upload")
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413, detail="zip too large (max 300 MB)")
        if not _lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="a run is already in progress")
        try:
            return _run(data, set)
        finally:
            _lock.release()

    @router.get("/upload-run/{run_id}/submission")
    def download(run_id: str) -> Any:
        if not _RUN_ID.fullmatch(run_id):
            raise HTTPException(status_code=404, detail="unknown run")
        path = UPLOADS / run_id / "submission.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="unknown run")
        return FileResponse(path, media_type="application/json", filename="submission.json")

    return router


def _run(data: bytes, set_name: str) -> UploadRunResult:
    import secrets
    import shutil
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    base = UPLOADS / run_id
    src = base / "input"
    src.mkdir(parents=True, exist_ok=False)
    try:
        _safe_extract(data, src)
        folder, arrivals = _find(src)
    except HTTPException:
        shutil.rmtree(base, ignore_errors=True)
        raise
    out = base / "submission.json"
    env_py = sys.executable
    t0 = time.monotonic()
    run = subprocess.run(
        [env_py, "-m", "prism", "run", "--folder", str(folder), "--arrivals", str(arrivals),
         "--data-root", str(_data_root(folder)), "--set", set_name, "--out", str(out)],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=RUN_TIMEOUT_S, env=_env(),
    )
    log = (run.stdout or "") + (run.stderr or "")
    valid, verrs, vwarns = False, 0, 0
    if run.returncode == 0 and out.is_file():
        val = subprocess.run(
            [env_py, "-m", "prism", "validate", str(out), "--folder", str(folder)],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=_env(),
        )
        vout = val.stdout or ""
        valid = val.returncode == 0 and any(line.strip() == "valid" for line in vout.splitlines())
        verrs = sum(1 for line in vout.splitlines() if line.startswith("ERROR"))
        vwarns = sum(1 for line in vout.splitlines() if line.startswith("WARN"))
        log += vout + (val.stderr or "")
    seconds = round(time.monotonic() - t0, 1)
    (base / "run_log.txt").write_text(log, encoding="utf-8")
    sub: dict[str, Any] = {}
    if out.is_file():
        try:
            sub = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            sub = {}
    deals = _summarise(sub)
    return UploadRunResult(
        run_id=run_id, set=set_name, ok=run.returncode == 0 and bool(sub), valid=valid, seconds=seconds,
        n_deals=len(deals), n_held=sum(d.held for d in deals), n_flagged=sum(d.flagged for d in deals),
        history_flags=[str(x) for x in (sub.get("history_flags") or [])], deals=deals,
        validate_errors=verrs, validate_warnings=vwarns,
        log_tail="\n".join(log.strip().splitlines()[-12:]),
    )


def _env() -> dict[str, str]:
    import os
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return env
