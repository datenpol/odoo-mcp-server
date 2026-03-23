# Odoo MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that connects AI agents to Odoo ERP instances. Works with Claude Code, Cursor, Windsurf, and any MCP-compatible client.

Supports **Odoo 17-18** (JSON-RPC) and **Odoo 19+** (JSON-2 API) — auto-detects the best protocol.

## Quick Start

```bash
# Run directly (uv handles dependencies automatically)
ODOO_URL=https://my.odoo.com ODOO_DB=mydb ODOO_USER=admin ODOO_PASSWORD=secret \
  uv run odoo_mcp_server.py
```

No virtualenv or `pip install` needed — the script has [inline metadata](https://packaging.python.org/en/latest/specifications/inline-script-metadata/) that `uv` resolves automatically.

## Configure in Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "odoo": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--python", "3.11", "--script", "/path/to/odoo_mcp_server.py"],
      "env": {
        "ODOO_URL": "https://your-instance.odoo.com",
        "ODOO_DB": "your-database",
        "ODOO_USER": "admin",
        "ODOO_PASSWORD": "your-password"
      }
    }
  }
}
```

Or for Odoo 19+ with API key auth:

```json
{
  "mcpServers": {
    "odoo": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--python", "3.11", "--script", "/path/to/odoo_mcp_server.py"],
      "env": {
        "ODOO_URL": "https://your-instance.odoo.com",
        "ODOO_DB": "your-database",
        "ODOO_USER": "admin",
        "ODOO_API_KEY": "your-api-key"
      }
    }
  }
}
```

## Configure in Cursor / Windsurf

Add to `~/.cursor/mcp.json` or equivalent:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "uv",
      "args": ["run", "--python", "3.11", "--script", "/path/to/odoo_mcp_server.py"],
      "env": {
        "ODOO_URL": "https://your-instance.odoo.com",
        "ODOO_DB": "your-database",
        "ODOO_USER": "admin",
        "ODOO_PASSWORD": "your-password"
      }
    }
  }
}
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ODOO_URL` | Yes | Odoo instance URL |
| `ODOO_DB` | Yes | Database name |
| `ODOO_USER` | Yes | Login username |
| `ODOO_PASSWORD` | One of these | Password (Odoo 17-18) |
| `ODOO_API_KEY` | required | API key (Odoo 19+, preferred) |
| `ODOO_READONLY` | No | Set to `true` to disable all write operations |

### Read-Only Mode

Set `ODOO_READONLY=true` to disable `create`, `update`, `delete`, and `execute` tools. Useful for safe browsing of production instances:

```json
{
  "mcpServers": {
    "odoo": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--python", "3.11", "--script", "/path/to/odoo_mcp_server.py"],
      "env": {
        "ODOO_URL": "https://production.odoo.com",
        "ODOO_DB": "prod",
        "ODOO_USER": "readonly-user",
        "ODOO_PASSWORD": "secret",
        "ODOO_READONLY": "true"
      }
    }
  }
}
```

## Available Tools

| Tool | Description |
|---|---|
| `odoo_search_read` | Query records with domain filters, field selection, pagination |
| `odoo_search_count` | Count matching records without fetching data |
| `odoo_export` | Bulk export up to 2000 records per call for spreadsheets |
| `odoo_create` | Create new records |
| `odoo_update` | Update existing records by ID |
| `odoo_delete` | Delete records by ID |
| `odoo_execute` | Run any model method (action_confirm, action_post, etc.) |
| `odoo_list_models` | Discover available models with keyword filter |
| `odoo_get_fields` | Inspect field definitions for any model |
| `odoo_doctor` | Health diagnostics (version, modules, users, crons, errors) |
| `odoo_connection_info` | Show current connection details |

## Example Usage

Once configured, ask your AI agent:

- "List all sale orders from this month"
- "Show me the fields on res.partner"
- "Create a new contact named Acme Corp"
- "Run a health check on the Odoo instance"
- "Export all products to a spreadsheet"
- "Confirm sale order 42"

## How It Works

```
AI Agent (Claude, Cursor, etc.)
    ↕ MCP Protocol (stdio)
Odoo MCP Server
    ↕ JSON-RPC / JSON-2 API
Odoo Instance
```

The server authenticates once at startup and maintains a persistent connection. All tools use the same authenticated session.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip install fastmcp httpx`

## License

MIT
