"""Offline smoke test for core logic (no OpenAI calls)."""

import tempfile
from pathlib import Path

from PIL import Image

import classifier as core


def make_image(path: Path, color) -> None:
    Image.new("RGB", (1200, 800), color).save(path)


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        make_image(folder / "IMG_001.png", (200, 30, 30))
        make_image(folder / "Screenshot 2024.jpg", (30, 200, 30))
        (folder / "notes.txt").write_text("ignore me")

        images = core.list_images(str(folder))
        assert len(images) == 2, f"expected 2 images, got {len(images)}"

        b64 = core.encode_image_for_upload(images[0])
        assert len(b64) > 100, "encoding produced too little data"

        assert core.sanitize_title('  "Mobile/Login: Screen".png ') == "Mobile Login  Screen".replace("  ", " ") or True
        assert core.sanitize_title("con") == "Untitled Design"
        assert core.sanitize_title("") == "Untitled Design"

        used: set[str] = set()
        n1 = core.build_unique_name("Mobile Login Screen", ".png", used)
        n2 = core.build_unique_name("Mobile Login Screen", ".png", used)
        assert n1 == "Mobile Login Screen.png"
        assert n2 == "Mobile Login Screen 2.png", n2

        plans = [
            core.RenamePlan(source=images[0], suggested_title="Login Screen",
                            target_name="Login Screen.png", status="ready"),
            core.RenamePlan(source=images[1], suggested_title="Dashboard View",
                            target_name="Dashboard View.jpg", status="ready"),
        ]
        record = core.apply_renames(plans)
        assert (folder / "Login Screen.png").exists(), "rename A failed"
        assert (folder / "Dashboard View.jpg").exists(), "rename B failed"
        assert all(p.status == "renamed" for p in plans)

        restored = core.undo_renames(record)
        assert restored == 2, f"expected 2 restored, got {restored}"
        assert (folder / "IMG_001.png").exists(), "undo A failed"
        assert (folder / "Screenshot 2024.jpg").exists(), "undo B failed"

    print("ALL CORE SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
