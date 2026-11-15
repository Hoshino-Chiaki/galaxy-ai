"""Offline adapter: selected trajectory generator -> captured Youzan SDK format.

Upstream generator remains unmodified in investigation/slider-captcha-lab.
This module does not send requests or read account credentials.
"""
from __future__ import annotations

import importlib.util
import io
import math
import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "investigation/slider-captcha-lab/human_track.py"


@lru_cache(maxsize=1)
def upstream_generator():
    spec = importlib.util.spec_from_file_location("selected_slider_human_track", UPSTREAM)
    if spec is None or spec.loader is None:
        raise RuntimeError("selected trajectory source is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_drag


def locate_youzan_piece(background: bytes, piece: bytes, detector,
                        display_width: float = 280.0) -> dict:
    """Convert the legacy ddddocr match to the full piece's displayed left edge.

    ddddocr 1.5.6 crops transparent margins before matching. target_x is that
    crop's horizontal offset, not an extra displacement to add to the answer.
    """
    from PIL import Image
    with Image.open(io.BytesIO(background)) as image:
        natural_width = image.width
    scale = float(display_width) / natural_width
    result = detector.slide_match(piece, background)
    if len(result.get("target", [])) != 4:
        raise ValueError("expected ddddocr legacy bounding-box result, not center coordinates")
    raw_left = result["target"][0] - result.get("target_x", 0)
    return {"cx": math.ceil(raw_left * scale), "scale": scale,
            "raw_piece_left": raw_left, "crop_x": result.get("target_x", 0),
            "natural_width": natural_width, "display_width": display_width}


@dataclass
class DragPlan:
    payload: dict
    mouse_down: dict
    mouse_up: dict
    movement_end_ms: int
    press_delay_ms: int
    release_delay_ms: int
    source_steps: int
    _created_monotonic: float = field(repr=False)

    @property
    def duration_ms(self):
        return self.mouse_up["t"] - self.mouse_down["t"]

    def wait_until_ready(self):
        """Do not submit while any declared event is still in the future."""
        remaining = self._created_monotonic + self.duration_ms / 1000 - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    def summary(self):
        events = self.payload["slidingEvents"]
        return {"engine": "kangleyao/slider-captcha-lab:baseline_current",
                "cx": self.payload["cx"], "cy": self.payload["cy"],
                "scale": self.payload["scale"], "initial_piece_left": 14,
                "mouse_dx": sum(e["mx"] for e in events[1:]),
                "event_count": len(events), "source_steps": self.source_steps,
                "movement_dt_ms": sum(e["ts"] for e in events[1:]),
                "press_delay_ms": self.press_delay_ms,
                "release_delay_ms": self.release_delay_ms,
                "down_to_up_ms": self.duration_ms}


def build_youzan_plan(cx: int, raw_cy: float, scale: float = .5, *,
                      start_mouse_x: int = 30, start_mouse_y: int = 200,
                      start_ms: int | None = None, seed: int | None = None,
                      total_s: float | None = None, events: int | None = None) -> DragPlan:
    """Keep upstream movement logic; translate geometry and event representation.

    The captured SDK starts the piece at left=14. Its first event is absolute
    relative to the widget, with an epoch timestamp. All following events are
    deltas. Mouse-up is not appended to slidingEvents by that SDK.
    """
    if isinstance(cx, bool) or not isinstance(cx, int) or not 14 < cx <= 240:
        raise ValueError("cx must be an integer in the captured 280px widget's drag range")
    if not math.isfinite(scale) or scale <= 0 or not math.isfinite(raw_cy) or raw_cy < 0:
        raise ValueError("invalid scale or vertical coordinate")
    if events is not None and not 5 <= events <= 300:
        raise ValueError("event count must fit the captured SDK's 300-point limit")
    if total_s is not None and (not math.isfinite(total_s) or total_s <= 0):
        raise ValueError("duration must be positive")
    rng = random.Random(seed)
    distance = cx - 14
    raw = upstream_generator()(distance, total_s=total_s, events=events,
                               rng=rng, bias_px=0.0, variant="baseline_current")
    if not raw or len(raw) + 1 > 300:
        raise ValueError("upstream event count is incompatible with the captured SDK")
    if any(not all(math.isfinite(v) for v in step) or step[2] <= 0 for step in raw):
        raise ValueError("upstream generated invalid steps")
    if not math.isclose(sum(p[0] for p in raw), distance, abs_tol=1e-6):
        raise ValueError("upstream cumulative displacement does not match the requested distance")

    # Match the upstream execution layer's before/after movement waits.
    hold, release = rng.randint(50, 110), rng.randint(10, 40)
    epoch = int(time.time() * 1000) if start_ms is None else int(start_ms)
    created = time.monotonic()
    encoded = [{"mx": start_mouse_x, "my": start_mouse_y, "ts": epoch}]
    x, y, elapsed = float(start_mouse_x), float(start_mouse_y), float(hold)
    previous_x, previous_y, previous_t = start_mouse_x, start_mouse_y, epoch
    for dx, dy, dt in raw:
        x += dx
        y += dy
        elapsed += dt
        # Quantize positions before differencing, so rounding does not accumulate.
        point_x, point_y = round(x), round(y)
        point_t = epoch + round(elapsed)
        encoded.append({"mx": point_x - previous_x, "my": point_y - previous_y,
                        "ts": point_t - previous_t})
        previous_x, previous_y, previous_t = point_x, point_y, point_t

    plan = DragPlan(
        payload={"cx": cx, "cy": math.ceil(raw_cy * scale), "scale": scale,
                 "slidingEvents": encoded},
        mouse_down={"x": start_mouse_x, "y": start_mouse_y, "t": epoch},
        mouse_up={"x": previous_x, "y": previous_y, "t": previous_t + release},
        movement_end_ms=previous_t, press_delay_ms=hold, release_delay_ms=release,
        source_steps=len(raw), _created_monotonic=created)
    validate_plan(plan)
    return plan


def validate_plan(plan: DragPlan):
    points = plan.payload["slidingEvents"]
    if set(plan.payload) != {"cx", "cy", "scale", "slidingEvents"}:
        raise ValueError("unexpected fields in encrypted SDK payload")
    x, y, stamp = points[0]["mx"], points[0]["my"], points[0]["ts"]
    if (x, y, stamp) != tuple(plan.mouse_down[k] for k in ("x", "y", "t")):
        raise ValueError("initial event differs from mouse-down")
    for point in points[1:]:
        if point["ts"] < 0:
            raise ValueError("event time moved backwards")
        x += point["mx"]
        y += point["my"]
        stamp += point["ts"]
    if (x, y) != (plan.mouse_up["x"], plan.mouse_up["y"]):
        raise ValueError("mouse-up position differs from final movement")
    if stamp != plan.movement_end_ms or plan.mouse_up["t"] != stamp + plan.release_delay_ms:
        raise ValueError("mouse-up time does not follow the final movement")
    if x - plan.mouse_down["x"] + 14 != plan.payload["cx"]:
        raise ValueError("mouse displacement does not produce the requested piece position")


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Generate an offline Youzan-format drag sample")
    parser.add_argument("--cx", type=int, default=174)
    parser.add_argument("--raw-cy", type=float, default=112)
    parser.add_argument("--scale", type=float, default=.5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plan = build_youzan_plan(args.cx, args.raw_cy, args.scale, seed=args.seed)
    result = {"summary": plan.summary(), "mouse_down": plan.mouse_down,
              "mouse_up": plan.mouse_up, "movement_end_ms": plan.movement_end_ms,
              "payload": plan.payload}
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(plan.summary(), ensure_ascii=False, indent=2))
