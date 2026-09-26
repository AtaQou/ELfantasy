"""Dependency-free local HTTP server for the Phase 8A control center."""

from __future__ import annotations

from copy import copy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import mimetypes
from pathlib import Path
import threading
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .service import PROJECT_ROOT

STATIC_ROOT = PROJECT_ROOT / "web" / "control_center"
LOGGER = logging.getLogger(__name__)


def create_server(
        service: Any,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        static_root: Path | str = STATIC_ROOT,
) -> ThreadingHTTPServer:
    root = Path(static_root).resolve()
    team_services: dict[int, Any] = {1: service}
    # DuckDB rejects overlapping connections to one file when one request opens
    # read-only and another opens read-write. This is a local, single-user app;
    # serialize complete requests so every connection closes before the next
    # request begins. The lock is shared by all three profile services.
    request_lock = threading.RLock()

    def team_service(raw_slot: str | None) -> Any:
        try:
            slot = int(raw_slot or "1")
        except ValueError as error:
            raise ValueError("team slot must be 1, 2, or 3") from error
        if slot not in {1, 2, 3}:
            raise ValueError("team slot must be 1, 2, or 3")
        if slot not in team_services:
            selected = copy(service)
            selected.profile_id = f"{service.profile_id}:team-{slot}"
            if hasattr(selected, "_saved_state"):
                selected._saved_state = None
            if hasattr(selected.repository, "service"):
                selected.repository = copy(selected.repository)
                selected.repository.service = selected
            team_services[slot] = selected
        return team_services[slot]

    class Handler(BaseHTTPRequestHandler):
        server_version = "ELFantasyControlCenter/1.2"

        def do_GET(self) -> None:  # noqa: N802
            with request_lock:
                self._do_GET_serialized()

        def _do_GET_serialized(self) -> None:
            self._request_id = uuid4().hex[:12]
            parsed = urlparse(self.path)
            try:
                active_service = team_service(self.headers.get("X-Team-Slot"))
            except ValueError as error:
                self._json({"error": str(error), "error_code": "INVALID_REQUEST",
                            "request_id": self._request_id}, HTTPStatus.BAD_REQUEST)
                return
            routes: dict[str, Callable[[], Any]] = {
                "/api/bootstrap": active_service.bootstrap,
                "/api/team/context": lambda: active_service.team_context(),
                "/api/dashboard": active_service.dashboard,
                "/api/strategy/current": lambda: active_service.team_strategy(),
                "/api/recommendations/latest": lambda: active_service.repository.latest_run(
                    active_service.profile_id
                ),
                "/api/monitoring": lambda: active_service.monitoring(),
                "/api/shadow/latest": lambda: _current_shadow(active_service),
            }
            if parsed.path.startswith("/api/player/"):
                self._call(lambda: active_service.player_detail(parsed.path.rsplit("/", 1)[-1]))
                return
            if parsed.path.startswith("/api/history/game/"):
                self._call(lambda: active_service.history_game_detail(parsed.path.rsplit("/", 1)[-1]))
                return
            if parsed.path.startswith("/api/history/player/"):
                query = {key: value[0] for key, value in parse_qs(parsed.query).items() if value}
                self._call(lambda: active_service.history_player_detail(
                    parsed.path.rsplit("/", 1)[-1], query
                ))
                return
            if parsed.path.startswith("/api/history/team/"):
                query = {key: value[0] for key, value in parse_qs(parsed.query).items() if value}
                self._call(lambda: active_service.history_team_detail(
                    parsed.path.rsplit("/", 1)[-1], query
                ))
                return
            if parsed.path == "/api/history":
                query = {key: value[0] for key, value in parse_qs(parsed.query).items() if value}
                section = query.pop("section", "overview")
                self._call(lambda: active_service.history(section, query))
                return
            if parsed.path == "/api/advisor/latest":
                self._call(lambda: _latest_advisor(active_service))
                return
            if parsed.path == "/api/players":
                query = parse_qs(parsed.query)
                filters = {
                    key: value[0] for key, value in query.items()
                    if key not in {"view"} and value
                }
                self._call(lambda: active_service.players(
                    view=query.get("view", ["BEST_EXPECTED_FP"])[0], filters=filters
                ))
                return
            if parsed.path in routes:
                self._call(routes[parsed.path])
                return
            if parsed.path.startswith("/api/"):
                self._json({"error": "not found", "error_code": "NOT_FOUND",
                            "request_id": self._request_id}, HTTPStatus.NOT_FOUND)
                return
            self._static(parsed.path, root)

        def do_POST(self) -> None:  # noqa: N802
            with request_lock:
                self._do_POST_serialized()

        def _do_POST_serialized(self) -> None:
            self._request_id = uuid4().hex[:12]
            parsed = urlparse(self.path)
            try:
                active_service = team_service(self.headers.get("X-Team-Slot"))
                body = self._body()
                if parsed.path == "/api/refresh":
                    result = active_service.refresh()
                elif parsed.path == "/api/team":
                    result = active_service.save_team(body)
                elif parsed.path == "/api/constraint":
                    result = active_service.set_constraint(body["entity_id"], body["constraint"])
                elif parsed.path == "/api/override":
                    result = active_service.set_override(
                        body["player_id"], body.get("decision", "UNKNOWN"),
                        note=body.get("note"), clear=bool(body.get("clear", False)),
                    )
                elif parsed.path == "/api/optimize":
                    result = active_service.optimize(
                        mode=str(body.get("mode", "CURRENT_TEAM")),
                        total_budget=(
                            float(body["total_budget"])
                            if body.get("total_budget") is not None else None
                        ),
                        bank_credits=(
                            float(body["bank_credits"])
                            if body.get("bank_credits") is not None else None
                        ),
                        max_changes=(
                            int(body["max_changes"])
                            if body.get("max_changes") is not None else None
                        ),
                        seed=int(body.get("seed", 20250801)),
                        training_simulations=int(body.get("training_simulations", 96)),
                        evaluation_simulations=int(body.get("evaluation_simulations", 512)),
                    )
                elif parsed.path == "/api/reevaluate":
                    result = active_service.reevaluate_strategy(
                        current_lineup=body.get("current_lineup"),
                        refresh_first=bool(body.get("refresh_first", True)),
                        simulations=int(body.get("simulations", 512)),
                        seed=int(body.get("seed", 20250802)),
                    )
                elif parsed.path == "/api/shadow/evaluate":
                    result = active_service.shadow_validation(body.get("shadow_snapshot_id"))
                elif parsed.path == "/api/demo/scenario" and hasattr(active_service, "set_demo_scenario"):
                    result = active_service.set_demo_scenario(body["scenario"])
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json(result)
            except (KeyError, TypeError, ValueError) as error:
                self._json({"error": str(error), "error_code": "INVALID_REQUEST",
                            "request_id": self._request_id}, HTTPStatus.BAD_REQUEST)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as error:  # pragma: no cover - defensive local boundary
                self._internal_error(error)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _call(self, callback: Callable[[], Any]) -> None:
            try:
                self._json(callback())
            except (KeyError, TypeError, ValueError) as error:
                self._json({"error": str(error), "error_code": "INVALID_REQUEST",
                            "request_id": self._request_id}, HTTPStatus.BAD_REQUEST)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as error:  # pragma: no cover - defensive local boundary
                self._internal_error(error)

        def _internal_error(self, error: Exception) -> None:
            if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                return
            LOGGER.exception("Control Center request %s failed", self._request_id, exc_info=error)
            try:
                self._json({
                    "error": (
                        "The Control Center could not complete this request. "
                        f"Technical details were logged with request {self._request_id}."
                    ),
                    "error_code": "INTERNAL_ERROR", "request_id": self._request_id,
                }, HTTPStatus.INTERNAL_SERVER_ERROR)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                raise ValueError("request body length cannot be negative")
            if length > 1_000_000:
                raise ValueError("request body is too large")
            if not length:
                return {}
            def reject_constant(value: str) -> None:
                raise ValueError(f"non-finite JSON number is not allowed: {value}")

            def finite_float(value: str) -> float:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("JSON numbers must be finite")
                return number

            value = json.loads(self.rfile.read(length), parse_constant=reject_constant,
                               parse_float=finite_float)
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            raw = json.dumps(_json_safe(value), default=str, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Request-ID", getattr(self, "_request_id", "static"))
            self.end_headers()
            self.wfile.write(raw)

        def _static(self, requested: str, root_path: Path) -> None:
            relative = "index.html" if requested in {"", "/"} else requested.lstrip("/")
            candidate = (root_path / relative).resolve()
            if root_path not in candidate.parents and candidate != root_path:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            if not candidate.is_file():
                candidate = root_path / "index.html"
            if not candidate.is_file():
                self._json({"error": "UI assets are missing"}, HTTPStatus.NOT_FOUND)
                return
            raw = candidate.read_bytes()
            mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'")
            self.end_headers()
            self.wfile.write(raw)

    return ThreadingHTTPServer((host, int(port)), Handler)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            return value
    return value


def _latest_advisor(service: Any) -> Any:
    snapshot = _current_shadow(service)
    return (
        service.repository.latest_advisor_run(snapshot["shadow_snapshot_id"])
        if snapshot else None
    )


def _current_shadow(service: Any) -> Any:
    if hasattr(service, "current_shadow_snapshot"):
        return service.current_shadow_snapshot()
    return service.repository.latest_shadow_snapshot(service.profile_id)
