"""
Google Maps Static API wrapper for generating the CMA comparable maps.

Produces PNG images where comparable properties appear as colored
teardrop callouts:
    - Subject property:  black, no number, tip at true coord
    - Comparable sale:   blue,  numbered, tip at true coord
    - Comparable listing:red,   numbered, tip at true coord

When multiple callouts would overlap (same building / same short street),
the overlapping ones are displaced onto a circle around the cluster and
drawn as long teardrops whose tip stays on the real address while the
rounded head sits in open space so the number is readable.
"""
from __future__ import annotations

import io
import math
import requests
from dataclasses import dataclass
from typing import Literal, Optional
from PIL import Image, ImageDraw, ImageFont


MarkerKind = Literal["subject", "sold", "listing"]


@dataclass
class MapMarker:
    lat: float
    lng: float
    label: str        # numeric label ("1", "2", "10"...) or "" for subject
    kind: MarkerKind


# RGB callout colors. Mirrors the in-PDF legend colors exactly.
_CALLOUT_RGB: dict[str, tuple[int, int, int]] = {
    "subject": (0, 0, 0),
    "sold":    (0x1e, 0x9a, 0xd6),  # blue
    "listing": (0xd9, 0x3b, 0x3b),  # red
}

# Darker border drawn behind each callout to make it pop off the basemap.
_OUTLINE_RGB: dict[str, tuple[int, int, int]] = {
    "subject": (20, 20, 20),
    "sold":    (0x0a, 0x50, 0x78),  # dark blue
    "listing": (0x7a, 0x18, 0x18),  # dark red
}


class StaticMapClient:
    """
    Request a basemap PNG from Google Static Maps and overlay our own
    teardrop callouts. We do not ask Google to draw markers because:
      - its ``label:`` attribute only accepts single characters (can't
        show two-digit indices like "10"), and
      - we need a long tapered-tail shape when overlapping pins are
        displaced, which Google's default pins don't provide.
    """

    BASE_URL = "https://maps.googleapis.com/maps/api/staticmap"

    # Kept for backwards-compat with callers that inspect it.
    COLORS = {
        "subject": "black",
        "sold": "blue",
        "listing": "red",
    }

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Google Maps API key is required")
        self.api_key = api_key

    def _best_zoom(
        self,
        markers: list,
        center_lat: float,
        center_lng: float,
        out_w: int,
        out_h: int,
        scale: int,
        margin_px: int = 60,
    ) -> int:
        """Return largest zoom in [8, 16] where every marker tip fits inside the canvas."""
        if len(markers) <= 1:
            return 14
        for z in range(16, 7, -1):
            if all(
                margin_px <= _latlng_to_pixel(m.lat, m.lng, center_lat, center_lng, z, scale, out_w, out_h)[0] <= out_w - margin_px
                and margin_px <= _latlng_to_pixel(m.lat, m.lng, center_lat, center_lng, z, scale, out_w, out_h)[1] <= out_h - margin_px
                for m in markers
            ):
                return z
        return 8

    def render(
        self,
        markers: list[MapMarker],
        width: int = 1200,
        height: int = 1400,
        scale: int = 2,
        maptype: str = "roadmap",
        marker_scale: float = 1.0,
        label_scale: float = 1.0,
    ) -> bytes:
        """
        Fetch the basemap from Google and composite our own callouts.

        `width` / `height` are requested output pixels. Google's free tier
        accepts up to 640x640 source at scale=2 (→ 1280x1280 output).
        """
        src_w = min(width // scale, 640)
        src_h = min(height // scale, 640)
        out_w = src_w * scale
        out_h = src_h * scale

        # Center on the subject pin if present, else on cluster centroid.
        subject = next((m for m in markers if m.kind == "subject"), None)
        if subject is not None:
            center_lat, center_lng = subject.lat, subject.lng
        elif markers:
            center_lat = sum(m.lat for m in markers) / len(markers)
            center_lng = sum(m.lng for m in markers) / len(markers)
        else:
            center_lat, center_lng = -33.6, 150.86

        zoom = self._best_zoom(markers, center_lat, center_lng, src_w * scale, src_h * scale, scale)

        params = [
            ("size", f"{src_w}x{src_h}"),
            ("scale", str(scale)),
            ("maptype", maptype),
            ("format", "png"),
            ("key", self.api_key),
            ("center", f"{center_lat:.6f},{center_lng:.6f}"),
            ("zoom", str(zoom)),
        ]

        r = requests.get(self.BASE_URL, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(
                f"Google Static Maps error {r.status_code}: {r.text[:200]}"
            )
        if not r.headers.get("content-type", "").startswith("image"):
            raise RuntimeError(f"Unexpected response: {r.text[:200]}")

        base = Image.open(io.BytesIO(r.content)).convert("RGBA")
        out_w, out_h = base.size

        # Draw markers at SS× resolution then downscale with LANCZOS so
        # polygon edges and circle outlines get high-quality anti-aliasing.
        SS = 3
        overlay_ss = Image.new("RGBA", (out_w * SS, out_h * SS), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay_ss)

        # Draw subjects last so they always appear on top.
        ordered = [m for m in markers if m.kind != "subject"] + \
                  [m for m in markers if m.kind == "subject"]

        tip_margin = 4
        placements: list[tuple[MapMarker, tuple[float, float], tuple[float, float], int]] = []
        for m in ordered:
            # Tip at this marker's exact true address.
            tip_xy = _latlng_to_pixel(
                m.lat, m.lng, center_lat, center_lng, zoom, scale, out_w, out_h,
            )
            tip_xy = (
                max(tip_margin, min(out_w - tip_margin, tip_xy[0])),
                max(tip_margin, min(out_h - tip_margin, tip_xy[1])),
            )

            r = max(10, int(_head_radius(m.label) * marker_scale))
            pad = r + 6

            # Initial head position: above → below → right → left.
            up_y    = tip_xy[1] - r * 2 - 10
            down_y  = tip_xy[1] + r * 2 + 10
            right_x = tip_xy[0] + r * 2 + 10
            left_x  = tip_xy[0] - r * 2 - 10
            if up_y >= pad:
                head_xy = (tip_xy[0], up_y)
            elif down_y <= out_h - pad:
                head_xy = (tip_xy[0], down_y)
            elif right_x <= out_w - pad:
                head_xy = (right_x, tip_xy[1])
            else:
                head_xy = (left_x, tip_xy[1])

            head_xy = (
                max(pad, min(out_w - pad, head_xy[0])),
                max(pad, min(out_h - pad, head_xy[1])),
            )
            placements.append((m, tip_xy, head_xy, r))

        resolved = _resolve_by_rotation(placements, out_w, out_h)
        # Canvas clamping inside _resolve_by_rotation can bring two heads that
        # were correctly separated back into overlap (e.g. both clamped to the
        # same edge).  Run the force-based resolver as a final safety pass.
        resolved = _resolve_head_overlaps(resolved, out_w, out_h)

        for (m, tip_xy, head_xy, r) in resolved:
            _draw_teardrop_callout(
                draw,
                (tip_xy[0] * SS, tip_xy[1] * SS),
                (head_xy[0] * SS, head_xy[1] * SS),
                _CALLOUT_RGB.get(m.kind, _CALLOUT_RGB["listing"]),
                m.label,
                tail_setback_px=0.0,
                thin_connector=False,
                radius=r * SS,
                label_scale=label_scale,
                outline_rgb=_OUTLINE_RGB.get(m.kind),
                outline_width=2 * SS,
            )

        overlay = overlay_ss.resize((out_w, out_h), Image.LANCZOS)
        out_img = Image.alpha_composite(base, overlay).convert("RGB")
        buf = io.BytesIO()
        out_img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    def render_with_overflow_labels(
        self,
        markers: list[MapMarker],
        width: int = 1200,
        height: int = 1400,
        scale: int = 2,
    ) -> bytes:
        """Kept for backwards-compat. render() already handles overflow labels."""
        return self.render(markers, width, height, scale)


# Simple helper for callers that don't want to instantiate
def render_map(
    api_key: str,
    markers: list[MapMarker],
    width: int = 1200,
    height: int = 1400,
) -> bytes:
    return StaticMapClient(api_key).render(markers, width=width, height=height)


# ---- Callout drawing --------------------------------------------------

_FONT_CACHE: dict[int, "ImageFont.ImageFont"] = {}


def _load_bold_font(size: int):
    f = _FONT_CACHE.get(size)
    if f is not None:
        return f
    for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            f = ImageFont.truetype(name, size)
            _FONT_CACHE[size] = f
            return f
        except Exception:
            continue
    f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


def _head_radius(label: str) -> int:
    """Bulb radius in pixels. Grows for multi-digit labels."""
    n = len(label or "")
    if n <= 1:
        return 18
    if n == 2:
        return 22
    return 26


def _draw_teardrop_callout(
    draw: "ImageDraw.ImageDraw",
    tip_xy: tuple[float, float],
    head_xy: tuple[float, float],
    fill_rgb: tuple[int, int, int],
    label: str,
    tail_setback_px: float = 0.0,
    thin_connector: bool = False,
    radius: int | None = None,
    label_scale: float = 1.0,
    outline_rgb: tuple[int, int, int] | None = None,
    outline_width: int = 2,
) -> None:
    """
    Draw a colored callout: a filled bulb at ``head_xy`` pointing toward
    ``tip_xy``.

    When ``thin_connector`` is False (default) a classical tangent-teardrop
    is drawn: the tail is a filled wedge bounded by the two tangent lines
    from the apex to the bulb.

    When ``thin_connector`` is True the tail becomes a thin straight line
    from the bulb edge toward the tip, stopping ``tail_setback_px`` short
    of the apex. This is the right mode for a cluster of callouts that
    all point at the same real address: N thin lines on N different
    radial headings can't overlap each other the way N filled wedges do.

    If ``outline_rgb`` is given, a slightly larger copy of the shape is
    drawn in that color first so the callout has a visible border.
    """
    r = radius if radius is not None else _head_radius(label)
    tx, ty = float(tip_xy[0]), float(tip_xy[1])
    hx, hy = float(head_xy[0]), float(head_xy[1])

    dx, dy = tx - hx, ty - hy
    dist = math.hypot(dx, dy)

    if dist < 1e-6:
        # Head sits exactly on tip (rare, only when canvas clamping
        # collapsed both to the same pixel). Point the tail straight up
        # so we still get a water-drop silhouette instead of a bare circle.
        ux, uy = 0.0, -1.0
    else:
        ux, uy = dx / dist, dy / dist           # unit vector head→tip
    apex_dist = max(r + 12, dist - tail_setback_px)

    def _draw_body(r_: int, rgb_: tuple[int, int, int]) -> None:
        fill_ = rgb_ + (255,)
        if thin_connector:
            line_start = (hx + ux * (r_ - 1), hy + uy * (r_ - 1))
            line_end   = (hx + ux * apex_dist, hy + uy * apex_dist)
            draw.line([line_start, line_end], fill=fill_, width=3 + 2 * (r_ - r))
            _draw_filled_circle(draw, line_end, 2 + (r_ - r), rgb_)
            _draw_filled_circle(draw, (hx, hy), r_, rgb_)
        else:
            px, py = -uy, ux
            apex_xy = (hx + ux * apex_dist, hy + uy * apex_dist)
            alpha = math.acos(max(-1.0, min(1.0, r_ / apex_dist)))
            left  = (hx + r_ * (math.cos(alpha) * ux + math.sin(alpha) * px),
                     hy + r_ * (math.cos(alpha) * uy + math.sin(alpha) * py))
            right = (hx + r_ * (math.cos(alpha) * ux - math.sin(alpha) * px),
                     hy + r_ * (math.cos(alpha) * uy - math.sin(alpha) * py))
            draw.polygon([apex_xy, right, (hx, hy), left], fill=fill_)
            _draw_filled_circle(draw, (hx, hy), r_, rgb_)

    if outline_rgb is not None:
        _draw_body(r + outline_width, outline_rgb)
    _draw_body(r, fill_rgb)

    if label:
        base_fs = int(r * 1.25) if len(label) == 1 else int(r * 1.05)
        font_size = max(6, min(int(r * 1.9), int(base_fs * label_scale)))
        font = _load_bold_font(font_size)
        try:
            bbox = draw.textbbox((0, 0), label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            ox = bbox[0]
            oy = bbox[1]
        except Exception:
            tw, th = draw.textsize(label, font=font)  # type: ignore[attr-defined]
            ox, oy = 0, 0
        draw.text(
            (hx - tw / 2 - ox, hy - th / 2 - oy),
            label, font=font, fill=(255, 255, 255, 255),
        )


def _draw_filled_circle(
    draw: "ImageDraw.ImageDraw",
    center: tuple[float, float],
    radius: int,
    fill_rgb: tuple[int, int, int],
) -> None:
    cx, cy = center
    bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
    draw.ellipse(bbox, fill=fill_rgb + (255,))


def _resolve_head_overlaps(
    placements: list,
    canvas_w: int,
    canvas_h: int,
    padding_px: float = 10.0,
    iterations: int = 60,
) -> list:
    """
    Nudge bulbs apart so no two overlap, even across clusters.

    Each placement is (marker, tip_xy, head_xy, radius). The bulb centred at
    ``head_xy`` with radius ``radius`` must not collide with any other bulb.
    We keep the tip anchored (true location), only move heads. After each
    sweep we clamp heads back inside the canvas.
    """
    if len(placements) < 2:
        return placements

    heads = [list(p[2]) for p in placements]
    tips = [p[1] for p in placements]
    radii = [p[3] for p in placements]

    for _ in range(iterations):
        moved_any = False
        # (1) Push heads apart from each other using full teardrop collision.
        for i in range(len(heads)):
            for j in range(i + 1, len(heads)):
                hi = (heads[i][0], heads[i][1])
                hj = (heads[j][0], heads[j][1])
                if not _teardrops_collide(tips[i], hi, radii[i],
                                          tips[j], hj, radii[j], padding_px):
                    continue
                moved_any = True
                dx = heads[j][0] - heads[i][0]
                dy = heads[j][1] - heads[i][1]
                dist = math.hypot(dx, dy)
                min_dist = radii[i] + radii[j] + padding_px
                if dist < 1e-6:
                    dx, dy, dist = 1.0, 0.0, 1.0
                overlap = max((min_dist - dist) / 2.0, 1.0)
                ux, uy = dx / dist, dy / dist
                heads[i][0] -= ux * overlap
                heads[i][1] -= uy * overlap
                heads[j][0] += ux * overlap
                heads[j][1] += uy * overlap

        # (2) Keep each head at least r + 20 px from its own tip, so the
        #     collision sweep above can't collapse a head onto its tip and
        #     leave the callout as a tail-less circle.
        for i, r in enumerate(radii):
            tx, ty = tips[i]
            dx = heads[i][0] - tx
            dy = heads[i][1] - ty
            d = math.hypot(dx, dy)
            min_d = r + 20
            if d < min_d:
                moved_any = True
                if d < 1e-6:
                    # Default: place head directly above the tip.
                    dx, dy, d = 0.0, -1.0, 1.0
                ux, uy = dx / d, dy / d
                heads[i][0] = tx + ux * min_d
                heads[i][1] = ty + uy * min_d

        # (2b) Pull each head back if it has been pushed too far from its tip.
        # The force-based nudge above is unconstrained; without this cap a head
        # in a dense cluster can drift 200 px from its pin producing absurdly
        # long tails.  5 × r gives ~90 px for a standard marker — generous
        # enough for any legitimate orbit-growth the rotation resolver needed.
        for i, r in enumerate(radii):
            tx, ty = tips[i]
            dx = heads[i][0] - tx
            dy = heads[i][1] - ty
            d = math.hypot(dx, dy)
            max_d = r * 5.0
            if d > max_d and d > 1e-6:
                moved_any = True
                ux, uy = dx / d, dy / d
                heads[i][0] = tx + ux * max_d
                heads[i][1] = ty + uy * max_d

        # (3) Clamp all heads inside the canvas.
        for i, r in enumerate(radii):
            pad = r + 6
            heads[i][0] = max(pad, min(canvas_w - pad, heads[i][0]))
            heads[i][1] = max(pad, min(canvas_h - pad, heads[i][1]))
        if not moved_any:
            break

    out = []
    for (m, tip_xy, _, r), head in zip(placements, heads):
        out.append((m, tip_xy, (head[0], head[1]), r))
    return out


def _pt_seg_closest(
    pt: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[float, float]:
    """Closest point on segment a-b to pt."""
    ax, ay = a; bx, by = b; px, py = pt
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom < 1e-12:
        return a
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return (ax + t * dx, ay + t * dy)


def _pt_seg_dist(
    pt: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    c = _pt_seg_closest(pt, a, b)
    return math.hypot(pt[0] - c[0], pt[1] - c[1])


def _teardrop_tangent_pts(
    tip: tuple[float, float],
    head: tuple[float, float],
    r: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """
    Return the two points where the tangent lines from `tip` touch the head
    circle (center=`head`, radius=`r`).  These are the outer corners of the
    teardrop triangle.
    """
    tx, ty = tip; hx, hy = head
    dx, dy = hx - tx, hy - ty
    D = math.hypot(dx, dy)
    if D < r + 1e-6:
        # Degenerate: tip inside circle — return diameter endpoints.
        vx, vy = (0.0, -1.0) if D < 1e-6 else (dx / D, dy / D)
        px, py = -vy, vx
        return ((hx + r * px, hy + r * py), (hx - r * px, hy - r * py))
    vx, vy = dx / D, dy / D   # unit vector tip → head
    px, py = -vy, vx           # perpendicular (left)
    sin_a = r / D
    cos_a = math.sqrt(max(0.0, 1.0 - sin_a * sin_a))
    bx = hx - r * sin_a * vx  # base point (slightly inside circle along axis)
    by = hy - r * sin_a * vy
    off = r * cos_a
    return ((bx + off * px, by + off * py),   # left tangent point
            (bx - off * px, by - off * py))   # right tangent point


def _seg_seg_intersect(
    a: tuple[float, float], b: tuple[float, float],
    c: tuple[float, float], d: tuple[float, float],
) -> bool:
    """True if segments AB and CD properly intersect (share an interior point)."""
    def _cross(ox, oy, ax, ay, bx, by) -> float:
        return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)
    ax, ay = a; bx, by = b; cx, cy = c; dx, dy = d
    d1 = _cross(cx, cy, dx, dy, ax, ay)
    d2 = _cross(cx, cy, dx, dy, bx, by)
    d3 = _cross(ax, ay, bx, by, cx, cy)
    d4 = _cross(ax, ay, bx, by, dx, dy)
    return (((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and
            ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)))


def _nearest_free_angle(
    current: float,
    blocked_arcs: list[tuple[float, float]],
) -> float | None:
    """
    Return the angle nearest to ``current`` (radians) not covered by any arc in
    ``blocked_arcs``.  Each arc (lo, hi) blocks the CCW sweep from lo to hi
    (hi - lo is the span; may exceed 2π to signal a full-circle block).
    Returns None when the entire circle is blocked.
    """
    TWO_PI = 2.0 * math.pi

    def _is_blocked(angle: float) -> bool:
        for lo, hi in blocked_arcs:
            span = hi - lo
            if span >= TWO_PI:
                return True
            if span <= 0:
                continue
            if ((angle - lo) % TWO_PI) < span:
                return True
        return False

    def _ang_dist(a: float, b: float) -> float:
        d = abs(a - b) % TWO_PI
        return d if d <= math.pi else TWO_PI - d

    if not _is_blocked(current):
        return current

    # The nearest free angle lies just past one of the arc boundary points.
    _EPS = 1e-6
    best: float | None = None
    best_d = float("inf")
    for lo, hi in blocked_arcs:
        for cand in (lo - _EPS, hi + _EPS):
            if not _is_blocked(cand):
                d = _ang_dist(cand, current)
                if d < best_d:
                    best_d = d
                    best = cand
    return best


def _teardrops_collide(
    tA: tuple[float, float], hA: tuple[float, float], rA: float,
    tB: tuple[float, float], hB: tuple[float, float], rB: float,
    gap: float,
) -> bool:
    """
    True if the two teardrop shapes are closer than `gap` pixels apart.

    Each teardrop is the convex hull of its tip point and head disk:
    visually a triangle with a circular cap, exactly matching the drawn
    marker shape.  The check covers all five overlap cases:
      1. Head circles too close (cap vs cap)
      2. Tip of A inside the exclusion zone of head B  (tip vs cap)
      3. Tip of B inside the exclusion zone of head A  (cap vs tip)
      4. A wedge side too close to head circle B  (side vs cap)
      5. B wedge side too close to head circle A  (cap vs side)
      6. Wedge sides cross each other             (side vs side)
    """
    # Case 1: head circles
    if math.hypot(hA[0] - hB[0], hA[1] - hB[1]) < rA + rB + gap:
        return True

    tlA, trA = _teardrop_tangent_pts(tA, hA, rA)
    tlB, trB = _teardrop_tangent_pts(tB, hB, rB)

    # Case 2-3: tip inside opposing head circle
    if math.hypot(tA[0] - hB[0], tA[1] - hB[1]) < rB + gap:
        return True
    if math.hypot(tB[0] - hA[0], tB[1] - hA[1]) < rA + gap:
        return True

    # Case 4: head B within `rB + gap` of either wedge side of A
    if _pt_seg_dist(hB, tA, tlA) < rB + gap:
        return True
    if _pt_seg_dist(hB, tA, trA) < rB + gap:
        return True

    # Case 5: head A within `rA + gap` of either wedge side of B
    if _pt_seg_dist(hA, tB, tlB) < rA + gap:
        return True
    if _pt_seg_dist(hA, tB, trB) < rA + gap:
        return True

    # Case 6: wedge sides cross (gap is ignored here — any crossing = overlap)
    if (_seg_seg_intersect(tA, tlA, tB, tlB) or
            _seg_seg_intersect(tA, tlA, tB, trB) or
            _seg_seg_intersect(tA, trA, tB, tlB) or
            _seg_seg_intersect(tA, trA, tB, trB)):
        return True

    return False


def _resolve_by_rotation(
    placements: list,
    canvas_w: int,
    canvas_h: int,
    padding_px: float = 10.0,
    max_iters: int = 300,
) -> list:
    """
    Pin every tip at its true address; rotate each head around its own tip
    until no two heads overlap and every head stays inside the canvas.

    Each head orbits its tip at a minimum radius of (2*r + 10), the natural
    standalone-teardrop length.  When rotation alone cannot separate two heads
    (tip_i is inside the exclusion zone of head_j), the orbit grows just
    enough to make a valid angle exist.

    Closed-form: angles where |tip_i + orbit*(cosθ,sinθ) − head_j| = sep
    satisfy cos(θ−φ) = (D² + orbit² − sep²) / (2·orbit·D), where
    D = |tip_i − head_j| and φ = atan2(head_j.y−tip_i.y, head_j.x−tip_i.x).
    """
    n = len(placements)
    if n < 2:
        return placements

    tips   = [p[1] for p in placements]
    radii  = [p[3] for p in placements]
    orbits = [float(r * 2 + 10) for r in radii]

    # Derive initial angles from the incoming head positions.
    angles = []
    for i in range(n):
        tx, ty = tips[i]
        hx, hy = placements[i][2]
        d = math.hypot(hx - tx, hy - ty)
        angles.append(math.atan2(hy - ty, hx - tx) if d > 1e-6 else -math.pi / 2)

    def _apply_cluster_spread(clusters: list[list[int]]) -> None:
        """Pre-size orbits and assign evenly-spaced angles for each cluster."""
        for group in clusters:
            if len(group) < 2:
                continue
            m_cnt = len(group)
            max_r  = max(radii[k] for k in group)
            min_sep = 2 * max_r + padding_px
            min_orbit = min_sep / (2.0 * math.sin(math.pi / m_cnt))
            for k in group:
                orbits[k] = max(orbits[k], min_orbit + 2.0)
            for rank, k in enumerate(group):
                angles[k] = -math.pi / 2 + 2 * math.pi * rank / m_cnt

    def _apply_natural_spread(groups: list[list[int]]) -> None:
        """Assign natural angles (away from group centroid) with minimum gap."""
        for _grp in groups:
            if len(_grp) < 2:
                continue
            _cx = sum(tips[_k][0] for _k in _grp) / len(_grp)
            _cy = sum(tips[_k][1] for _k in _grp) / len(_grp)
            _max_r_grp = max(radii[_k] for _k in _grp)
            _min_sep_grp = 2 * _max_r_grp + padding_px
            _orb_grp = max(
                max(orbits[_k] for _k in _grp),
                _min_sep_grp / (2.0 * math.sin(math.pi / len(_grp))) + 2.0,
            )
            _min_gap = (2 * math.asin(min(_min_sep_grp / 2.0 / _orb_grp, 1.0))
                        + math.radians(2.0))
            _raw: list[tuple[int, float]] = []
            _dup = 0
            for _k in _grp:
                _dx, _dy = tips[_k][0] - _cx, tips[_k][1] - _cy
                if math.hypot(_dx, _dy) > 2.0:
                    _raw.append((_k, math.atan2(_dy, _dx)))
                else:
                    _raw.append((_k, -math.pi / 2 + 2 * math.pi * _dup / len(_grp)))
                    _dup += 1
            _raw.sort(key=lambda x: x[1])
            _spr: list[float] = [_raw[0][1]]
            for _j in range(1, len(_raw)):
                _spr.append(max(_raw[_j][1], _spr[-1] + _min_gap))
            if len(_spr) > 1:
                _ov = (_spr[-1] - _spr[0]) - (2 * math.pi - _min_gap)
                if _ov > 0:
                    # Proportional compression shrinks consecutive gaps below
                    # _min_gap.  Use evenly-spaced clock positions instead so
                    # every adjacent pair starts at the correct separation.
                    _base = _spr[0]
                    _step = 2.0 * math.pi / len(_spr)
                    for _j in range(len(_spr)):
                        _spr[_j] = _base + _j * _step
            for _j, (_k, _) in enumerate(_raw):
                angles[_k] = _spr[_j]
                orbits[_k] = max(orbits[_k], _orb_grp)

    # Pass 1 — same-address pre-spread.
    # Group tips within 5 px of each other (handles the <1 px float-rounding
    # case without chaining geographically distinct markers).  Union-find is
    # safe at this small radius: the max cluster diameter is ~10 px.
    _CLUSTER_PX: float = 5.0
    _uf: list[int] = list(range(n))

    def _uf_find(x: int) -> int:
        while _uf[x] != x:
            _uf[x] = _uf[_uf[x]]
            x = _uf[x]
        return x

    for _a in range(n):
        for _b in range(_a + 1, n):
            if math.hypot(tips[_a][0] - tips[_b][0], tips[_a][1] - tips[_b][1]) < _CLUSTER_PX:
                ra, rb = _uf_find(_a), _uf_find(_b)
                if ra != rb:
                    _uf[ra] = rb

    _cluster_map: dict[int, list[int]] = {}
    for _a in range(n):
        _cluster_map.setdefault(_uf_find(_a), []).append(_a)
    _apply_cluster_spread(list(_cluster_map.values()))

    # Pass 2 — natural-angle initialisation with sort-and-spread.
    # Group all markers within 80 px via union-find, then initialise each group
    # with natural angles (head points away from group centroid) and a minimum
    # angular gap so no two heads start at nearly the same position.
    _LOCAL_PX: float = 80.0
    _luf: list[int] = list(range(n))

    def _luf_find(x: int) -> int:
        while _luf[x] != x:
            _luf[x] = _luf[_luf[x]]
            x = _luf[x]
        return x

    for _a in range(n):
        for _b in range(_a + 1, n):
            if math.hypot(tips[_a][0]-tips[_b][0], tips[_a][1]-tips[_b][1]) < _LOCAL_PX:
                _ra, _rb = _luf_find(_a), _luf_find(_b)
                if _ra != _rb:
                    _luf[_ra] = _rb

    _local_group_map: dict[int, list[int]] = {}
    for _a in range(n):
        _local_group_map.setdefault(_luf_find(_a), []).append(_a)
    _apply_natural_spread(list(_local_group_map.values()))

    def get_head(i: int) -> tuple[float, float]:
        return (
            tips[i][0] + orbits[i] * math.cos(angles[i]),
            tips[i][1] + orbits[i] * math.sin(angles[i]),
        )

    def adist(a: float, b: float) -> float:
        """Unsigned angular distance in [0, π]."""
        d = abs(a - b) % (2 * math.pi)
        return d if d <= math.pi else 2 * math.pi - d

    # Main loop: for each marker, compute all head-circle blocked arcs from every
    # other marker, then jump directly to the nearest free angle.  This avoids the
    # oscillation that plagues pairwise rotation (marker i escapes j, hits k,
    # escapes k, hits j again…) because each marker sees ALL constraints at once.
    for _it in range(max_iters):
        any_change = False
        for i in range(n):
            ti = tips[i]
            ri = radii[i]
            oi = orbits[i]

            blocked: list[tuple[float, float]] = []
            must_grow = False
            for j in range(n):
                if j == i:
                    continue
                hj = get_head(j)
                D  = math.hypot(ti[0] - hj[0], ti[1] - hj[1])
                sep = ri + radii[j] + padding_px + 1.0
                if D < 1e-6 or oi + D <= sep:
                    # Tip i is inside j's exclusion zone for every angle.
                    must_grow = True
                    break
                cos_v = (D * D + oi * oi - sep * sep) / (2.0 * oi * D)
                if cos_v <= -1.0:
                    # All angles on this orbit collide with j.
                    must_grow = True
                    break
                if cos_v >= 1.0:
                    continue  # j imposes no constraint at the current orbit
                phi = math.atan2(hj[1] - ti[1], hj[0] - ti[0])
                dlt = math.acos(cos_v)
                blocked.append((phi - dlt, phi + dlt))

            if must_grow:
                orbits[i] = max(orbits[i] * 1.3, orbits[i] + 4.0)
                any_change = True
                continue

            if not blocked:
                continue  # already clear of everyone

            best = _nearest_free_angle(angles[i], blocked)
            if best is None:
                # Every angle on this orbit is blocked → grow.
                orbits[i] = max(orbits[i] * 1.3, orbits[i] + 4.0)
                any_change = True
            elif adist(best, angles[i]) > 1e-6:
                angles[i] = best
                any_change = True

        if not any_change:
            break

    # Fine-tune pass: handle any remaining wedge / tip-in-circle collisions that
    # the head-circle arc-sweep above doesn't model.  A small number of pairwise
    # teardrop rotations is enough here because the main loop already placed every
    # head in a valid head-circle position; only subtle shape collisions remain.
    for _it in range(60):
        _chg = False
        for _i in range(n):
            for _j in range(_i + 1, n):
                _hi = get_head(_i); _hj = get_head(_j)
                _ti = tips[_i]; _tj = tips[_j]
                if not _teardrops_collide(_ti, _hi, radii[_i],
                                          _tj, _hj, radii[_j], padding_px):
                    continue
                _sep   = radii[_i] + radii[_j] + padding_px
                _sep_t = _sep + 1.0
                _tg    = math.hypot(_ti[0] - _tj[0], _ti[1] - _tj[1])
                if _tg < 3.0:
                    _rk, _ref, _tk = _i, _hj, _ti
                else:
                    _tlI, _trI = _teardrop_tangent_pts(_ti, _hi, radii[_i])
                    _tlJ, _trJ = _teardrop_tangent_pts(_tj, _hj, radii[_j])
                    _di = min(math.hypot(_hj[0] - _hi[0], _hj[1] - _hi[1]),
                              _pt_seg_dist(_hj, _ti, _tlI),
                              _pt_seg_dist(_hj, _ti, _trI))
                    _dj = min(math.hypot(_hi[0] - _hj[0], _hi[1] - _hj[1]),
                              _pt_seg_dist(_hi, _tj, _tlJ),
                              _pt_seg_dist(_hi, _tj, _trJ))
                    if _dj <= _di:
                        _rk, _ref, _tk = _i, _hj, _ti
                    else:
                        _rk, _ref, _tk = _j, _hi, _tj
                _chg = True
                _tx, _ty = _tk; _jx, _jy = _ref
                _D = math.hypot(_tx - _jx, _ty - _jy)
                if orbits[_rk] + _D < _sep_t:
                    orbits[_rk] = _sep_t - _D + 2.0
                if _D < 1e-6:
                    angles[_rk] += 0.2
                else:
                    _cv  = max(-1.0, min(1.0,
                        (_D*_D + orbits[_rk]*orbits[_rk] - _sep_t*_sep_t)
                        / (2.0*orbits[_rk]*_D)))
                    _phi = math.atan2(_jy - _ty, _jx - _tx)
                    _dlt = math.acos(_cv)
                    _a1, _a2 = _phi + _dlt, _phi - _dlt
                    angles[_rk] = (
                        _a1 if adist(_a1, angles[_rk]) <= adist(_a2, angles[_rk]) else _a2
                    )
        if not _chg:
            break

    # Keep heads inside canvas using orbit-preserving angle rotation.
    # For any head that falls outside [pad, canvas-pad] on either axis we solve
    # for the nearest angle on the same orbit circle that puts the head back in
    # bounds.  Clipping hx/hy independently (the old approach) breaks the orbit
    # radius and can push a freshly-separated head back into a neighbour.
    out = []
    for i, (m, tip_xy, _, r) in enumerate(placements):
        pad = r + 6
        hx, hy = get_head(i)

        if pad <= hx <= canvas_w - pad and pad <= hy <= canvas_h - pad:
            out.append((m, tip_xy, (hx, hy), r))
            continue

        tx, ty = tips[i]
        orbit = orbits[i]
        cur_angle = angles[i]
        candidates: list[tuple[float, float]] = []  # (angular_dist, theta)

        if orbit > 1e-6:
            # Left / right edges: solve tx + orbit*cos(θ) = edge_x
            for edge_x in (pad, canvas_w - pad):
                cv = (edge_x - tx) / orbit
                if -1.0 <= cv <= 1.0:
                    base = math.acos(max(-1.0, min(1.0, cv)))
                    for theta in (base, -base):
                        chx = tx + orbit * math.cos(theta)
                        chy = ty + orbit * math.sin(theta)
                        if pad <= chx <= canvas_w - pad and pad <= chy <= canvas_h - pad:
                            candidates.append((adist(theta, cur_angle), theta))

            # Top / bottom edges: solve ty + orbit*sin(θ) = edge_y
            for edge_y in (pad, canvas_h - pad):
                sv = (edge_y - ty) / orbit
                if -1.0 <= sv <= 1.0:
                    base = math.asin(max(-1.0, min(1.0, sv)))
                    for theta in (base, math.pi - base):
                        chx = tx + orbit * math.cos(theta)
                        chy = ty + orbit * math.sin(theta)
                        if pad <= chx <= canvas_w - pad and pad <= chy <= canvas_h - pad:
                            candidates.append((adist(theta, cur_angle), theta))

        if candidates:
            _, best_angle = min(candidates)
            angles[i] = best_angle
            hx = tx + orbit * math.cos(best_angle)
            hy = ty + orbit * math.sin(best_angle)
        else:
            # Orbit too large to fit within canvas — fall back to hard clamp.
            hx = max(pad, min(canvas_w - pad, hx))
            hy = max(pad, min(canvas_h - pad, hy))
            # Sync angles/orbits so get_head(i) reflects the clamped position;
            # without this, the post-clamp sweep below sees stale coordinates.
            _cdx, _cdy = hx - tx, hy - ty
            _cor = math.hypot(_cdx, _cdy)
            if _cor > 1e-6:
                orbits[i] = _cor
                angles[i] = math.atan2(_cdy, _cdx)

        out.append((m, tip_xy, (hx, hy), r))

    # Post-clamp blocked-arc sweep: canvas clamping (especially the hard-clamp
    # fallback) can bring two heads back into head-circle collision.  Run a
    # short arc-sweep pass to push them apart before returning.
    for _vit in range(60):
        _any_v = False
        for _i in range(n):
            _ti = tips[_i]; _ri = radii[_i]; _oi = orbits[_i]
            _blk: list[tuple[float, float]] = []
            _mg = False
            for _j in range(n):
                if _j == _i:
                    continue
                _hj = get_head(_j)
                _D  = math.hypot(_ti[0] - _hj[0], _ti[1] - _hj[1])
                _sp = _ri + radii[_j] + padding_px + 1.0
                if _D < 1e-6 or _oi + _D <= _sp:
                    _mg = True
                    break
                _cv = (_D * _D + _oi * _oi - _sp * _sp) / (2.0 * _oi * _D)
                if _cv <= -1.0:
                    _mg = True
                    break
                if _cv >= 1.0:
                    continue
                _ph = math.atan2(_hj[1] - _ti[1], _hj[0] - _ti[0])
                _dl = math.acos(max(-1.0, min(1.0, _cv)))
                _blk.append((_ph - _dl, _ph + _dl))
            if _mg:
                orbits[_i] = max(orbits[_i] * 1.3, orbits[_i] + 4.0)
                _any_v = True
                continue
            if not _blk:
                continue
            _bst = _nearest_free_angle(angles[_i], _blk)
            if _bst is None:
                orbits[_i] = max(orbits[_i] * 1.3, orbits[_i] + 4.0)
                _any_v = True
            elif adist(_bst, angles[_i]) > 1e-6:
                angles[_i] = _bst
                _any_v = True
        if not _any_v:
            break

    # Rebuild out with corrected positions, clamped to canvas.
    out = []
    for i, (m, tip_xy, _, r) in enumerate(placements):
        pad = r + 6
        hx, hy = get_head(i)
        hx = max(pad, min(canvas_w - pad, hx))
        hy = max(pad, min(canvas_h - pad, hy))
        out.append((m, tip_xy, (hx, hy), r))
    return out


# ---- Web Mercator projection ------------------------------------------

def _latlng_to_pixel(
    lat: float,
    lng: float,
    center_lat: float,
    center_lng: float,
    zoom: int,
    scale: int,
    width_px: int,
    height_px: int,
) -> tuple[float, float]:
    """
    Convert (lat, lng) to output-image pixel coordinates, matching the
    Google Static Maps canvas for the same center / zoom / scale / size.
    """
    def project(lat_: float, lng_: float) -> tuple[float, float]:
        siny = math.sin(lat_ * math.pi / 180.0)
        siny = max(-0.9999, min(0.9999, siny))
        x = 128.0 + lng_ * (256.0 / 360.0)
        y = 128.0 + 0.5 * math.log((1 + siny) / (1 - siny)) * -(256.0 / (2 * math.pi))
        return x, y

    world_scale = (2 ** zoom) * scale

    wx, wy = project(lat, lng)
    cx, cy = project(center_lat, center_lng)

    px = (wx - cx) * world_scale + width_px / 2.0
    py = (wy - cy) * world_scale + height_px / 2.0
    return px, py


# ---- Jitter for overlapping markers ----------------------------------

def _jitter_overlapping_markers(
    markers: list[MapMarker],
    cluster_radius_m: float = 120.0,
    spread_radius_m: float = 700.0,
    meters_per_pixel: float = 8.0,
) -> tuple[
    list[MapMarker],
    list[tuple[float, float, float, float]],
    list[int],
    dict[int, tuple[float, float]],
]:
    """
    Detect markers within `cluster_radius_m` of each other and spread them
    evenly on a circle around the cluster centroid, returning leader lines
    back to each marker's true location plus cluster membership data.

    Returns ``(new_markers, leaders, cluster_ids, cluster_anchors)``:

    - ``new_markers``: input list with overlapping pins replaced by copies
      placed on a circle around the shared anchor.
    - ``leaders``: list of ``(true_lat, true_lng, jittered_lat, jittered_lng)``
      for every displaced marker.
    - ``cluster_ids``: same length as ``markers``. Markers in a multi-member
      cluster share a non-negative id; singletons get ``-1``.
    - ``cluster_anchors``: maps cluster id → ``(anchor_lat, anchor_lng)``
      used for that cluster (subject-anchor if the cluster has a subject,
      else centroid).

    Does not mutate input markers.
    """
    n_markers = len(markers)
    cluster_ids = [-1] * n_markers
    cluster_anchors: dict[int, tuple[float, float]] = {}

    if n_markers < 2:
        return list(markers), [], cluster_ids, cluster_anchors

    parent = list(range(n_markers))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n_markers):
        for j in range(i + 1, n_markers):
            if _haversine_m(markers[i].lat, markers[i].lng, markers[j].lat, markers[j].lng) < cluster_radius_m:
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n_markers):
        clusters.setdefault(find(i), []).append(i)

    out = list(markers)
    leaders: list[tuple[float, float, float, float]] = []

    next_cluster_id = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        cid = next_cluster_id
        next_cluster_id += 1

        subject_i = next((i for i in members if markers[i].kind == "subject"), None)
        if subject_i is not None:
            anchor_lat = markers[subject_i].lat
            anchor_lng = markers[subject_i].lng
        else:
            anchor_lat = sum(markers[i].lat for i in members) / len(members)
            anchor_lng = sum(markers[i].lng for i in members) / len(members)
        cluster_anchors[cid] = (anchor_lat, anchor_lng)
        for i in members:
            cluster_ids[i] = cid

        movers = [i for i in members if i != subject_i]
        n = len(movers)
        # Size the spread ring so bulb centres are never closer than
        # 2r + 20 px to each other (bulb-separation rule).
        max_label_len = max(len(markers[i].label or "") for i in members)
        max_r_px = _head_radius("X" * max(max_label_len, 1))
        bulb_r_m = max_r_px * meters_per_pixel

        min_center_gap_m = 2 * bulb_r_m + 20.0 * meters_per_pixel
        bulb_sep_ring_m = min_center_gap_m / (2 * math.sin(math.pi / max(n, 2)))

        required_r = bulb_sep_ring_m
        r_m = max(spread_radius_m, required_r)
        lat_rad = math.radians(anchor_lat)
        m_per_deg_lat = 111_320.0
        m_per_deg_lng = 111_320.0 * max(math.cos(lat_rad), 1e-6)

        # Natural geographic angle for each mover: direction from anchor
        # to the marker's true address in metric space.  Fall back to
        # uniform distribution only for same-address duplicates (d < 1 m).
        raw_angles: list[float] = []
        for k, i in enumerate(movers):
            dlat_m = (markers[i].lat - anchor_lat) * m_per_deg_lat
            dlng_m = (markers[i].lng - anchor_lng) * m_per_deg_lng
            d = math.hypot(dlat_m, dlng_m)
            if d < 1.0:
                raw_angles.append(2 * math.pi * k / n - math.pi / 2)
            else:
                raw_angles.append(math.atan2(dlat_m, dlng_m))

        # Sort movers by natural angle, then enforce a minimum angular gap
        # so nearby directions don't place bulbs on top of each other.
        min_gap = 2 * math.asin(min(bulb_r_m / r_m, 1.0)) + math.radians(5.0)
        indexed = sorted(enumerate(raw_angles), key=lambda x: x[1])
        spread: list[float] = [indexed[0][1]]
        for j in range(1, len(indexed)):
            spread.append(max(indexed[j][1], spread[-1] + min_gap))
        # Compress back into one full circle if the spread overflows.
        if len(spread) > 1:
            overflow = (spread[-1] - spread[0]) - (2 * math.pi - min_gap)
            if overflow > 0:
                compress = overflow / (len(spread) - 1)
                for j in range(1, len(spread)):
                    spread[j] -= compress * j
        angle_map = {orig_k: spread[j] for j, (orig_k, _) in enumerate(indexed)}

        for k, i in enumerate(movers):
            angle = angle_map[k]
            dlat = (r_m * math.sin(angle)) / m_per_deg_lat
            dlng = (r_m * math.cos(angle)) / m_per_deg_lng
            new_lat = anchor_lat + dlat
            new_lng = anchor_lng + dlng
            true_lat = markers[i].lat
            true_lng = markers[i].lng
            out[i] = MapMarker(
                lat=new_lat,
                lng=new_lng,
                label=markers[i].label,
                kind=markers[i].kind,
            )
            leaders.append((true_lat, true_lng, new_lat, new_lng))
    return out, leaders, cluster_ids, cluster_anchors


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def compute_center_and_zoom(markers: list[MapMarker]) -> tuple[tuple[float, float], int]:
    """
    Unused: Google auto-fits bounds when no center/zoom is specified
    and markers are provided. Kept as a stub in case we need manual control.
    """
    if not markers:
        return (-33.6, 150.86), 13
    lats = [m.lat for m in markers]
    lngs = [m.lng for m in markers]
    return ((sum(lats) / len(lats), sum(lngs) / len(lngs)), 13)
