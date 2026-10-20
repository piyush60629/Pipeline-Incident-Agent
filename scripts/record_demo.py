"""Record a demo GIF of the Streamlit app: pick a real lakehouse failure, diagnose it, approve the rerun.

    streamlit run app.py &                       # with GROQ_API_KEY set
    python scripts/record_demo.py --out docs/demo.gif

Needs: pip install playwright && playwright install chromium, and ffmpeg. The GitHub workflow
"Record demo" runs exactly this with the repository secrets and commits the GIF.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

PLATFORM_LABEL = "Retail Analytics Lakehouse"
VIEWPORT = {"width": 1280, "height": 860}


def pause(page: Page, seconds: float) -> None:
    page.wait_for_timeout(int(seconds * 1000))


def smooth_scroll(page: Page, pixels: int, steps: int = 12) -> None:
    for _ in range(steps):
        page.mouse.wheel(0, pixels / steps)
        pause(page, 0.12)


def record(url: str, run_text: str, video_dir: Path, timeout_s: int) -> Path:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, record_video_dir=str(video_dir), record_video_size=VIEWPORT)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        page.get_by_text("Pipeline Incident Agent").first.wait_for(timeout=120_000)
        pause(page, 2)

        page.get_by_text(PLATFORM_LABEL).first.click()
        pause(page, 2)

        # Streamlit selectbox: open it and choose the failed run.
        page.locator('[data-testid="stSelectbox"]').first.click()
        pause(page, 0.8)
        page.get_by_role("option").filter(has_text=run_text).first.click()
        pause(page, 2.5)

        page.get_by_role("button", name="Diagnose").click()
        # Follow the investigation steps as they stream in.
        page.mouse.move(VIEWPORT["width"] * 0.6, VIEWPORT["height"] * 0.6)
        done = page.get_by_text("Diagnosis ready").first
        results = page.get_by_text("Result of ")
        deadline, shown = time.monotonic() + timeout_s, 0
        while not done.is_visible():
            if time.monotonic() > deadline:
                raise TimeoutError("No diagnosis within the timeout")
            count = results.count()
            if count > shown:  # scroll only when a new step appears (fewer full-frame changes = smaller GIF)
                results.nth(count - 1).scroll_into_view_if_needed()
                shown = count
            pause(page, 0.5)
        pause(page, 1.5)

        page.get_by_text("Root cause:").first.scroll_into_view_if_needed()
        pause(page, 4)
        smooth_scroll(page, 500)
        pause(page, 4)

        approve = page.get_by_role("button", name="Approve rerun")
        if approve.count():
            approve.scroll_into_view_if_needed()
            pause(page, 2)
            approve.click()
            page.get_by_text("Rerun queued").first.wait_for(timeout=60_000)
            page.get_by_text("Rerun queued").first.scroll_into_view_if_needed()
            pause(page, 4)

        video = Path(page.video.path())
        context.close()
        browser.close()
        return video


def to_gif(video: Path, out: Path, speed: float, width: int, fps: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    filters = f"setpts=PTS/{speed},fps={fps},scale={width}:-1:flags=lanczos"
    with tempfile.TemporaryDirectory() as tmp:
        palette = Path(tmp) / "palette.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                        "-vf", f"{filters},palettegen=max_colors=96:stats_mode=diff", str(palette)], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(palette),
                        "-lavfi", f"{filters} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
                        str(out)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--run-text", default="Gold layer", help="Text of the failed run to pick in the list")
    parser.add_argument("--out", type=Path, default=Path("docs/demo.gif"))
    parser.add_argument("--video-out", type=Path, help="Also keep the raw .webm video here")
    parser.add_argument("--speed", type=float, default=2.0, help="Playback speed of the GIF")
    parser.add_argument("--width", type=int, default=880)
    parser.add_argument("--fps", type=int, default=7)
    parser.add_argument("--timeout", type=int, default=240, help="Seconds to wait for the diagnosis")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        started = time.perf_counter()
        video = record(args.url, args.run_text, Path(tmp), args.timeout)
        if args.video_out:
            args.video_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(video, args.video_out)
        to_gif(video, args.out, args.speed, args.width, args.fps)
    size_mb = args.out.stat().st_size / 1_000_000
    print(f"Wrote {args.out} ({size_mb:.1f} MB) in {time.perf_counter() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
