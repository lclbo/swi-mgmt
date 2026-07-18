"""FastAPI HTTP server for the SWI-MGMT backend."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import signal
from typing import Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from swi_mgmt.api.errors import format_snmp_error
from swi_mgmt.api.serialize import (
    app_config_dict,
    snapshot_dict,
    switch_config_dict,
)
from swi_mgmt.api.state import AppState
from swi_mgmt.config import (
    SWITCH_ORDER_MODES,
    SwitchConfig,
    apply_switch_order,
    insert_switch,
    normalize_switch_order,
)
from swi_mgmt.scenario import ScenarioError, export_scenario
from swi_mgmt.snmp.scanner import get_local_subnet, list_candidate_subnets, suggest_scan_cidr

logger = logging.getLogger(__name__)

app = FastAPI(title="SWI-MGMT API", version="0.10.20")
state = AppState()

# Shutdown token: optional secret set only when the desktop .app spawns this
# process (`--shutdown-token` / SWI_MGMT_SHUTDOWN_TOKEN). It proves the caller
# is the parent that started *this* sidecar. Without a token (npm run dev,
# manual swi-mgmt-api), POST /api/shutdown stays disabled so nothing can ask
# the process to exit over HTTP. The .app never sends shutdown for a reused
# API because it only keeps the token when it performed the spawn.
_shutdown_token: Optional[str] = None

# Tauri production webviews use tauri://localhost (macOS/Linux) or
# http(s)://tauri.localhost (Windows). credentials=True with "*" is rejected
# by WebKit and surfaces as TypeError: Load failed.
_CORS_ORIGINS = [
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SwitchBody(BaseModel):
    host: str
    community: str = "public"
    snmp_version: int = 2  # 1, 2 (v2c), or 3
    name: str = ""
    driver_id: str = ""
    port: int = 161
    v3_user: str = ""
    v3_auth_proto: str = "sha"
    v3_auth_key: str = ""
    v3_priv_proto: str = "aes128"
    v3_priv_key: str = ""


class SwitchUpdateBody(SwitchBody):
    """Update an existing switch; original_host identifies the entry to replace."""

    original_host: str


class ScanBody(BaseModel):
    cidr: str = ""
    community: str = ""
    snmp_version: int = 0


class HighlightBody(BaseModel):
    vlan_id: Optional[int] = None


class ConflictResolveBody(BaseModel):
    vlan_id: int
    choice: str = Field(pattern="^(session|switch)$")


class ConflictResolveAllBody(BaseModel):
    choice: str = Field(pattern="^(session|switch)$")


class ConfigBody(BaseModel):
    scan_community: Optional[str] = None
    scan_version: Optional[int] = None
    scan_subnet: Optional[str] = None
    poll_interval_sec: Optional[float] = None
    snmp_timeout: Optional[float] = None
    snmp_retries: Optional[int] = None
    snmp_fast_mode: Optional[bool] = None
    structure_cache_sec: Optional[float] = None
    prefetch_concurrency: Optional[int] = None
    switch_order: Optional[str] = None


class ScenarioImportBody(BaseModel):
    """Import a previously exported scenario (or compatible switches+settings JSON)."""

    scenario: dict
    mode: Literal["replace", "merge"] = "replace"
    name: Optional[str] = None


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/shutdown")
async def shutdown_api(request: Request) -> dict:
    """Gracefully stop this process (uvicorn SIGTERM). Loopback + token only."""
    if not _shutdown_token:
        raise HTTPException(status_code=404, detail="shutdown not enabled")

    # Real clients on a 127.0.0.1 bind show 127.0.0.1/::1; Starlette's
    # TestClient uses the host name "testclient".
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(status_code=403, detail="localhost only")

    provided = request.headers.get("X-SWI-Shutdown-Token", "")
    if not provided or not secrets.compare_digest(provided, _shutdown_token):
        raise HTTPException(status_code=403, detail="invalid token")

    async def _stop() -> None:
        await asyncio.sleep(0.05)
        logger.info("Shutdown requested by parent; sending SIGTERM to self")
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_stop())
    return {"status": "shutting_down"}


@app.get("/api/scenario")
async def get_scenario(name: str = "") -> dict:
    """Export switch list + SNMP/scan settings as a portable scenario file."""
    return export_scenario(state.config, name=name)


@app.post("/api/scenario")
async def import_scenario(body: ScenarioImportBody) -> dict:
    """Import a scenario; replace or merge into the current inventory."""
    payload = dict(body.scenario)
    if body.name is not None:
        payload["name"] = body.name
    try:
        summary = state.apply_scenario(payload, mode=body.mode)
    except ScenarioError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Failed to save config: {exc}") from exc
    dns = await state.resolve_ptrs([s.host for s in state.config.switches])
    return {
        "summary": summary,
        "config": app_config_dict(state.config, dns_by_host=dns),
    }


@app.get("/api/config")
async def get_config() -> dict:
    dns = await state.resolve_ptrs([s.host for s in state.config.switches])
    return app_config_dict(state.config, dns_by_host=dns)


@app.patch("/api/config")
async def patch_config(body: ConfigBody) -> dict:
    data = body.model_dump(exclude_unset=True)
    if "switch_order" in data:
        order = normalize_switch_order(data["switch_order"])
        if str(body.switch_order or "").lower().strip() not in (*SWITCH_ORDER_MODES, "custom"):
            raise HTTPException(400, "switch_order must be 'ip', 'name', or 'type'")
        data["switch_order"] = order
    for key, val in data.items():
        setattr(state.config, key, val)
    if "switch_order" in data:
        apply_switch_order(state.config)
    if body.prefetch_concurrency is not None:
        import asyncio

        state._prefetch_sem = asyncio.Semaphore(max(1, state.config.prefetch_concurrency))
    state.save()
    dns = await state.resolve_ptrs([s.host for s in state.config.switches])
    return app_config_dict(state.config, dns_by_host=dns)


@app.get("/api/drivers")
async def get_drivers() -> list:
    return state.drivers()


@app.get("/api/subnet/default")
async def default_subnet() -> dict:
    hosts = [s.host for s in state.config.switches]
    return {
        "cidr": suggest_scan_cidr(hosts),
        "candidates": list_candidate_subnets(hosts),
        "egress": get_local_subnet(),
    }


@app.get("/api/switches")
async def list_switches() -> list:
    dns = await state.resolve_ptrs([s.host for s in state.config.switches])
    return [
        switch_config_dict(s, dns_name=dns.get(s.host))
        for s in state.config.switches
    ]


def _switch_from_body(body: SwitchBody) -> SwitchConfig:
    data = body.model_dump()
    try:
        ver = int(data.get("snmp_version", 2))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "snmp_version must be 1, 2, or 3") from exc
    if ver not in (1, 2, 3):
        raise HTTPException(400, "snmp_version must be 1, 2, or 3")
    data["snmp_version"] = ver
    if ver == 3 and not str(data.get("v3_user", "")).strip():
        raise HTTPException(400, "v3_user is required for SNMPv3")
    return SwitchConfig(**data)


@app.post("/api/switches")
async def add_switch(body: SwitchBody) -> dict:
    cfg = _switch_from_body(body)
    if state.get_switch(cfg.host):
        raise HTTPException(409, f"Switch {cfg.host} already exists")
    insert_switch(state.config, cfg)
    state.save()
    dns_name = await state.resolve_ptr(cfg.host)
    return switch_config_dict(cfg, dns_name=dns_name)


@app.put("/api/switches")
async def update_switch(body: SwitchUpdateBody) -> dict:
    """Update a switch by original_host (avoids fragile IP path params)."""
    data = body.model_dump()
    original = data.pop("original_host")
    for i, sw in enumerate(state.config.switches):
        if sw.host == original:
            new_cfg = _switch_from_body(SwitchBody(**data))
            if new_cfg.host != original and state.get_switch(new_cfg.host):
                raise HTTPException(409, f"Switch {new_cfg.host} already exists")
            state.config.switches[i] = new_cfg
            state.rename_switch_state(original, new_cfg.host)
            state.invalidate_switch_cache(new_cfg.host)
            apply_switch_order(state.config)
            try:
                state.save()
            except OSError as exc:
                raise HTTPException(500, f"Failed to save config: {exc}") from exc
            dns_name = await state.resolve_ptr(new_cfg.host)
            return switch_config_dict(new_cfg, dns_name=dns_name)
    raise HTTPException(404, f"Switch not found: {original}")


@app.put("/api/switches/{host:path}")
async def update_switch_by_path(host: str, body: SwitchBody) -> dict:
    """Legacy path-based update; prefer PUT /api/switches with original_host."""
    for i, sw in enumerate(state.config.switches):
        if sw.host == host:
            new_cfg = _switch_from_body(body)
            if new_cfg.host != host and state.get_switch(new_cfg.host):
                raise HTTPException(409, f"Switch {new_cfg.host} already exists")
            state.config.switches[i] = new_cfg
            state.rename_switch_state(host, new_cfg.host)
            state.invalidate_switch_cache(new_cfg.host)
            apply_switch_order(state.config)
            try:
                state.save()
            except OSError as exc:
                raise HTTPException(500, f"Failed to save config: {exc}") from exc
            dns_name = await state.resolve_ptr(new_cfg.host)
            return switch_config_dict(new_cfg, dns_name=dns_name)
    raise HTTPException(404, "Switch not found")


@app.delete("/api/switches/{host:path}")
async def delete_switch(host: str) -> dict:
    sw = state.get_switch(host)
    if not sw:
        raise HTTPException(404, "Switch not found")
    state.config.switches = [s for s in state.config.switches if s.host != host]
    state.snapshots.pop(host, None)
    state.prev_counters.pop(host, None)
    state._structure_fetched_at.pop(host, None)
    state.save()
    return {"deleted": host}


@app.get("/api/switches/{host:path}/snapshot")
async def get_snapshot(
    host: str,
    refresh: bool = False,
    mode: Optional[Literal["full", "live", "fast"]] = Query(None),
    prefetch: bool = Query(True),
) -> dict:
    cfg = state.get_switch(host)
    if not cfg:
        raise HTTPException(404, "Switch not found")
    if refresh:
        try:
            snap = await state.refresh_switch(host, mode=mode)
        except KeyError:
            raise HTTPException(404, "Switch not found") from None
        except Exception as exc:
            raise HTTPException(502, format_snmp_error(host, cfg, exc)) from exc
        if prefetch:
            state.schedule_prefetch(host)
    else:
        snap = state.snapshots.get(host)
        if not snap:
            try:
                snap = await state.refresh_switch(host, mode=mode)
            except KeyError:
                raise HTTPException(404, "Switch not found") from None
            except Exception as exc:
                raise HTTPException(502, format_snmp_error(host, cfg, exc)) from exc
            if prefetch:
                state.schedule_prefetch(host)
    return {
        "snapshot": snapshot_dict(snap),
        "session": state.session_state(),
    }


@app.get("/api/session")
async def get_session() -> dict:
    return state.session_state()


@app.post("/api/session/highlight")
async def set_highlight(body: HighlightBody) -> dict:
    if body.vlan_id is not None and state.highlight_vlan == body.vlan_id:
        state.highlight_vlan = None
    else:
        state.highlight_vlan = body.vlan_id
    return state.session_state()


@app.post("/api/session/resolve-conflict")
async def resolve_conflict(body: ConflictResolveBody) -> dict:
    state.resolve_conflict(body.vlan_id, body.choice)
    return state.session_state()


@app.post("/api/session/resolve-conflicts")
async def resolve_all_conflicts(body: ConflictResolveAllBody) -> dict:
    state.resolve_all_conflicts(body.choice)
    return state.session_state()


@app.post("/api/scan")
async def start_scan(body: ScanBody) -> dict:
    await state.start_scan(body.cidr, body.community, body.snmp_version)
    return state.scan_status()


@app.get("/api/scan")
async def scan_status() -> dict:
    return state.scan_status()


@app.delete("/api/scan")
async def cancel_scan() -> dict:
    state.cancel_scan()
    return state.scan_status()


def _mount_static_frontend() -> None:
    """Serve built frontend when dist/ exists (optional web-only deploy)."""
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if not dist.is_dir():
        return

    index = dist / "index.html"

    @app.get("/")
    async def spa_index() -> FileResponse:
        return FileResponse(index)

    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")


_mount_static_frontend()


def main() -> None:
    global _shutdown_token

    parser = argparse.ArgumentParser(description="SWI-MGMT API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18742)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--shutdown-token",
        default="",
        help=(
            "Enable POST /api/shutdown for the desktop parent that spawned this "
            "process. Opaque secret; must match X-SWI-Shutdown-Token."
        ),
    )
    args = parser.parse_args()

    # Prefer CLI (Tauri sidecar args); env is a fallback for wrappers/tests.
    token = (args.shutdown_token or os.environ.get("SWI_MGMT_SHUTDOWN_TOKEN") or "").strip()
    _shutdown_token = token or None

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    except OSError as exc:
        # Another swi-mgmt-api (or desktop sidecar) already owns the port.
        if getattr(exc, "errno", None) in (48, 98):  # EADDRINUSE macOS / Linux
            logging.error(
                "Port %s:%s is already in use. Stop the other swi-mgmt process "
                "(e.g. quit the .app, or: lsof -nP -iTCP:%s -sTCP:LISTEN) and retry.",
                args.host,
                args.port,
                args.port,
            )
            raise SystemExit(1) from exc
        raise


if __name__ == "__main__":
    main()
