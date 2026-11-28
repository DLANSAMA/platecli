"""The model on the plate, drawn in the terminal with half-block characters.

Each cell is one ``▀``: its foreground is the upper pixel and its background
the lower one, so a cell holds two square-ish pixels in 24-bit colour and the
picture needs no image support from the terminal. The pixels come from
``bambu_cli.mesh.render``, the same rasterizer as ``plate preview``'s PNG, so
the terminal and the PNG show the same pose with the same overhang tint.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from bambu_cli.mesh import Matrix, Mesh
from bambu_cli.mesh.render import BACKGROUND, RGB, Image, render

_HALF = "▀"


def half_blocks(image: Image) -> Text:
    """``image`` as rows of ``▀`` cells; an odd last row is paired with itself."""
    text = Text(no_wrap=True, overflow="crop")
    width, height = image.width, image.height
    for y in range(0, height, 2):
        lower_y = y + 1 if y + 1 < height else y
        run_style: Style | None = None
        run = 0
        for x in range(width):
            top = image.get(x, y)
            bottom = image.get(x, lower_y)
            style = Style(
                color=f"#{top[0]:02x}{top[1]:02x}{top[2]:02x}",
                bgcolor=f"#{bottom[0]:02x}{bottom[1]:02x}{bottom[2]:02x}",
            )
            if style == run_style:
                run += 1
                continue
            if run:
                text.append(_HALF * run, run_style)
            run_style, run = style, 1
        if run:
            text.append(_HALF * run, run_style)
        if y + 2 < height:
            text.append("\n")
    return text


def draw_model(
    mesh: Mesh, rotation: Matrix, columns: int, rows: int, *, view: str = "iso", background: RGB = BACKGROUND
) -> Text:
    """Render ``mesh`` in ``rotation`` to fit ``columns`` × ``rows`` cells."""
    columns, rows = max(columns, 2), max(rows, 1)
    return half_blocks(render(mesh, rotation, columns, rows * 2, view=view, background=background, grid=False))


def draw_views(
    mesh: Mesh,
    rotation: Matrix,
    columns: int,
    rows: int,
    views: tuple[tuple[str, str], ...] = (("iso", "from above"), ("front", "from the front")),
    gap: int = 2,
    *,
    background: RGB = BACKGROUND,
) -> Text:
    """Several views of ``mesh`` side by side in ``columns`` × ``rows`` cells, captioned on the last row."""
    picture_rows = max(rows - 1, 1)
    width = max(2, (columns - gap * (len(views) - 1)) // len(views))
    pictures = [
        draw_model(mesh, rotation, width, picture_rows, view=view, background=background).split("\n")
        for view, _label in views
    ]
    text = Text(no_wrap=True, overflow="crop")
    for row in range(min(len(lines) for lines in pictures)):
        for n, lines in enumerate(pictures):
            if n:
                text.append(" " * gap)
            text.append_text(lines[row])
        text.append("\n")
    caption = (" " * gap).join(label[:width].ljust(width) for _view, label in views)
    text.append(caption.rstrip(), style="dim")
    return text


class ModelView(Static):
    """Shows a pre-rendered picture of the model; the screen decides what and when to draw."""

    DEFAULT_CSS = """
    ModelView {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
    }
    """

    def cell_size(self) -> tuple[int, int]:
        """The cells available for the picture."""
        size = self.content_size
        return size.width, size.height

    def backdrop(self) -> RGB:
        """The colour behind the model: the renderer's own dark one, or this widget's in a light theme.

        The dark theme keeps the renderer's backdrop (the same picture as
        ``plate preview``); in a light theme that would be a black box on a
        pale screen, so the picture takes the colour it is drawn on.
        """
        if self.app.current_theme.dark:
            return BACKGROUND
        red, green, blue = self.background_colors[1].rgb
        return (red, green, blue)
