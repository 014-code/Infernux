# UIText

<div class="class-info">
类位于 <b>infernux.ui</b>
</div>

**继承自:** `InxUIScreenComponent`

## 描述

UI 文本组件。在屏幕上显示文字。

<!-- USER CONTENT START --> description
启用 `rich_text` 后，支持嵌套的 `<color=#RRGGBB>` / `<color=#RRGGBBAA>`、
`<size=32>` / `<size=125%>` 和 `<br>` 标记。不支持的标记按原文显示。
字号和字符偏移使用 UI 逻辑像素，Canvas 缩放在渲染时统一应用。
<!-- USER CONTENT END -->

## 属性

| 名称 | 类型 | 描述 |
|------|------|------|
| text | `str` | 显示的文本内容。 |
| rich_text | `bool` |  |
| font | `FontAssetInfo | None` |  |
| fallback_fonts | `list[FontAssetInfo | None]` |  |
| font_size | `float` | 字体大小。 |
| line_height | `float` |  |
| letter_spacing | `float` |  |
| text_align_h | `TextAlignH` |  |
| text_align_v | `TextAlignV` |  |
| overflow | `TextOverflow` |  |
| resize_mode | `TextResizeMode` |  |
| color | `list` | 文本颜色。 |
| max_visible_characters | `int` |  |

<!-- USER CONTENT START --> properties

<!-- USER CONTENT END -->

## 公共方法

| 方法 | 描述 |
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
`get_text_layout()` 返回缓存的只读布局快照，包含行、词、字素和字形。
源文本范围使用包含富文本标记的原文 UTF-8 字节偏移。组合字符序列按一个字符计数；
词是连续的非空白字素，不是按特定语言分词。

`create_character_buffer()` 返回可写的 NumPy 视图：`offsets[N,2]`、
以度为单位的 `rotations[N]`、`scales[N,2]`、`skews[N]`、`vertex_offsets[N,4,2]`
和 `colors[N,4,4]`。四角顺序为左上、右上、右下、左下，颜色与文本/样式颜色相乘。
修改后调用 `set_character_data()` 提交；字符状态是临时数据，不写入场景文件。
修改文本或富文本模式后需要重新创建缓冲区；字体和文本框变化保留字符身份。

层叠顺序为 Manual → Effects → Appearance。`reset_character_data(layer)` 重置一层，
省略参数则重置全部。`max_visible_characters` 按字素计数，包含空白，`-1` 表示全部显示。
<!-- USER CONTENT END -->

## 示例

<!-- USER CONTENT START --> example
```python
from infernux.ui import TextGeometryLayer

# label 是已有的 UIText 组件。
label.rich_text = True
label.text = "<color=#ef5858>Infernux</color> · 逐字动画"
characters = label.create_character_buffer()
characters.offsets[:, 1] = 6.0
characters.rotations[:] = 8.0
label.set_character_data(characters, TextGeometryLayer.Effects)
label.max_visible_characters = 8

# 效果结束后恢复。
label.reset_character_data(TextGeometryLayer.Effects)
label.max_visible_characters = -1
```

字符效果复用原生字体图集，适用于 Canvas 和世界空间文字。该接口保证按字素索引，
不代表新增了复杂文字塑形、双向排版或彩色 Emoji 渲染。
<!-- USER CONTENT END -->

## 另请参阅

<!-- USER CONTENT START --> see_also

<!-- USER CONTENT END -->
