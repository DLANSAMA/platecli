"""Thin command wrappers that delegate to focused packages."""


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


def cmd_preflight(args):
    """Check local install/config readiness without contacting printer."""
    from bambu_cli.setup_cmd import _cmd_preflight

    _cmd_preflight(args)


def cmd_config(args):
    """Show the effective config (redacted) or validate it locally."""
    from bambu_cli.setup_cmd import _cmd_config

    _cmd_config(args)


def cmd_job(args):
    """One-shot URL/local file workflow: download, slice, upload, optionally print.

    This is the composition root for the job pipeline: the orchestrator in
    ``bambu_cli.job`` sequences the stages but does not know who implements
    them, so the real handlers are wired in here and passed down.
    """
    from bambu_cli.commands.files import cmd_upload
    from bambu_cli.commands.print_cmd import cmd_print
    from bambu_cli.job import JobSteps, _cmd_job

    steps = JobSteps(
        download=cmd_download,
        slice=cmd_slice,
        upload=cmd_upload,
        print_=cmd_print,
    )
    return _cmd_job(args, steps)
