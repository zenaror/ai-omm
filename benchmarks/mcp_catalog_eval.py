"""Measure advertised MCP text without calling a model or opening user data."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from omm.mcp_server import build_server, FULL_USAGE_GUIDE


async def measure():
    with tempfile.TemporaryDirectory(prefix="omm-catalog-") as temporary:
        server, _ = build_server(Path(temporary))
        tools = await server.list_tools()
        catalog = [{"name": t.name, "description": t.description,
                    "inputSchema": t.input_schema, "outputSchema": t.output_schema} for t in tools]
        instructions = server.instructions
        serialized = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        catalog_chars = serialized(catalog)
        previous_catalog_chars = serialized([t for t in catalog if t["name"] != "usage_guide"])
        before = previous_catalog_chars + len(FULL_USAGE_GUIDE)
        after = catalog_chars + len(instructions)
        return {"measurement": "Caracteres de instruções e catálogo SDK local. Não mede tokens, serialização do cliente, cache nem faturamento.",
                "tool_count": len(tools),
                "catalog_json_chars": catalog_chars,
                "previous_instructions_plus_catalog_chars": before,
                "instructions_plus_catalog_chars": after,
                "combined_reduction_percent": round(100 * (1 - after / before), 2),
                "instructions_chars": len(instructions), "previous_instructions_chars": len(FULL_USAGE_GUIDE),
                "instructions_reduction_percent": round(100 * (1 - len(instructions) / len(FULL_USAGE_GUIDE)), 2),
                "full_guide_available": any(t.name == "usage_guide" for t in tools)}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(measure()), ensure_ascii=False, indent=2))
