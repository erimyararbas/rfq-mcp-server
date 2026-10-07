"""Guard rails between the model and a system that sends real e-mails to real suppliers.

Three layers:
  1. Read-only by default. Write tools refuse to run unless RFQ_ALLOW_WRITE=true.
  2. Actions that e-mail suppliers or cannot be undone also require confirm=True, so the model
     has to describe the action and get an explicit "yes" from the user first.
  3. MCP tool annotations (readOnlyHint / destructiveHint) so the client can show its own
     approval prompts.
"""
from __future__ import annotations

import os

from mcp.types import ToolAnnotations

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DANGER = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


class GuardError(RuntimeError):
    pass


def writes_enabled() -> bool:
    return os.getenv("RFQ_ALLOW_WRITE", "false").strip().lower() in ("1", "true", "yes")


def write_guard(confirm: bool | None = None) -> None:
    """Call at the top of every write tool.

    confirm=None  -> ordinary write (only the global switch is checked)
    confirm=False -> dangerous write that the user has not confirmed yet -> refuse
    confirm=True  -> dangerous write the user explicitly approved
    """
    if not writes_enabled():
        raise GuardError("Write tools are disabled. Set RFQ_ALLOW_WRITE=true to enable them.")
    if confirm is not None and not confirm:
        raise GuardError("This action e-mails suppliers or cannot be undone. Describe it to the user, "
                         "and call again with confirm=true only after they explicitly approve.")
