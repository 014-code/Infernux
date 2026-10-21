# UIText

<div class="class-info">
class in <b>infernux.ui</b>
</div>

**Inherits from:** `InxUIScreenComponent`

## Description

Figma-style text label rendered with ImGui draw primitives.

Inherits ``width`` and ``height`` from ``InxUIScreenComponent``; position
and rotation come from the GameObject Transform.

Attributes:
    text: Display string.
    font: Optional imported Font asset (``.ttf`` / ``.otf``), persisted by GUID.
    fallback_fonts: Ordered fallback Font assets, persisted by GUID.
    font_size: Font size in canvas pixels.
    line_height: Line height multiplier.
    letter_spacing: Extra letter spacing in pixels.
    text_align_h: Horizontal text alignment.
    text_align_v: Vertical text alignment.
    overflow: Text overflow mode.
    resize_mode: How the clipping box resizes with content.
    color: Text color as ``[R, G, B, A]`` (0–1 each).

Example::

    text = game_object.add_component(UIText)
    text.text = "Hello World"
    text.font_size = 32.0
    text.color = [1.0, 0.8, 0.0, 1.0]

<!-- USER CONTENT START --> description
`rich_text` enables nested `<color=#RRGGBB>` / `<color=#RRGGBBAA>`,
`<size=32>` / `<size=125%>` and `<br>` tags. Unsupported tags are displayed literally.
Sizes and character offsets use logical UI pixels; Canvas scaling is applied during rendering.
<!-- USER CONTENT END -->

## Properties

| Name | Type | Description |
|------|------|------|
| text | `str` |  |
| rich_text | `bool` |  |
| font | `FontAssetInfo | None` |  |
| fallback_fonts | `list[FontAssetInfo | None]` |  |
| font_size | `float` |  |
| line_height | `float` |  |
| letter_spacing | `float` |  |
| text_align_h | `TextAlignH` |  |
| text_align_v | `TextAlignV` |  |
| overflow | `TextOverflow` |  |
| resize_mode | `TextResizeMode` |  |
| color | `list` |  |
| max_visible_characters | `int` |  |

<!-- USER CONTENT START --> properties

<!-- USER CONTENT END -->

## Public Methods

| Method | Description |
|------|------|
| `get_text_layout() → TextLayout` |  |
| `create_character_buffer() → TextCharacterBuffer` |  |
| `set_character_data(buffer: TextCharacterBuffer, layer: TextGeometryLayer | None = None) → None` |  |
| `reset_character_data(layer: TextGeometryLayer | None = None) → None` |  |
| `is_auto_width() → bool` | Return ``True`` if resize mode is ``AutoWidth``. |
| `is_auto_height() → bool` | Return ``True`` if resize mode is ``AutoHeight``. |
| `is_fixed_size() → bool` | Return ``True`` if resize mode is ``FixedSize``. |
| `get_wrap_width() → float` | Return the wrap width for text layout (0 = no wrap). |
| `resolve_text_layout(measure_text: Callable[[str, float, float, str, float, float], Tuple[float, float]], scale: float = ...) → bool` | Resolve the transient intrinsic size in logical canvas pixels. |
| `get_resolved_size() → Tuple[float, float]` | Return the effective box shared by layout, drawing, and input. |
| `is_width_editable() → bool` | Return ``True`` if width can be manually edited (not AutoWidth). |
| `is_height_editable() → bool` | Return ``True`` if height can be manually edited (not AutoHeight). |

<!-- USER CONTENT START --> public_methods
`get_text_layout()` returns a cached, read-only snapshot of lines, words, grapheme
clusters and glyphs. Source ranges are UTF-8 byte offsets into the authored text,
including rich-text tags. A combining sequence is one character; a word is a run
of non-whitespace graphemes, not a language-specific token.

`create_character_buffer()` returns writable NumPy views: `offsets[N,2]`,
`rotations[N]` in degrees, `scales[N,2]`, `skews[N]`, `vertex_offsets[N,4,2]` and
`colors[N,4,4]`. Corners are top-left, top-right, bottom-right, bottom-left.
Colors multiply the text/style color. Commit changes with `set_character_data()`;
the state is transient and is not written to scene files. Recreate a buffer after
changing the text or rich-text mode. Font and box changes retain character identity.

Layers compose in this order: Manual → Effects → Appearance. Reset a layer with
`reset_character_data(layer)`, or reset all layers by omitting the argument.
`max_visible_characters` counts graphemes, including whitespace; `-1` shows all.
<!-- USER CONTENT END -->

## Example

<!-- USER CONTENT START --> example
```python
from infernux.ui import TextGeometryLayer

# label is an existing UIText component.
label.rich_text = True
label.text = "<color=#ef5858>Infernux</color> · 逐字动画"
characters = label.create_character_buffer()
characters.offsets[:, 1] = 6.0
characters.rotations[:] = 8.0
label.set_character_data(characters, TextGeometryLayer.Effects)
label.max_visible_characters = 8

# Restore the text after the effect.
label.reset_character_data(TextGeometryLayer.Effects)
label.max_visible_characters = -1
```

Character effects share the native font atlas and work on Canvas and world-space
text. This API provides grapheme-safe indexing; it does not add complex-script
shaping, bidirectional layout or color-emoji rendering.
<!-- USER CONTENT END -->

## See Also

<!-- USER CONTENT START --> see_also

<!-- USER CONTENT END -->
