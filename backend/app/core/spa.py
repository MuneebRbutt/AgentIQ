"""Serving the built dashboard from the API, so one web service is the whole app
(FR-10-08; ADR-0012).

The dashboard is a single-page app: any path that isn't a file (`/profiles/<id>/phone`) gets
`index.html` and the browser's router takes over. API paths never fall back to it, so an
unknown endpoint is still a JSON 404.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Scope

INDEX = "index.html"


class DashboardFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.startswith("api/") or path == "api":
                raise
            response = await super().get_response(INDEX, scope)
        if path.startswith("assets/"):
            # Vite puts a content hash in these names, so they never change.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def mount_dashboard(app: FastAPI, directory: Path) -> None:
    if not (directory / INDEX).is_file():
        raise RuntimeError(f"FRONTEND_DIST has no {INDEX}: {directory} (run `npm run build`)")
    app.mount("/", DashboardFiles(directory=directory, html=True), name="dashboard")
