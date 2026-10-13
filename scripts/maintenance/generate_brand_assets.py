"""Generate the website, Hub and desktop icons from the editable engine SVG.

Run in the infernux environment. PNG/ICO consumers keep their existing paths;
the web SVG and outlined wordmark require no external fonts at runtime.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "python/infernux/resources/icons/icon.svg"
WEB = ROOT / "docs/assets"
BACKGROUND = (10, 12, 17)
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(svg: bytes, size: int, *, padding: float = 0.0) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(svg))
    if not renderer.isValid():
        raise ValueError("Invalid brand SVG")
    resolution = size * 4
    image = QImage(resolution, resolution, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = padding * resolution
    renderer.render(painter, QRectF(inset, inset, resolution - inset * 2, resolution - inset * 2))
    painter.end()
    pixels = Image.frombytes("RGBA", (resolution, resolution), bytes(image.constBits()))
    return pixels.resize((size, size), Image.Resampling.LANCZOS)


def opaque_icon(svg: bytes, size: int, *, padding: float = 0.0) -> Image.Image:
    foreground = render(svg, size, padding=padding)
    canvas = Image.new("RGBA", foreground.size, BACKGROUND + (255,))
    canvas.alpha_composite(foreground)
    return canvas.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT)


def outline(text: str, font: QFont) -> tuple[str, float]:
    path = QPainterPath()
    path.addText(0, 0, font, text)
    commands = []
    index = 0
    while index < path.elementCount():
        element = path.elementAt(index)
        if element.isMoveTo():
            commands.append(f"M{element.x:.3f} {element.y:.3f}")
        elif element.isLineTo():
            commands.append(f"L{element.x:.3f} {element.y:.3f}")
        elif element.type == QPainterPath.ElementType.CurveToElement:
            control = path.elementAt(index + 1)
            end = path.elementAt(index + 2)
            commands.append(
                f"C{element.x:.3f} {element.y:.3f} "
                f"{control.x:.3f} {control.y:.3f} {end.x:.3f} {end.y:.3f}"
            )
            index += 2
        index += 1
    return "".join(commands), path.boundingRect().right()


def wordmark(svg: bytes) -> bytes:
    font_path = ROOT / "packaging/resources/fonts/SpaceGrotesk-Bold.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        raise ValueError(f"Cannot load wordmark font: {font_path}")
    font = QFont(families[0])
    font.setPixelSize(64)
    font.setWeight(QFont.Weight.Bold)
    first, width = outline("INFERNUX", font)
    second, second_width = outline("ENGINE", font)
    text_x = 256
    engine_x = text_x + width + 20
    right = engine_x + second_width
    # Reuse the mark geometry verbatim. The lockup lettering is outlined so
    # viewers do not need Space Grotesk installed to see the intended shape.
    body = svg.decode("utf-8").split(">", 1)[1].rsplit("</svg>", 1)[0]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{right + 48:.3f}" height="288" '
        f'viewBox="0 0 {right + 48:.3f} 288" color="#151518" role="img" aria-label="Infernux Engine">\n'
        f'  <g transform="translate(0 16)">{body}</g>\n'
        f'  <path fill="#B33D49" d="{first}" transform="translate({text_x} 172)"/>\n'
        f'  <path fill="currentColor" d="{second}" transform="translate({engine_x:.3f} 172)"/>\n'
        f'  <path stroke="#B33D49" stroke-width="3" d="M{text_x} 195H{right:.3f}"/>\n'
        '</svg>\n'
    ).encode("utf-8")


def main() -> None:
    application = QGuiApplication.instance() or QGuiApplication([])
    svg = MASTER.read_bytes()
    ET.fromstring(svg)
    WEB.joinpath("logo.svg").write_bytes(svg)
    WEB.joinpath("logo-wordmark.svg").write_bytes(wordmark(svg))
    icon = render(svg, 256)
    buffer = io.BytesIO()
    icon.save(buffer, format="PNG", optimize=True)
    for target in (
        MASTER.with_suffix(".png"),
        MASTER.with_name("scene.png"),
        ROOT / "packaging/resources/icon.png",
        WEB / "logo.png",
    ):
        target.write_bytes(buffer.getvalue())
    icon.save(MASTER.with_suffix(".ico"), format="ICO", sizes=[(size, size) for size in SIZES])
    render(svg, 64).save(WEB / "favicon-64.png", format="PNG", optimize=True)
    for name, size, padding in (
        ("infernux-icon-192.png", 192, 0.0),
        ("infernux-icon-512.png", 512, 0.0),
        ("infernux-icon-maskable-512.png", 512, 0.14),
        ("infernux-apple-touch-icon.png", 180, 0.0),
    ):
        opaque_icon(svg, size, padding=padding).save(WEB / name, format="PNG", optimize=True)
    print("Generated brand SVG, outlined wordmark and 10 raster assets.")
    del application


if __name__ == "__main__":
    main()
