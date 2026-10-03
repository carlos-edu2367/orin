"""The profile file area: browse, create folders, import a .zip, download."""
from __future__ import annotations

import tempfile
from typing import Callable

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.file_preview import media_type_for
from agentos.profile_files.archive import ArchiveRejected, extract_zip
from agentos.profile_files.binding import ProfileFolderRejected, files_root, resolve_inside
from agentos.profile_files.listing import list_folder, make_folder

from .contracts import ApplicationNotFoundError, ApplicationValidationError
from .security import AuthenticatedPrincipal

MAX_IMPORT_BYTES = 512 * 1024 * 1024


class CreateFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent: str = Field(default="", max_length=4096)
    name: str = Field(min_length=1, max_length=255)


def register_files_routes(app: FastAPI, services, principal_for: Callable[..., AuthenticatedPrincipal]) -> None:
    def principal(request: Request, *, mutable: bool, purpose: str) -> AuthenticatedPrincipal:
        services.capabilities.require("profile_files")
        current = principal_for(request, mutable=mutable)
        services.security.authorize(current, action="files.write" if mutable else "files.read", resource_id=None, purpose=purpose)
        return current

    @app.get("/v1/files")
    async def list_files(request: Request, path: str = "") -> JSONResponse:
        current = principal(request, mutable=False, purpose="files.list")
        try:
            return JSONResponse(list_folder(files_root(current.user_id), path))
        except ProfileFolderRejected as error:
            raise ApplicationNotFoundError(path) from error

    @app.post("/v1/files/folders", status_code=201)
    async def create_folder(payload: CreateFolderRequest, request: Request) -> JSONResponse:
        current = principal(request, mutable=True, purpose="files.folder.create")
        try:
            created = make_folder(files_root(current.user_id), payload.parent, payload.name)
        except ProfileFolderRejected as error:
            raise ApplicationValidationError(str(error)) from error
        return JSONResponse({"path": created}, status_code=201)

    @app.post("/v1/files/import", status_code=201)
    async def import_archive(request: Request, file: UploadFile = File(...), folder_name: str = Form(default=""), parent: str = Form(default="")) -> JSONResponse:
        current = principal(request, mutable=True, purpose="files.import")
        name = folder_name.strip() or (file.filename or "importado").rsplit(".", 1)[0]
        with tempfile.TemporaryFile() as spooled:
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise ArchiveRejected("upload is larger than allowed")
                spooled.write(chunk)
            spooled.seek(0)
            created = await run_in_threadpool(extract_zip, spooled, files_root(current.user_id), parent, name)
        return JSONResponse({"path": created}, status_code=201)

    @app.get("/v1/files/download")
    async def download_file(request: Request, path: str) -> FileResponse:
        current = principal(request, mutable=False, purpose="files.download")
        try:
            target = resolve_inside(files_root(current.user_id), path)
        except ProfileFolderRejected as error:
            raise ApplicationNotFoundError(path) from error
        if not target.is_file():
            raise ApplicationNotFoundError(path)
        return FileResponse(target, media_type=media_type_for(target), filename=target.name, content_disposition_type="attachment", headers={"X-Content-Type-Options": "nosniff"})


__all__ = ["register_files_routes"]
