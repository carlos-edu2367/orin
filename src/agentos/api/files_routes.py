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
        def _list() -> dict[str, object]:
            current = principal(request, mutable=False, purpose="files.list")
            try:
                return list_folder(files_root(current.user_id), path)
            except ProfileFolderRejected as error:
                raise ApplicationNotFoundError(path) from error

        return JSONResponse(await run_in_threadpool(_list))

    @app.post("/v1/files/folders", status_code=201)
    async def create_folder(payload: CreateFolderRequest, request: Request) -> JSONResponse:
        def _create() -> str:
            current = principal(request, mutable=True, purpose="files.folder.create")
            try:
                return make_folder(files_root(current.user_id), payload.parent, payload.name)
            except ProfileFolderRejected as error:
                raise ApplicationValidationError(str(error)) from error

        created = await run_in_threadpool(_create)
        return JSONResponse({"path": created}, status_code=201)

    @app.post("/v1/files/import", status_code=201)
    async def import_archive(request: Request, file: UploadFile = File(...), folder_name: str = Form(default=""), parent: str = Form(default="")) -> JSONResponse:
        def _auth_and_open_spool():
            current = principal(request, mutable=True, purpose="files.import")
            return current, tempfile.TemporaryFile()

        current, spooled = await run_in_threadpool(_auth_and_open_spool)
        name = folder_name.strip() or (file.filename or "importado").rsplit(".", 1)[0]
        try:
            size = 0
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise ArchiveRejected("upload is larger than allowed")
                await run_in_threadpool(spooled.write, chunk)

            def _extract() -> str:
                spooled.seek(0)
                return extract_zip(spooled, files_root(current.user_id), parent, name)

            created = await run_in_threadpool(_extract)
        finally:
            await run_in_threadpool(spooled.close)
        return JSONResponse({"path": created}, status_code=201)

    @app.get("/v1/files/download")
    async def download_file(request: Request, path: str) -> FileResponse:
        def _resolve():
            current = principal(request, mutable=False, purpose="files.download")
            try:
                target = resolve_inside(files_root(current.user_id), path)
            except ProfileFolderRejected as error:
                raise ApplicationNotFoundError(path) from error
            if not target.is_file():
                raise ApplicationNotFoundError(path)
            return target, media_type_for(target)

        target, media_type = await run_in_threadpool(_resolve)
        return FileResponse(target, media_type=media_type, filename=target.name, content_disposition_type="attachment", headers={"X-Content-Type-Options": "nosniff"})


__all__ = ["register_files_routes"]
