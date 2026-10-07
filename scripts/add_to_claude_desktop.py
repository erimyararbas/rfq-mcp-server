"""Register this server in Claude Desktop's config (keeps a backup of the old file)."""
import glob
import json
import os
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
entry = {"command": str(python if python.exists() else Path(sys.executable)),
         "args": ["-m", "rfq_mcp.server"], "cwd": str(root)}

if os.name == "nt":
    candidates = [Path(os.environ.get("APPDATA", "")) / "Claude"]
    candidates += [Path(p) for p in glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages",
                                                           "Claude_*", "LocalCache", "Roaming", "Claude"))]
elif sys.platform == "darwin":
    candidates = [Path.home() / "Library" / "Application Support" / "Claude"]
else:
    candidates = [Path.home() / ".config" / "Claude"]

for d in [c for c in candidates if c.is_dir()] or candidates[:1]:
    d.mkdir(parents=True, exist_ok=True)
    cfg = d / "claude_desktop_config.json"
    data = {}
    if cfg.exists() and cfg.read_text(encoding="utf-8-sig").strip():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            print(f"ERROR: {cfg} is not valid JSON ({e}). Left untouched.")
            continue
        shutil.copy2(cfg, cfg.with_suffix(".json.bak"))
    data.setdefault("mcpServers", {})["rfq"] = entry
    cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Added to {cfg}. Fully quit and reopen Claude Desktop.")
