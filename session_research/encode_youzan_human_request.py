"""Local JSON stdin/stdout bridge for a browser-owned challenge. No networking."""
import base64
import contextlib
import io
import json
import sys
from pathlib import Path

from youzan_human_track import build_youzan_plan, locate_youzan_piece

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "investigation/youzan-slider"))
with contextlib.redirect_stdout(io.StringIO()):
    import utils


try:
    request = json.load(sys.stdin)
    location = locate_youzan_piece(base64.b64decode(request["background"]),
                                   base64.b64decode(request["piece"]), utils.det,
                                   request["display_width"])
    plan = build_youzan_plan(location["cx"], request["raw_cy"], location["scale"],
                             start_mouse_x=request["start_x"],
                             start_mouse_y=request["start_y"])
    encrypted = utils.encrypt(request["random_string"], json.dumps(plan.payload))
    print(json.dumps({"userBehaviorData": encrypted, "coordinates": location,
                      "summary": plan.summary(), "mouse_up_ms": plan.mouse_up["t"]}))
except Exception as error:
    print(json.dumps({"error_type": type(error).__name__}))
    sys.exit(1)
