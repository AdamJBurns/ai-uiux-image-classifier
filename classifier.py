"""Core logic: image preparation, OpenAI vision naming, and safe renaming.

Kept GUI-free so it can be unit-tested or reused from a CLI.
"""

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from openai import OpenAI

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}

DEFAULT_MODEL = "gpt-4o-mini"

# Longest edge (px) the image is downscaled to before upload. Keeps the
# request small/cheap while leaving enough detail for the model to read UI.
MAX_UPLOAD_EDGE = 768

# Windows-illegal filename characters plus control chars.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Reserved device names on Windows that cannot be used as a base filename.
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

SYSTEM_PROMPT = (
    "You name UI/UX design example screenshots. "
    "Given an interface image, reply with ONLY a short, human-readable title "
    "(3 to 6 words) in Title Case that describes the screen. "
    "Capture, when visible: the screen/page type, the product domain, and a "
    "notable style or state. Do not include a file extension, quotes, or "
    "punctuation. "
    "Good examples: Mobile Login Screen, Ecommerce Checkout Dark Mode, "
    "Analytics Dashboard Overview, SaaS Pricing Page, Food Delivery App Home, "
    "Banking Transactions List. "
    "If the image is not a UI, briefly describe what it shows instead."
)


@dataclass
class RenamePlan:
    """A proposed rename for a single image."""

    source: Path
    suggested_title: str
    original_name: str = ""  # name at scan time (kept stable after rename)
    target_name: str = ""  # filename incl. extension, filled at planning time
    status: str = "pending"  # pending | ready | error | renamed | skipped
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.original_name:
            self.original_name = self.source.name


@dataclass
class UndoRecord:
    """Stores a batch of applied renames so they can be reverted."""

    folder: str
    timestamp: float
    moves: list = field(default_factory=list)  # list of [new_path, old_path]


def list_images(folder: str) -> list[Path]:
    """Return supported image files directly inside ``folder`` (sorted)."""
    root = Path(folder)
    if not root.is_dir():
        return []
    files = [
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(files, key=lambda p: p.name.lower())


def encode_image_for_upload(path: Path, max_edge: int = MAX_UPLOAD_EDGE) -> str:
    """Load, auto-orient, downscale and JPEG-encode an image as base64.

    Returns a base64 string (no data-URL prefix).
    """
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, format="JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def sanitize_title(raw: str, fallback: str = "Untitled Design") -> str:
    """Turn a model response into a clean, filesystem-safe base filename."""
    text = (raw or "").strip().strip("\"'`")
    # Drop a trailing extension the model may have added.
    text = re.sub(r"\.(png|jpe?g|webp|gif|bmp|tiff?)$", "", text, flags=re.I)
    text = _ILLEGAL_FILENAME_CHARS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    # Cap length so names stay tidy.
    if len(text) > 80:
        text = text[:80].rsplit(" ", 1)[0].strip()
    if not text or text.lower() in _RESERVED_NAMES:
        text = fallback
    return text


def suggest_title(
    client: OpenAI,
    path: Path,
    model: str = DEFAULT_MODEL,
    retries: int = 2,
) -> str:
    """Ask the vision model for a descriptive title for one image."""
    b64 = encode_image_for_upload(path)
    data_url = f"data:image/jpeg;base64,{b64}"
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Name this UI/UX design example.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url, "detail": "low"},
                            },
                        ],
                    },
                ],
                max_tokens=30,
                temperature=0.2,
            )
            content = response.choices[0].message.content or ""
            return sanitize_title(content)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as status
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(str(last_error))


def build_unique_name(
    base_title: str,
    extension: str,
    used_lower: set[str],
) -> str:
    """Build a unique filename, appending ' 2', ' 3'... on collisions.

    ``used_lower`` holds already-claimed names (lowercased) and is updated.
    """
    ext = extension.lower()
    candidate = f"{base_title}{ext}"
    if candidate.lower() not in used_lower:
        used_lower.add(candidate.lower())
        return candidate
    counter = 2
    while True:
        candidate = f"{base_title} {counter}{ext}"
        if candidate.lower() not in used_lower:
            used_lower.add(candidate.lower())
            return candidate
        counter += 1


def apply_renames(plans: list[RenamePlan]) -> UndoRecord:
    """Rename files on disk for plans marked ``ready``.

    Uses a two-phase move (via temporary names) so that swaps/cycles and
    case-only changes work safely. Returns an :class:`UndoRecord`.
    """
    ready = [p for p in plans if p.status == "ready" and p.target_name]
    record = UndoRecord(
        folder=str(ready[0].source.parent) if ready else "",
        timestamp=time.time(),
    )
    # Each entry: (plan, temp_path, final_path)
    temp_moves: list[tuple[RenamePlan, Path, Path]] = []
    stamp = int(time.time() * 1000)
    for index, plan in enumerate(ready):
        final_path = plan.source.parent / plan.target_name
        if final_path == plan.source:
            plan.status = "skipped"
            continue
        temp_path = plan.source.parent / f".__rename_{stamp}_{index}.tmp"
        try:
            os.replace(plan.source, temp_path)
            temp_moves.append((plan, temp_path, final_path))
        except OSError as exc:
            plan.status = "error"
            plan.error = f"Move failed: {exc}"

    for plan, temp_path, final_path in temp_moves:
        orig = plan.source
        try:
            os.replace(temp_path, final_path)
            record.moves.append([str(final_path), str(orig)])
            plan.status = "renamed"
            plan.source = final_path
        except OSError as exc:
            # Roll this file back to its original name.
            try:
                os.replace(temp_path, orig)
            except OSError:
                pass
            plan.status = "error"
            plan.error = f"Rename failed: {exc}"
    return record


def undo_renames(record: UndoRecord) -> int:
    """Revert a previously applied batch. Returns count restored."""
    restored = 0
    for new_path_str, old_path_str in reversed(record.moves):
        new_path = Path(new_path_str)
        old_path = Path(old_path_str)
        if new_path.exists() and not old_path.exists():
            try:
                os.replace(new_path, old_path)
                restored += 1
            except OSError:
                pass
    return restored


# --- lightweight config persistence (API key + preferences) ---------------

def _config_path() -> Path:
    base = Path.home() / ".ui_image_classifier"
    base.mkdir(parents=True, exist_ok=True)
    return base / "config.json"


def load_config() -> dict:
    path = _config_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config: dict) -> None:
    try:
        _config_path().write_text(
            json.dumps(config, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
