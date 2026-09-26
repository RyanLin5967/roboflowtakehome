"""Server-owned illustrative replay images, never presented as model output.

Detections are defined together with the drawing. They exercise infrastructure
and verifier decisions; they do not measure computer-vision accuracy.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

FIXTURE_VERSION = "counterpart-illustration/1.0.0"
WIDTH, HEIGHT = 960, 640
SCENARIOS = {"baseline", "counterpart", "wrong_relation", "camera_moved", "ambiguous", "recovery"}


def _box(x: float, y: float, width: float, height: float) -> list[float]:
    return [x - width / 2, y - height / 2, x + width / 2, y + height / 2]


def _detection(label: str, x: float, y: float, width: float, height: float) -> dict:
    return {"label": label, "confidence": 0.97, "box": _box(x, y, width, height)}


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _draw(detections: list[dict], recipe: str, timestamp: int, index: int) -> bytes:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "#ece8df")
    draw = ImageDraw.Draw(canvas)
    # Visible fixture labeling stays embedded in downloaded assets as well.
    draw.rectangle((0, 0, WIDTH, 64), fill="#222d29")
    draw.text((28, 17), "ILLUSTRATIVE REPLAY", fill="#f8f7f1", font=_font(24))
    draw.text((645, 22), "No model predictions", fill="#c6d6ca", font=_font(18))
    draw.line((26, 596, 934, 596), fill="#bfbcb2", width=1)
    draw.text((28, 609), "Counterpart / controlled fixture", fill="#62675f", font=_font(16))
    draw.text((752, 609), f"{timestamp / 1000:.1f}s · frame {index + 1:02d}", fill="#62675f", font=_font(16))
    if recipe == "temporal":
        boundary = int(0.62 * WIDTH)
        draw.rectangle((boundary, 76, 933, 575), fill="#dbe5d8")
        draw.line((boundary, 76, boundary, 575), fill="#87a17f", width=3)
        draw.text((boundary + 18, 85), "TARGET BAND", fill="#456342", font=_font(18))
        draw.line((28, 336, 931, 336), fill="#c9c6bc", width=2)
    for detection in detections:
        x1, y1, x2, y2 = (
            round(v * scale)
            for v, scale in zip(detection["box"], (WIDTH, HEIGHT, WIDTH, HEIGHT), strict=True)
        )
        width, height = x2 - x1, y2 - y1
        draw.ellipse((x1 - 5, y2 - 9, x2 + 15, y2 + 13), fill="#c4c1b8")
        if detection["label"] == "bottle":
            neck_left, neck_right = x1 + width * 0.30, x2 - width * 0.30
            draw.rounded_rectangle((neck_left, y1, neck_right, y1 + height * 0.2), radius=5, fill="#274d49")
            draw.polygon(
                [
                    (neck_left, y1 + height * 0.17),
                    (neck_right, y1 + height * 0.17),
                    (x2, y1 + height * 0.34),
                    (x1, y1 + height * 0.34),
                ],
                fill="#477f76",
            )
            draw.rounded_rectangle((x1, y1 + height * 0.28, x2, y2), radius=12, fill="#477f76")
            draw.rounded_rectangle(
                (x1 + width * 0.13, y1 + height * 0.46, x2 - width * 0.13, y1 + height * 0.72),
                radius=3,
                fill="#dce9d7",
            )
            draw.text((x1 + width * 0.2, y1 + height * 0.52), "A", font=_font(25), fill="#315b50")
        else:
            # The handle stays inside the declared illustrative bounding box.
            draw.ellipse(
                (x1 + width * 0.66, y1 + height * 0.2, x2, y1 + height * 0.81),
                outline="#a45c48",
                width=max(5, int(width * 0.08)),
            )
            draw.rounded_rectangle((x1, y1, x1 + width * 0.8, y2), radius=13, fill="#c87c63")
            draw.ellipse((x1, y1, x1 + width * 0.8, y1 + height * 0.2), fill="#e6ad91")
            draw.text((x1 + width * 0.26, y1 + height * 0.38), "B", font=_font(25), fill="#fff2df")
    buffer = BytesIO()
    canvas.save(buffer, format="JPEG", quality=88, optimize=True)
    return buffer.getvalue()


def generate(recipe: str, scenario: str) -> list[dict]:
    """Make deterministic illustrative assets and server-only observations.

    All metadata describes its provenance explicitly. A replay adapter may use
    ``detections`` but the live upload API must never accept these from clients.
    """
    if recipe not in ("spatial", "temporal") or scenario not in SCENARIOS:
        raise ValueError("Unknown illustrative replay recipe or scenario")
    observations = []
    if recipe == "spatial":
        moving_x = 0.79 if scenario in ("counterpart", "camera_moved", "ambiguous") else 0.21
        anchor_x = 0.64 if scenario == "camera_moved" else 0.50
        detections = [
            _detection("bottle", moving_x, 0.52, 0.13, 0.38),
            _detection("cup", anchor_x, 0.52, 0.17, 0.23),
        ]
        if scenario == "ambiguous":
            detections.append(_detection("bottle", 0.20, 0.52, 0.13, 0.38))
        observations.append((0, detections))
    elif scenario == "recovery":
        observations.append(
            (0, [_detection("bottle", 0.24, 0.32, 0.12, 0.25), _detection("cup", 0.24, 0.72, 0.16, 0.18)])
        )
    else:
        first = "bottle" if scenario in ("baseline", "wrong_relation") else "cup"
        first_xs = [0.24, 0.24, 0.44, 0.74, 0.74, 0.74, 0.74, 0.74, 0.74, 0.74]
        second_xs = [0.24, 0.24, 0.24, 0.24, 0.24, 0.44, 0.74, 0.74, 0.74, 0.74]
        shift_y = 0.09 if scenario == "camera_moved" else 0
        for index, (first_x, second_x) in enumerate(zip(first_xs, second_xs, strict=True)):
            bottle_x = first_x if first == "bottle" else second_x
            cup_x = first_x if first == "cup" else second_x
            detections = [
                _detection("bottle", bottle_x, 0.32 + shift_y, 0.12, 0.25),
                _detection("cup", cup_x, 0.72 + shift_y, 0.16, 0.18),
            ]
            if scenario == "ambiguous" and index == 4:
                detections.append(_detection("bottle", 0.47, 0.40, 0.12, 0.25))
            observations.append((index * 400, detections))
    return [
        {
            "bytes": _draw(detections, recipe, timestamp, index),
            "t_ms": timestamp,
            "width": WIDTH,
            "height": HEIGHT,
            "detections": detections,
            "model": {"provider": "fixture", "model_id": FIXTURE_VERSION},
            "provenance": {
                "kind": "illustrative_fixture",
                "fixture_version": FIXTURE_VERSION,
                "recipe": recipe,
                "scenario": scenario,
                "notice": "Generated illustrations and declared observations; not model predictions.",
            },
        }
        for index, (timestamp, detections) in enumerate(observations)
    ]
