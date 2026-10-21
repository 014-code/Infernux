#include "InxTextGeometry.h"

#include <cmath>
#include <stdexcept>

namespace infernux::textlayout
{
namespace
{
constexpr float DegreesToRadians = 0.017453292519943295f;

size_t LayerIndex(TextGeometryLayer layer)
{
    const auto index = static_cast<size_t>(layer);
    if (index >= 3)
        throw std::invalid_argument("Unknown text geometry layer");
    return index;
}

TextCharacterBuffer::Row IdentityRow()
{
    TextCharacterBuffer::Row row{};
    row[3] = row[4] = 1.0f;
    std::fill(row.begin() + 14, row.end(), 1.0f);
    return row;
}

ImDrawVert InterpolateVertex(const ImDrawVert &a, const ImDrawVert &b, float t)
{
    const auto ac = ImGui::ColorConvertU32ToFloat4(a.col), bc = ImGui::ColorConvertU32ToFloat4(b.col);
    return {{a.pos.x + (b.pos.x - a.pos.x) * t, a.pos.y + (b.pos.y - a.pos.y) * t},
            {a.uv.x + (b.uv.x - a.uv.x) * t, a.uv.y + (b.uv.y - a.uv.y) * t},
            ImGui::ColorConvertFloat4ToU32({ac.x + (bc.x - ac.x) * t, ac.y + (bc.y - ac.y) * t,
                                            ac.z + (bc.z - ac.z) * t, ac.w + (bc.w - ac.w) * t})};
}

void EmitClippedTriangle(ImDrawList &draw, const ImDrawVert &a, const ImDrawVert &b, const ImDrawVert &c,
                         const std::array<float, 4> &bounds)
{
    // Clip the two original glyph triangles independently. Four-corner edits
    // may produce a concave quad; treating it as one convex polygon is wrong.
    std::array<ImDrawVert, 8> polygon{a, b, c}, output{};
    int count = 3;
    for (int edge = 0; edge < 4 && count > 0; ++edge) {
        const auto distance = [&](const ImDrawVert &vertex) {
            const float coordinate = edge % 2 == 0 ? vertex.pos.x : vertex.pos.y;
            return edge < 2 ? coordinate - bounds[edge] : bounds[edge] - coordinate;
        };
        int nextCount = 0;
        auto previous = polygon[count - 1];
        float previousDistance = distance(previous);
        for (int index = 0; index < count; ++index) {
            const auto current = polygon[index];
            const float currentDistance = distance(current);
            if ((currentDistance >= 0) != (previousDistance >= 0))
                output[nextCount++] =
                    InterpolateVertex(previous, current, previousDistance / (previousDistance - currentDistance));
            if (currentDistance >= 0)
                output[nextCount++] = current;
            previous = current;
            previousDistance = currentDistance;
        }
        polygon = output;
        count = nextCount;
    }
    if (count < 3)
        return;
    draw.PrimReserve((count - 2) * 3, count);
    const auto first = draw._VtxCurrentIdx;
    for (int index = 1; index + 1 < count; ++index) {
        draw.PrimWriteIdx(static_cast<ImDrawIdx>(first));
        draw.PrimWriteIdx(static_cast<ImDrawIdx>(first + index));
        draw.PrimWriteIdx(static_cast<ImDrawIdx>(first + index + 1));
    }
    for (int index = 0; index < count; ++index)
        draw.PrimWriteVtx(polygon[index].pos, polygon[index].uv, polygon[index].col);
}

void ClipGlyph(ImDrawList &draw, int firstVertex, const std::array<float, 4> &bounds)
{
    std::array<ImDrawVert, 4> quad;
    bool contained = true;
    for (int index = 0; index < 4; ++index) {
        quad[index] = draw.VtxBuffer[firstVertex + index];
        const auto &position = quad[index].pos;
        contained &=
            position.x >= bounds[0] && position.y >= bounds[1] && position.x <= bounds[2] && position.y <= bounds[3];
    }
    if (contained)
        return;
    draw.PrimUnreserve(6, 4);
    draw._VtxCurrentIdx -= 4;
    draw._VtxWritePtr = draw.VtxBuffer.Data + draw.VtxBuffer.Size;
    draw._IdxWritePtr = draw.IdxBuffer.Data + draw.IdxBuffer.Size;
    EmitClippedTriangle(draw, quad[0], quad[1], quad[2], bounds);
    EmitClippedTriangle(draw, quad[0], quad[2], quad[3], bounds);
}
} // namespace

TextCharacterBuffer::TextCharacterBuffer(std::shared_ptr<const std::string> content, size_t count)
    : m_content(std::move(content)), m_rows(count, IdentityRow())
{
}

void TextCharacterBuffer::Reset()
{
    std::fill(m_rows.begin(), m_rows.end(), IdentityRow());
}

TextGeometry::TextGeometry(TextLayoutParams params)
{
    SetLayout(std::move(params));
}

void TextGeometry::SetLayout(TextLayoutParams params)
{
    auto layout = CreateTextLayout(params);
    if (!m_content || *m_content != params.text || m_params.richText != params.richText) {
        m_content = std::make_shared<const std::string>(params.text);
        m_layers = {};
        m_stateDirty = true;
        m_visibleCharacters = -1;
    }
    m_params = std::move(params);
    m_layout = std::move(layout);
    m_fontGeneration = FontCacheGeneration();
    m_preparedScale = 0.0f;
    ++m_layoutRevision;
    ++m_revision;
}

void TextGeometry::EnsureLayout()
{
    if (m_fontGeneration != FontCacheGeneration())
        SetLayout(m_params);
}

std::shared_ptr<TextLayoutResult> TextGeometry::GetLayout()
{
    EnsureLayout();
    return m_layout;
}

std::shared_ptr<TextCharacterBuffer> TextGeometry::CreateCharacterBuffer()
{
    EnsureLayout();
    return std::make_shared<TextCharacterBuffer>(m_content, m_layout->characters.size());
}

void TextGeometry::SetCharacterData(const std::shared_ptr<TextCharacterBuffer> &buffer, TextGeometryLayer layer)
{
    const size_t index = LayerIndex(layer);
    if (!buffer || buffer->m_content != m_content)
        throw std::invalid_argument("Character buffer belongs to a different text or geometry");
    m_layers[index] = buffer;
    m_stateDirty = true;
    ++m_revision;
}

void TextGeometry::ResetCharacterData(int layer)
{
    if (layer == -1) {
        m_layers = {};
    } else {
        const size_t index = LayerIndex(static_cast<TextGeometryLayer>(layer));
        m_layers[index].reset();
    }
    m_stateDirty = true;
    ++m_revision;
}

void TextGeometry::CompileCharacters()
{
    if (!m_stateDirty)
        return;
    m_compiled.assign(m_layout->characters.size(), {});
    m_colorScale = 1.0f;
    for (size_t index = 0; index < m_compiled.size(); ++index) {
        auto &state = m_compiled[index];
        for (const auto &layer : m_layers) {
            if (!layer)
                continue;
            const auto &row = layer->m_rows[index];
            const float radians = row[2] * DegreesToRadians;
            const float cosine = std::cos(radians), sine = std::sin(radians);
            const float xx = cosine * row[3], xy = (cosine * row[5] - sine) * row[4];
            const float yx = sine * row[3], yy = (sine * row[5] + cosine) * row[4];
            const CharacterState previous = state;
            state.xx = xx * previous.xx + xy * previous.yx;
            state.xy = xx * previous.xy + xy * previous.yy;
            state.yx = yx * previous.xx + yy * previous.yx;
            state.yy = yx * previous.xy + yy * previous.yy;
            state.offset = {xx * previous.offset.x + xy * previous.offset.y + row[0],
                            yx * previous.offset.x + yy * previous.offset.y + row[1]};
            for (size_t corner = 0; corner < 4; ++corner) {
                const auto &vertex = previous.vertices[corner];
                state.vertices[corner] = {xx * vertex.x + xy * vertex.y + row[6 + corner * 2],
                                          yx * vertex.x + yy * vertex.y + row[7 + corner * 2]};
                auto &tint = state.colors[corner];
                tint.x *= row[14 + corner * 4];
                tint.y *= row[15 + corner * 4];
                tint.z *= row[16 + corner * 4];
                tint.w *= row[17 + corner * 4];
            }
        }
        for (const auto &color : state.colors)
            m_colorScale = std::max({m_colorScale, color.x, color.y, color.z});
    }
    m_stateDirty = false;
}

void TextGeometry::SetVisibleCharacters(int count)
{
    if (count < -1)
        throw std::invalid_argument("Visible character count must be -1 (all) or non-negative");
    if (m_visibleCharacters != count) {
        m_visibleCharacters = count;
        ++m_revision;
    }
}

void TextGeometry::Prepare(float scale)
{
    if (!std::isfinite(scale) || scale <= 0.0f)
        throw std::invalid_argument("Text render scale must be finite and positive");
    EnsureLayout();
    if (m_preparedScale == scale)
        return;
    for (const auto &glyph : m_layout->glyphs) {
        if (!glyph.visible)
            continue;
        const auto codepoint = static_cast<ImWchar>(
            glyph.codepoint <= IM_UNICODE_CODEPOINT_MAX ? glyph.codepoint : IM_UNICODE_CODEPOINT_INVALID);
        glyph.font->GetFontBaked(glyph.fontSize * scale)->FindGlyph(codepoint);
    }
    m_preparedScale = scale;
}

float TextGeometry::Render(ImDrawList &draw, float minX, float minY, float maxX, float maxY, ImVec4 color, float alignX,
                           float alignY, float scale, float rotation, bool mirrorH, bool mirrorV, bool allowHDR,
                           bool clip)
{
    EnsureLayout();
    CompileCharacters();
    const auto &layout = *m_layout;
    const float hdrScale = allowHDR ? std::max({1.0f, color.x, color.y, color.z}) * m_colorScale : 1.0f;
    color.x /= hdrScale;
    color.y /= hdrScale;
    color.z /= hdrScale;
    const float baseY =
        minY + (maxY - minY - (layout.inkMaxY - layout.inkMinY) * scale) * alignY - layout.inkMinY * scale;
    const ImU32 baseColor = ImGui::ColorConvertFloat4ToU32(color);
    const int textStart = draw.VtxBuffer.Size;
    for (const auto &glyph : layout.glyphs) {
        if (!glyph.visible || glyph.line == text::NoWord ||
            (m_visibleCharacters >= 0 && glyph.character >= static_cast<size_t>(m_visibleCharacters)))
            continue;
        const float lineX = minX + (maxX - minX - layout.lines[glyph.line].width * scale) * alignX;
        const auto &bounds = layout.characters[glyph.character].bounds;
        const ImVec2 center(lineX + (bounds[0] + bounds[2]) * 0.5f * scale,
                            baseY + (bounds[1] + bounds[3]) * 0.5f * scale);
        const auto &state = m_compiled[glyph.character];
        const int firstVertex = draw.VtxBuffer.Size;
        const auto codepoint = static_cast<ImWchar>(
            glyph.codepoint <= IM_UNICODE_CODEPOINT_MAX ? glyph.codepoint : IM_UNICODE_CODEPOINT_INVALID);
        glyph.font->RenderChar(&draw, glyph.fontSize * scale,
                               {lineX + glyph.originX * scale, baseY + glyph.originY * scale}, baseColor, codepoint);
        if (draw.VtxBuffer.Size == firstVertex)
            continue;
        assert(draw.VtxBuffer.Size == firstVertex + 4);
        for (int corner = 0; corner < 4; ++corner) {
            auto &vertex = draw.VtxBuffer[firstVertex + corner];
            const float x = vertex.pos.x - center.x, y = vertex.pos.y - center.y;
            vertex.pos = {center.x + state.xx * x + state.xy * y + (state.offset.x + state.vertices[corner].x) * scale,
                          center.y + state.yx * x + state.yy * y + (state.offset.y + state.vertices[corner].y) * scale};
            const ImVec4 tint(
                color.x * state.colors[corner].x * glyph.color[0], color.y * state.colors[corner].y * glyph.color[1],
                color.z * state.colors[corner].z * glyph.color[2], color.w * state.colors[corner].w * glyph.color[3]);
            vertex.col = ImGui::ColorConvertFloat4ToU32(tint);
        }
        if (clip)
            ClipGlyph(draw, firstVertex, {minX, minY, maxX, maxY});
    }
    if (rotation != 0.0f || mirrorH || mirrorV) {
        const ImVec2 pivot((minX + maxX) * 0.5f, (minY + maxY) * 0.5f);
        const float cosine = std::cos(rotation * DegreesToRadians), sine = std::sin(rotation * DegreesToRadians);
        const float mirrorX = mirrorH ? -1.0f : 1.0f, mirrorY = mirrorV ? -1.0f : 1.0f;
        for (int index = textStart; index < draw.VtxBuffer.Size; ++index) {
            auto &position = draw.VtxBuffer[index].pos;
            const float x = (position.x - pivot.x) * mirrorX, y = (position.y - pivot.y) * mirrorY;
            position = {pivot.x + x * cosine - y * sine, pivot.y + x * sine + y * cosine};
        }
    }
    return hdrScale;
}

} // namespace infernux::textlayout
