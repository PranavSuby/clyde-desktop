"""Event kinds flowing from Orchestrator.handle's on_event callback to the UI.

Both the emitters (agent.py) and the consumer (ui_page.py) import these
constants instead of writing bare strings — a typo on either side would
otherwise drop events silently.
"""

ROUTE = "route"            # {"route": str, "model": str} — routing decision
STATUS = "status"          # str — transient progress line
THINKING = "thinking"      # str — model reasoning tokens, streamed
TEXT_DELTA = "text_delta"  # str — answer tokens, streamed
TEXT = "text"              # str — complete answer (non-streamed paths)
IMAGES = "images"          # {"names": [str], "tags": dict} — generated images
USAGE = "usage"            # {"prompt_tokens": int, "completion_tokens": int}
ERROR = "error"            # str — user-facing error message
DONE = "done"              # None — turn finished (always emitted last)
