#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fastmcp>=2.0",
#     "httpx>=0.27",
# ]
# ///
"""Standalone MCP server exposing Odoo operations as tools.

Run with:
    uv run odoo_mcp_server.py

Or configure in your MCP client (e.g. Claude Code, Cursor, OdooCLI):
    mcp_servers:
      odoo:
        command: uv
        args: [run, odoo_mcp_server.py]
        env:
          ODOO_URL: "https://your-instance.odoo.com"
          ODOO_DB: "your-database"
          ODOO_USER: "admin"
          ODOO_PASSWORD: "your-password"

Environment variables:
    ODOO_URL       — Odoo instance URL           (required)
    ODOO_DB        — Database name                (required)
    ODOO_USER      — Login username               (required)
    ODOO_PASSWORD  — Password for Odoo 17-18      (one of password/api_key required)
    ODOO_API_KEY   — API key for Odoo 19+         (preferred when available)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Odoo client (self-contained — no imports from the rest of the project)
# ---------------------------------------------------------------------------


class OdooConnectionError(Exception):
    pass


class OdooClient:
    """Lightweight Odoo client supporting JSON-RPC (v17-18) and JSON-2 (v19+)."""

    def __init__(
        self,
        url: str,
        database: str,
        username: str,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.url = url.rstrip("/")
        self.database = database
        self.username = username
        self.password = password
        self.api_key = api_key
        self.uid: Optional[int] = None
        self.version: Optional[str] = None
        self._http = httpx.Client(timeout=30.0)

    # -- auth ----------------------------------------------------------------

    def authenticate(self) -> int:
        self.version = self._detect_version()

        if self.api_key and self._is_v19_plus():
            self.uid = self._auth_json2()
        else:
            self.uid = self._auth_jsonrpc()

        if not self.uid:
            raise OdooConnectionError(
                f"Authentication failed for {self.username}@{self.url}/{self.database}"
            )
        return self.uid

    def _detect_version(self) -> str:
        try:
            resp = self._jsonrpc(
                f"{self.url}/jsonrpc", "call",
                service="common", method="version", args=[],
            )
            return resp.get("server_version", "unknown")
        except Exception:
            return "unknown"

    def _is_v19_plus(self) -> bool:
        if not self.version or self.version == "unknown":
            return False
        try:
            return int(self.version.split(".")[0]) >= 19
        except (ValueError, IndexError):
            return False

    def _auth_jsonrpc(self) -> Optional[int]:
        try:
            result = self._jsonrpc(
                f"{self.url}/jsonrpc", "call",
                service="common", method="authenticate",
                args=[self.database, self.username, self.password or "", {}],
            )
            return result if isinstance(result, int) else None
        except Exception as exc:
            logger.error("JSON-RPC auth failed: %s", exc)
            return None

    def _auth_json2(self) -> Optional[int]:
        try:
            resp = self._http.get(
                f"{self.url}/api/res.users/whoami",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("uid") or data.get("id")
        except Exception as exc:
            logger.error("JSON-2 auth failed: %s", exc)
            return None

    # -- execute -------------------------------------------------------------

    def execute(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if self.uid is None:
            raise OdooConnectionError("Not authenticated.")
        if self._is_v19_plus() and self.api_key:
            return self._exec_json2(model, method, *args, **kwargs)
        return self._exec_jsonrpc(model, method, *args, **kwargs)

    def _exec_jsonrpc(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._jsonrpc(
            f"{self.url}/jsonrpc", "call",
            service="object", method="execute_kw",
            args=[self.database, self.uid, self.password or "",
                  model, method, list(args), kwargs],
        )

    def _exec_json2(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        payload: dict[str, Any] = {}
        if args:
            payload["args"] = list(args)
        if kwargs:
            payload.update(kwargs)
        resp = self._http.post(
            f"{self.url}/api/{model}/{method}",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        resp.raise_for_status()
        return resp.json()

    # -- convenience ---------------------------------------------------------

    def search_read(
        self, model: str, domain: list | None = None,
        fields: list[str] | None = None, limit: int = 2000,
        offset: int = 0, order: str | None = None,
    ) -> list[dict]:
        kw: dict[str, Any] = {"limit": limit, "offset": offset}
        if fields:
            kw["fields"] = fields
        if order:
            kw["order"] = order
        return self.execute(model, "search_read", domain or [], **kw)

    def search_count(self, model: str, domain: list | None = None) -> int:
        return self.execute(model, "search_count", domain or [])

    def create(self, model: str, values: dict) -> int:
        return self.execute(model, "create", values)

    def write(self, model: str, ids: list[int], values: dict) -> bool:
        return self.execute(model, "write", ids, values)

    def unlink(self, model: str, ids: list[int]) -> bool:
        return self.execute(model, "unlink", ids)

    # -- transport -----------------------------------------------------------

    def _jsonrpc(self, url: str, rpc_method: str, **params: Any) -> Any:
        payload = {"jsonrpc": "2.0", "method": rpc_method, "params": params, "id": 1}
        resp = self._http.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            err = body["error"]
            msg = err.get("data", {}).get("message", err.get("message", str(err)))
            raise OdooConnectionError(f"Odoo error: {msg}")
        return body.get("result")

    def close(self) -> None:
        self._http.close()


# ---------------------------------------------------------------------------
# Connect on startup
# ---------------------------------------------------------------------------

def _connect_from_env() -> OdooClient:
    url = os.environ.get("ODOO_URL", "")
    db = os.environ.get("ODOO_DB", "")
    user = os.environ.get("ODOO_USER", "")
    password = os.environ.get("ODOO_PASSWORD")
    api_key = os.environ.get("ODOO_API_KEY")

    if not all([url, db, user]):
        raise SystemExit(
            "Set ODOO_URL, ODOO_DB, and ODOO_USER environment variables.\n"
            "Also set ODOO_PASSWORD (v17-18) or ODOO_API_KEY (v19+)."
        )
    if not password and not api_key:
        raise SystemExit(
            "Set ODOO_PASSWORD (for Odoo 17-18) or ODOO_API_KEY (for Odoo 19+)."
        )

    client = OdooClient(url=url, database=db, username=user,
                        password=password, api_key=api_key)
    client.authenticate()
    return client


odoo: OdooClient  # set at startup

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("odoo")


# -- Tools -------------------------------------------------------------------

@mcp.tool()
def odoo_search_read(
    model: str,
    domain: list | None = None,
    fields: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    order: str | None = None,
) -> str:
    """Search and read records from any Odoo model.

    Args:
        model: Odoo model technical name (e.g. "res.partner", "sale.order").
        domain: Search filter as a list of tuples, e.g. [["state","=","sale"]].
        fields: List of field names to return. None returns all fields.
        limit: Maximum number of records (default 20).
        offset: Number of records to skip for pagination.
        order: Sort string, e.g. "create_date desc".

    Returns:
        JSON string with matching records.
    """
    records = odoo.search_read(model, domain=domain, fields=fields,
                               limit=limit, offset=offset, order=order)
    return json.dumps({"model": model, "count": len(records),
                       "records": records}, default=str)


@mcp.tool()
def odoo_search_count(model: str, domain: list | None = None) -> str:
    """Count records matching a domain filter without fetching data.

    Use this before a large export to know how many records exist.

    Args:
        model: Odoo model technical name (e.g. "res.partner").
        domain: Search filter, e.g. [["active","=",true]]. None counts all.

    Returns:
        JSON string with the total count.
    """
    total = odoo.search_count(model, domain=domain)
    return json.dumps({"model": model, "count": total})


@mcp.tool()
def odoo_export(
    model: str,
    domain: list | None = None,
    fields: list[str] | None = None,
    limit: int = 500,
    offset: int = 0,
    order: str | None = None,
) -> str:
    """Export records in bulk for spreadsheet use. Higher default limit than search_read.

    Fetches up to 500 records per call (vs 20 for search_read). Use offset
    to paginate through large datasets. Pair with odoo_search_count to know
    the total.

    Args:
        model: Odoo model technical name.
        domain: Search filter. None returns all records.
        fields: Fields to export (column headers). None returns all fields.
        limit: Max records per call (default 500, max 2000).
        offset: Skip this many records (for pagination).
        order: Sort string, e.g. "id asc".

    Returns:
        JSON string with records, count returned, and offset for next page.
    """
    limit = min(limit, 2000)
    records = odoo.search_read(model, domain=domain, fields=fields,
                               limit=limit, offset=offset, order=order)
    return json.dumps({
        "model": model,
        "count": len(records),
        "offset": offset,
        "next_offset": offset + len(records) if len(records) == limit else None,
        "records": records,
    }, default=str)


@mcp.tool()
def odoo_create(model: str, values: dict) -> str:
    """Create a new record in an Odoo model.

    Args:
        model: Odoo model technical name (e.g. "res.partner").
        values: Field values for the new record, e.g. {"name": "Acme Corp"}.

    Returns:
        JSON string with the new record ID.
    """
    new_id = odoo.create(model, values)
    return json.dumps({"model": model, "operation": "create", "id": new_id})


@mcp.tool()
def odoo_update(model: str, ids: list[int], values: dict) -> str:
    """Update existing records in an Odoo model.

    Args:
        model: Odoo model technical name.
        ids: List of record IDs to update.
        values: Field values to write, e.g. {"phone": "+1-555-0100"}.

    Returns:
        JSON string confirming the update.
    """
    result = odoo.write(model, ids, values)
    return json.dumps({"model": model, "operation": "update",
                       "ids": ids, "success": result})


@mcp.tool()
def odoo_delete(model: str, ids: list[int]) -> str:
    """Delete records from an Odoo model.

    Args:
        model: Odoo model technical name.
        ids: List of record IDs to delete.

    Returns:
        JSON string confirming the deletion.
    """
    result = odoo.unlink(model, ids)
    return json.dumps({"model": model, "operation": "delete",
                       "ids": ids, "success": result})


@mcp.tool()
def odoo_execute(model: str, method: str, ids: list[int] | None = None) -> str:
    """Execute any model method (action) on Odoo records.

    Use this for workflow actions like action_confirm, action_post, etc.

    Args:
        model: Odoo model technical name (e.g. "account.move").
        method: Method name (e.g. "action_post", "action_confirm").
        ids: Record IDs to act on. Pass None or [] for methods that need no IDs.

    Returns:
        JSON string with the method result.
    """
    result = odoo.execute(model, method, ids or [])
    return json.dumps({"model": model, "method": method,
                       "ids": ids, "result": result}, default=str)


@mcp.tool()
def odoo_list_models(keyword: str = "") -> str:
    """List available Odoo models, optionally filtered by keyword.

    Args:
        keyword: Filter models whose technical name contains this string
                 (e.g. "sale", "stock", "account").

    Returns:
        JSON string with matching model names and descriptions.
    """
    domain: list = [["transient", "=", False]]
    if keyword:
        domain.append(["model", "ilike", keyword])

    models = odoo.search_read(
        "ir.model", domain=domain,
        fields=["model", "name"], limit=50, order="model",
    )
    return json.dumps({
        "count": len(models),
        "models": [{"model": m["model"], "name": m["name"]} for m in models],
    })


@mcp.tool()
def odoo_get_fields(model: str, attributes: list[str] | None = None) -> str:
    """Get field definitions for an Odoo model.

    Useful for discovering what fields a model has before reading/writing.

    Args:
        model: Odoo model technical name (e.g. "sale.order").
        attributes: Field attributes to return (e.g. ["string", "type", "required"]).
                    None returns all attributes.

    Returns:
        JSON string with field metadata.
    """
    attrs = attributes or ["string", "type", "required", "readonly", "help"]
    result = odoo.execute(model, "fields_get", attributes=attrs)
    return json.dumps({"model": model, "fields": result}, default=str)


@mcp.tool()
def odoo_doctor() -> str:
    """Run health diagnostics on the connected Odoo instance.

    Checks server version, installed modules, active users, cron jobs,
    and recent error logs.

    Returns:
        JSON string with diagnostic results.
    """
    checks = []

    # Server version
    checks.append({
        "check": "server_version",
        "status": "ok",
        "value": odoo.version,
    })

    # Installed modules
    try:
        modules = odoo.search_read(
            "ir.module.module",
            domain=[["state", "=", "installed"]],
            fields=["name", "shortdesc"], limit=200,
        )
        checks.append({
            "check": "installed_modules", "status": "ok",
            "value": len(modules),
            "modules": [m["name"] for m in modules],
        })
    except Exception as exc:
        checks.append({"check": "installed_modules", "status": "error", "value": str(exc)})

    # Active users
    try:
        users = odoo.search_read(
            "res.users", domain=[["active", "=", True]],
            fields=["login"], limit=500,
        )
        checks.append({"check": "active_users", "status": "ok", "value": len(users)})
    except Exception as exc:
        checks.append({"check": "active_users", "status": "error", "value": str(exc)})

    # Cron jobs
    try:
        crons = odoo.search_read(
            "ir.cron", domain=[["active", "=", True]],
            fields=["name", "interval_type", "interval_number", "nextcall"],
            limit=100,
        )
        checks.append({"check": "active_cron_jobs", "status": "ok", "value": len(crons)})
    except Exception as exc:
        checks.append({"check": "active_cron_jobs", "status": "error", "value": str(exc)})

    # Recent errors
    try:
        errors = odoo.search_read(
            "ir.logging",
            domain=[["level", "=", "ERROR"], ["type", "=", "server"]],
            fields=["name", "message", "create_date"],
            limit=5, order="create_date desc",
        )
        checks.append({
            "check": "recent_errors",
            "status": "warning" if errors else "ok",
            "value": len(errors), "errors": errors,
        })
    except Exception:
        checks.append({
            "check": "recent_errors", "status": "skipped",
            "value": "ir.logging not accessible",
        })

    ok_count = sum(1 for c in checks if c["status"] == "ok")
    return json.dumps({
        "instance": odoo.url, "database": odoo.database,
        "version": odoo.version,
        "summary": f"{ok_count}/{len(checks)} checks passed",
        "checks": checks,
    }, default=str)


@mcp.tool()
def odoo_connection_info() -> str:
    """Show the current Odoo connection details.

    Returns:
        JSON string with URL, database, user, version, and uid.
    """
    return json.dumps({
        "url": odoo.url,
        "database": odoo.database,
        "username": odoo.username,
        "version": odoo.version,
        "uid": odoo.uid,
    })


# -- Resources ---------------------------------------------------------------

@mcp.resource("odoo://connection")
def connection_info() -> str:
    """Current Odoo connection metadata."""
    return json.dumps({
        "url": odoo.url, "database": odoo.database,
        "username": odoo.username, "version": odoo.version, "uid": odoo.uid,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global odoo
    odoo = _connect_from_env()
    mcp.instructions = (
        "Odoo ERP tools. You are connected to "
        f"{odoo.url} (database: {odoo.database}, Odoo {odoo.version})."
    )
    mcp.run()


if __name__ == "__main__":
    main()
