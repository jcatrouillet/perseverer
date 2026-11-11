"""Reads the handwritten workout notes in a photo (a gym logbook page) into a list of exercises and
sets, through Claude's vision. This is the only place a photo leaves the server: it is sent to the
Anthropic API when the athlete asks for it, never automatically, and only with an API key set in
the configuration (`PERSEVERER_ANTHROPIC_API_KEY`). Without a key the athlete types the sets in
instead.

The result is a *suggestion*: the athlete corrects it, matches the exercises to the exercise
library and orders them before anything is stored as sets (see logbook_photos.py).
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, Literal

import anthropic
from anthropic.types.beta import BetaMessageParam
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# The API takes images up to 5 MB and works best at about 1.5 megapixels: a phone photo is
# resized before it is sent, which also drops its metadata (location included).
MAX_READ_SIDE_PX = 1600
MAX_SOURCE_PIXELS = 60_000_000  # refuse a decompression bomb before decoding it


class DecodedSet(BaseModel):
    reps: int | None = Field(default=None, description="Repetitions, null when not written.")
    weight: float | None = Field(default=None, description="Weight as written, null if none.")
    unit: Literal["kg", "lb"] | None = Field(
        default=None, description="Unit written next to the weight, null when not written."
    )
    duration_s: float | None = Field(
        default=None, description="Seconds, for a timed set such as a plank; else null."
    )


class DecodedExercise(BaseModel):
    name: str = Field(description="The exercise name as written, in the writer's own words.")
    sets: list[DecodedSet] = Field(description="One entry per set, in the order done.")
    note: str | None = Field(default=None, description="Any remark written beside it.")


class DecodedLogbook(BaseModel):
    legible: bool = Field(description="False when the photo holds no readable workout notes.")
    transcript: str = Field(description="All the text on the page, line by line, as written.")
    exercises: list[DecodedExercise] = Field(
        description="The exercises in the order they are written on the page."
    )


PROMPT = """This is a photo of handwritten workout notes: a gym logbook page listing the exercises \
of one strength or HIIT session with their sets, repetitions and weights.

Read it and return:
- `transcript`: every line of text on the page, as written.
- `exercises`: the exercises in the order they appear on the page. For each, `name` is the \
exercise as the writer wrote it (do not rename or translate it), and `sets` has one entry per set. \
Expand shorthand: "3x10 @ 20" is three sets of 10 repetitions at 20; "10/8/6" with one weight is \
three sets; a list of weights with the same repetitions is one set per weight. Put a weight in \
`weight` with its `unit` (kg or lb) only when it is written; leave `unit` null when no unit is \
written. A timed exercise (a plank, a hold) has `duration_s`.
- Use null for anything not written or not legible. Never guess a value to fill a gap; if a number \
is ambiguous, leave it null and say so in `note`.
- If the photo has no readable workout notes, set `legible` to false and return no exercises."""


def prepare_image_for_reading(content: bytes) -> tuple[bytes, str]:
    """The photo as a JPEG the API accepts: orientation applied, at most MAX_READ_SIDE_PX on its
    long side, metadata removed. Raises ValueError for something that is not a readable image."""
    try:
        opened = Image.open(io.BytesIO(content))
        if opened.width * opened.height > MAX_SOURCE_PIXELS:
            raise ValueError("the image is too large")
        image = ImageOps.exif_transpose(opened).convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("the file is not a readable image") from exc
    image.thumbnail((MAX_READ_SIDE_PX, MAX_READ_SIDE_PX))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=88)
    return out.getvalue(), "image/jpeg"


def decode_logbook_photo(
    content: bytes, *, api_key: str, model: str, client: Any | None = None
) -> DecodedLogbook:
    """Sends the photo to Claude and returns what it read. Raises anthropic.APIError (or a
    subclass) when the call fails and ValueError when the file is not an image or the model
    returned nothing parseable. `client` is injectable for tests."""
    image_bytes, _media_type = prepare_image_for_reading(content)
    api = client or anthropic.Anthropic(api_key=api_key)
    message: BetaMessageParam = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(image_bytes).decode("ascii"),
                },
            },
            {"type": "text", "text": PROMPT},
        ],
    }
    response = api.beta.messages.parse(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_format=DecodedLogbook,
        messages=[message],
    )
    if response.stop_reason == "refusal":
        raise ValueError("the model declined to read this image")
    parsed = response.parsed_output
    if parsed is None:
        raise ValueError("the model returned no readable result")
    assert isinstance(parsed, DecodedLogbook)
    return parsed
