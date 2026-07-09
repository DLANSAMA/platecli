"""OrcaSlicer CLI argv assembly and subprocess execution."""

from __future__ import annotations

import argparse
import io
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING

from bambu_cli.logging_utils import logger

if TYPE_CHECKING:
    from bambu_cli.context import Settings


def _build_orcaslicer_cmd(
    settings: Settings,
    args: argparse.Namespace,
    machine: str,
    tmp_process_name: str,
    tmp_filament_name: str,
    outfile: str,
    outdir: str,
    copies: int,
    filepath: str,
) -> list[str]:
    """Assemble the OrcaSlicer CLI argv, prefixing xvfb/nice wrappers as needed."""
    cmd: list[str] = []
    if platform.system() == "Linux":
        # Prefer a real display (gives working GPU GL + thumbnails). Only fall back
        # to a virtual framebuffer when no display is present (e.g. headless/agent runs).
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        if not has_display:
            if shutil.which("xvfb-run"):
                cmd.extend(["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24 +extension GLX +render"])
            else:
                logger.warning(
                    "⚠️  No DISPLAY and xvfb-run not found; OrcaSlicer may fail headless. "
                    "Install it: `sudo pacman -S xorg-server-xvfb` (Arch/CachyOS) or `sudo apt install xvfb` (Debian/Ubuntu)."
                )

    cmd.extend(
        [
            settings.orca_slicer,
            "--load-settings",
            f"{machine};{tmp_process_name}",
            "--load-filaments",
            tmp_filament_name,
            "--slice",
            "0",
            "--export-3mf",
            outfile,
            "--outputdir",
            outdir,
        ]
    )
    if copies > 1:
        cmd.extend(["--arrange", "1"])
    if getattr(args, "threads", None) is not None:
        cmd.extend(["--threads", str(args.threads)])
    if sys.platform != "win32" and shutil.which("nice"):
        cmd = ["nice", "-n", "10"] + cmd
    cmd.extend([filepath] * copies)
    return cmd


def _run_orcaslicer(  # pragma: no cover -- external process + rich TTY UI; cmd_slice mocks at unit layer
    cmd: list[str],
    slicer_timeout: float,
    show_progress: bool,
    filepath: str,
) -> subprocess.CompletedProcess[str]:
    """Run OrcaSlicer, pumping stdout/stderr through an optional Rich progress bar.

    Raises subprocess.TimeoutExpired when the slice exceeds slicer_timeout, and
    propagates OSError from Popen. Returns a text CompletedProcess on exit.
    """
    import queue

    # Interactive visual feedback logging (A0530-UI-05)
    logger.info("   Running OrcaSlicer background worker...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    progress = None
    task_id = None
    try:
        if show_progress:
            from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

            progress = Progress(
                TextColumn("[bold blue]{task.description}", justify="right"),
                BarColumn(bar_width=None),
                "[progress.percentage]{task.percentage:>3.1f}%",
                "•",
                TimeElapsedColumn(),
                transient=True,
            )
            progress.start()
            task_id = progress.add_task(f"Slicing {os.path.basename(filepath)}", total=100)
    except ImportError:
        pass

    chunk_queue: queue.Queue[tuple[str, str]] = queue.Queue()

    def _pump(stream: io.BufferedReader, name: str) -> None:
        while True:
            chunk = stream.read1(4096)
            if not chunk:
                break
            chunk_queue.put((name, chunk.decode("utf-8", errors="replace")))

    readers = [
        threading.Thread(target=_pump, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, "stderr"), daemon=True),
    ]
    for t in readers:
        t.start()

    def _handle_stdout_line(line_str: str) -> None:
        pct_match = re.search(r"(\d+)%", line_str)
        if progress and task_id is not None and pct_match:
            progress.update(task_id, completed=int(pct_match.group(1)))
        if any(pat in line_str.lower() for pat in ("progress", "%", "slicing", "exporting")):
            logger.info(f"   [OrcaSlicer] {line_str}")
        elif line_str:
            logger.debug(f"   [OrcaSlicer] {line_str}")

    stdout_carry = ""

    def _consume(name: str, text: str) -> None:
        nonlocal stdout_carry
        if name == "stderr":
            stderr_lines.append(text)
            return
        stdout_lines.append(text)
        # OrcaSlicer emits progress lines terminated by \r as well as \n
        parts = re.split(r"[\r\n]", stdout_carry + text)
        stdout_carry = parts.pop()
        for part in parts:
            line_str = part.strip()
            if line_str:
                _handle_stdout_line(line_str)

    try:
        start_time = time.monotonic()
        while True:
            try:
                name, text = chunk_queue.get(timeout=0.5)
                _consume(name, text)
            except queue.Empty:
                pass
            if time.monotonic() - start_time > slicer_timeout:
                raise subprocess.TimeoutExpired(cmd, slicer_timeout)
            if proc.poll() is not None:
                alive = False
                for t in readers:
                    t.join(timeout=2)
                    alive = alive or t.is_alive()
                if not alive:
                    break
        # Final drain after both readers exit
        while True:
            try:
                name, text = chunk_queue.get_nowait()
            except queue.Empty:
                break
            _consume(name, text)
        if stdout_carry.strip():
            _handle_stdout_line(stdout_carry.strip())
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        if progress:
            progress.stop()
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    proc.wait()
    return subprocess.CompletedProcess(
        cmd,
        returncode=proc.returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )
