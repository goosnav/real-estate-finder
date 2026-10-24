"""Loopback-only API; state changes require an in-memory per-launch token."""

from __future__ import annotations

import secrets
import threading
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from . import __version__
from .config import AppError, static_root
from .files import MAX_FILE
from .models import RunRequest, SearchInput, SuppressionInput
from .pipeline import Jobs
from .providers import inspect_providers
from .service import Service


def create_app(service: Service | None = None, *, on_quit=None, testing: bool = False) -> FastAPI:
    service = service or Service()
    jobs = Jobs(service)
    session = secrets.token_urlsafe(32)
    provider_cache = {"at": 0, "value": []}

    @asynccontextmanager
    async def lifespan(app):
        jobs.recover_interrupted()
        app.state.ready = True
        yield
        app.state.ready = False
        jobs.shutdown()

    app = FastAPI(title="Real Estate Finder", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
    app.state.service, app.state.jobs, app.state.ready = service, jobs, False

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        host_name = host.split(":", 1)[0]
        allowed = {"127.0.0.1", "localhost"} | ({"testserver"} if testing else set())
        if host_name not in allowed:
            return JSONResponse({"error": {"code": "LOCAL-HOST", "message": "Open the local app address."}}, status_code=403)
        origin = request.headers.get("origin")
        if (origin is not None and origin != f"http://{host}") or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": {"code": "LOCAL-ORIGIN", "message": "Cross-site requests are not allowed."}}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(request.headers.get("x-session-token", ""), session):
                return JSONResponse({"error": {"code": "SESSION", "message": "Reload the app to restore this session."}}, status_code=403)
        length = request.headers.get("content-length", "0")
        try:
            if int(length) > MAX_FILE + 1024 * 1024:
                raise ValueError
        except ValueError:
            return JSONResponse({"error": {"code": "FILE-SIZE", "message": "Request is too large."}}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path != "/api/docs":
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        # Never reflect raw inputs (which can contain private search documents) into error output.
        errors = exc.errors()
        field = ".".join(str(part) for part in errors[0].get("loc", [])) if errors else "input"
        return JSONResponse({"error": {"code": "INPUT-INVALID", "message": f"Check {field or 'the input'} and try again."}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return JSONResponse({"error": {"code": "APP-ERROR", "message": "The operation failed. Saved work is intact. Try again."}}, status_code=500)

    @app.get("/health/ready")
    def ready():
        return {"ready": app.state.ready, "app": "real-estate-finder", "version": __version__}

    @app.get("/api/session")
    def get_session():
        if time.monotonic() - provider_cache["at"] > 20:
            provider_cache["value"] = inspect_providers()
            provider_cache["at"] = time.monotonic()
        return {"token": session, "version": __version__, "providers": provider_cache["value"], "data_directory": str(service.store.root)}

    @app.get("/api/searches")
    def searches():
        return {"searches": sorted(service.store.list("searches"), key=lambda c: c["updated_at"], reverse=True)}

    @app.post("/api/searches")
    def new_search(body: SearchInput):
        return service.create_search(body.model_dump())

    @app.post("/api/demo")
    def new_demo(mode: str = 'residential_rent'):
        search = service.create_demo(mode)
        return {"search": search, "run": jobs.create(search["id"], "pipeline")}

    @app.get("/api/searches/{search_id}")
    def search(search_id: str):
        return service.search_detail(search_id)

    @app.patch("/api/searches/{search_id}")
    def update_search(search_id: str, body: dict):
        return service.update_search(search_id, body)

    @app.patch("/api/properties/{opportunity_id}")
    @app.patch("/api/opportunities/{opportunity_id}")
    def update_opportunity(opportunity_id: str, body: dict):
        return service.update_opportunity(opportunity_id, body)

    async def uploaded(file: UploadFile):
        try:
            raw = await file.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE:
                raise AppError("FILE-SIZE", "Files must be 8 MB or smaller.", 413)
            return file.filename or "upload", raw
        finally:
            await file.close()

    @app.post("/api/searches/{search_id}/documents")
    async def document(search_id: str, file: Annotated[UploadFile, File()]):
        name, raw = await uploaded(file)
        return await run_in_threadpool(service.upload_document, search_id, name, raw)

    @app.delete("/api/searches/{search_id}/documents/{document_id}")
    def remove_document(search_id: str, document_id: str):
        service.delete_document(search_id, document_id)
        return {"ok": True}

    @app.post("/api/searches/{search_id}/import")
    async def import_opportunities(search_id: str, file: Annotated[UploadFile, File()]):
        name, raw = await uploaded(file)
        return await run_in_threadpool(service.import_opportunities, search_id, name, raw)

    @app.post("/api/searches/{search_id}/verification/import")
    async def import_verification(search_id: str, file: Annotated[UploadFile, File()], source: Annotated[str, Form()] = ""):
        name, raw = await uploaded(file)
        return await run_in_threadpool(service.import_verification, search_id, name, raw, source)

    @app.post("/api/searches/{search_id}/outcomes/import")
    async def import_outcomes(search_id: str, file: Annotated[UploadFile, File()]):
        name, raw = await uploaded(file)
        return await run_in_threadpool(service.import_outcomes, search_id, name, raw)

    @app.post("/api/searches/{search_id}/runs")
    def start_run(search_id: str, body: RunRequest):
        return {"run": jobs.create(search_id, body.stage)}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return {"run": service.store.get("runs", run_id), "events": service.store.events(run_id)}

    @app.post("/api/runs/{run_id}/{action}")
    def control_run(run_id: str, action: str):
        return {"run": jobs.control(run_id, action)}

    @app.get("/api/searches/{search_id}/export")
    def export(search_id: str, format: str = "csv", scope: str = "all"):
        raw = service.export(search_id, format, scope)
        mime = "text/csv; charset=utf-8" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return Response(raw, media_type=mime, headers={"Content-Disposition": f'attachment; filename="properties-{search_id}-{scope}.{format}"'})

    @app.get("/api/suppressions")
    def suppressions():
        return {"suppressions": service.store.suppressions()}

    @app.post("/api/suppressions")
    def add_suppression(body: SuppressionInput):
        return service.suppress(body.model_dump())

    @app.post("/api/suppressions/import")
    async def import_suppressions(file: Annotated[UploadFile, File()]):
        name, raw = await uploaded(file)
        return await run_in_threadpool(service.import_suppressions, name, raw)

    @app.post("/api/quit")
    def quit_app():
        if on_quit:
            threading.Timer(0.2, on_quit).start()
        else:
            jobs.shutdown()
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=static_root()), name="static")
    app.mount("/", StaticFiles(directory=static_root(), html=True), name="ui")
    return app
