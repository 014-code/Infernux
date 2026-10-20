"""UIImage — a rectangular image UI element.

Hierarchy:
    InxComponent → InxUIComponent → InxUIScreenComponent → UIImage
"""

from infernux.components import serialized_field, add_component_menu
from infernux.components.fields import FieldType
from .inx_ui_screen_component import InxUIScreenComponent
from .ui_sampled_texture import UISampledTextureField, ui_sampled_texture_source


@add_component_menu("UI/Image")
class UIImage(InxUIScreenComponent):
    """Screen or world image using a texture asset or live RenderTexture.

    Position and rotation come from the GameObject Transform. Width, height,
    opacity, corner radius and mirroring come from InxUIScreenComponent.
    """

    # ── Fill ──
    texture = UISampledTextureField(
        name="texture",
        tooltip="Texture or RenderTexture asset (drag from Project panel)",
    )
    uv_rect = serialized_field(
        default=[0.0, 0.0, 1.0, 1.0],
        field_type=FieldType.VEC4,
        tooltip="Texture region: normalized X, Y, Width, Height",
        group="Fill",
    )
    color: list = serialized_field(
        default=[1.0, 1.0, 1.0, 1.0],
        field_type=FieldType.COLOR,
        hdr=True,
        tooltip="Tint color (RGBA)",
        group="Fill",
    )

    def _image_texture_source(self):
        """Keep static image draws on the GPU asset cache, not CPU pixel loads."""
        return ui_sampled_texture_source(self, type(self).texture)

    def _deserialize_fields_document(self, data, **kwargs):
        if isinstance(data, dict):
            data = dict(data)
            data.pop("texture_path", None)
        super()._deserialize_fields_document(data, **kwargs)
