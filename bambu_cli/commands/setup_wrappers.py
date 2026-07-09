"""Thin command wrappers that delegate to focused packages."""

from bambu_cli.context import RuntimeContext


def cmd_setup(args):
    """Interactive or non-interactive printer configuration setup."""
    from bambu_cli.setup_cmd import _cmd_setup

    _cmd_setup(args)


def cmd_download(args, **collaborators):
    """Download a model file from a remote URL.

    Optional keyword collaborators (``opener_factory``, ``resolve_printables``,
    ``noncolliding_path``) are forwarded to the download implementation for tests.
    """
    from bambu_cli.download.downloader import _cmd_download

    return _cmd_download(args, **collaborators)


def cmd_slice(args, **collaborators):
    """Slice a model with OrcaSlicer.

    Extra keyword args are forwarded to ``slicer.cmd_slice`` for injectable
    collaborators when the slicer accepts them.
    """
    from bambu_cli.slicer import cmd_slice as _cmd_slice

    if collaborators:
        return _cmd_slice(args, **collaborators)
    return _cmd_slice(args)


def cmd_snapshot(args, ctx=None, **collaborators):
    """Capture a camera snapshot using the RTSP Streamer Docker container.

    Extra keyword args are forwarded to ``camera._cmd_snapshot`` (injectable
    collaborators: grab_frame, which, subprocess_run, access_code_loader, …).
    """
    from bambu_cli.camera import _cmd_snapshot

    ctx = ctx or RuntimeContext.for_request(args)
    _cmd_snapshot(args, ctx=ctx, **collaborators)


def cmd_preflight(args):
    """Check local install/config readiness without contacting printer."""
    from bambu_cli.setup_cmd import _cmd_preflight

    _cmd_preflight(args)


def cmd_config(args):
    """Show the effective config (redacted) or validate it locally."""
    from bambu_cli.setup_cmd import _cmd_config

    _cmd_config(args)


def cmd_job(args):
    """One-shot URL/local file workflow: download, slice, upload, optionally print."""
    from bambu_cli.job import _cmd_job

    return _cmd_job(args)
