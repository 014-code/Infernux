#include "InxTextLayout.h"

#include <stdexcept>

namespace infernux::textlayout
{

TextLayoutResult LayoutText(const TextLayoutParams &params)
{
    TextLayoutResult result{};
    result.text = params.text;
    result.font = ResolveFont(params.fontPath);
    const float lineFontSize = ResolveFontSize(params.fontSize);
    const float rasterScale = ResolveEmRasterScale(result.font);
    const float logicalFontSize = params.fontSize > 0.0f ? lineFontSize : lineFontSize / rasterScale;
    result.logicalFontSize = logicalFontSize;
    result.fontSize = params.fontSize > 0.0f ? lineFontSize * rasterScale : lineFontSize;
    result.baseLineHeight = lineFontSize;
    result.lineAdvance = result.baseLineHeight * std::max(params.lineHeight, 0.1f);
    text::MarkupText markup;
    if (params.richText) {
        markup = text::ParseTextMarkup(result.text, logicalFontSize);
        result.displayText = std::move(markup.text);
        result.styles = std::move(markup.styles);
    } else {
        result.displayText = result.text;
        result.styles.push_back({0, result.text.size(), logicalFontSize});
    }
    result.segments = text::SegmentText(result.displayText);
    result.characters.reserve(result.segments.characters.size());
    for (const auto &character : result.segments.characters)
        result.characters.push_back({character.start, character.end, 0, 0, 0, character.word, character.whitespace});

    if (result.font != nullptr) {
        result.fallbackFonts.reserve(params.fallbackFontPaths.size());
        for (const std::string &path : params.fallbackFontPaths) {
            if (path.empty()) {
                auto &missingFonts = GetMissingFonts();
                if (missingFonts.insert("<empty-fallback-font>").second)
                    INXLOG_ERROR("UIText fallback font paths must not contain an empty entry");
                result.font = nullptr;
                result.fallbackFonts.clear();
                result.fallbackFontSizes.clear();
                break;
            }
            ImFont *font = ResolveFont(path);
            if (font == nullptr) {
                result.font = nullptr;
                result.fallbackFonts.clear();
                result.fallbackFontSizes.clear();
                break;
            }
            result.fallbackFonts.push_back(font);
            result.fallbackFontSizes.push_back(logicalFontSize * ResolveEmRasterScale(font));
        }
    }

    if (result.font == nullptr || result.displayText.empty())
        return result;

    const float wrapWidth = params.wrapWidth > 0.0f ? params.wrapWidth : 0.0f;
    const float letterSpacing = params.letterSpacing;
    const char *textBegin = result.displayText.c_str();
    const char *textEnd = textBegin + result.displayText.size();
    const char *lineStart = textBegin;
    const char *cursor = textBegin;
    bool endedWithNewline = false;

    float lineWidth = 0.0f;
    float characterStartWidth = 0.0f;
    int glyphCount = 0;
    size_t lastCharacter = text::NoWord;
    const char *lastBreak = nullptr;
    const char *resumeAfterBreak = nullptr;
    float widthAtBreak = 0.0f;
    size_t glyphCursor = 0;
    const char *spaceRunEnd = textBegin;
    size_t styleIndex = 0;

    while (cursor < textEnd) {
        const char *charStart = cursor;
        // Decode/select/measure once. Word wrap may revisit a measured word;
        // rendering consumes these same advances instead of measuring again.
        if (glyphCursor == result.glyphs.size()) {
            const auto &codepointInfo = result.segments.codepoints[glyphCursor];
            const uint32_t codepoint = codepointInfo.value;
            const size_t consumed = codepointInfo.end - codepointInfo.start;
            TextGlyph glyph{};
            glyph.startOffset = static_cast<size_t>(cursor - textBegin);
            glyph.endOffset = glyph.startOffset + consumed;
            glyph.codepoint = codepoint;
            glyph.character = codepointInfo.character;
            while (styleIndex + 1 < result.styles.size() && result.styles[styleIndex + 1].start <= glyph.startOffset)
                ++styleIndex;
            const auto &style = result.styles[styleIndex];
            glyph.logicalFontSize = style.fontSize;
            glyph.color = style.color;
            if (!result.segments.characters[glyph.character].newline) {
                const auto rasterCodepoint = static_cast<ImWchar>(
                    codepoint <= IM_UNICODE_CODEPOINT_MAX ? codepoint : IM_UNICODE_CODEPOINT_INVALID);
                glyph.font = ResolveGlyphFont(result, rasterCodepoint);
                glyph.fontSize = ResolveGlyphRasterSize(result, glyph.font) * style.fontSize / logicalFontSize;
                glyph.advance = glyph.font->CalcTextSizeA(glyph.fontSize, FLT_MAX, 0.0f, cursor, cursor + consumed).x;
                auto *baked = glyph.font->GetFontBaked(glyph.fontSize);
                glyph.baseline = baked->Ascent * glyph.fontSize / baked->Size;
                const auto *quad = baked->FindGlyph(rasterCodepoint);
                if (quad && quad->Visible) {
                    const float scale = glyph.fontSize / baked->Size;
                    glyph.inkMinY = quad->Y0 * scale;
                    glyph.inkMaxY = quad->Y1 * scale;
                    glyph.bounds = {quad->X0 * scale, quad->Y0 * scale, quad->X1 * scale, quad->Y1 * scale};
                    glyph.visible = true;
                }
            }
            result.glyphs.push_back(glyph);
        }
        const TextGlyph &glyph = result.glyphs[glyphCursor++];
        const uint32_t codepoint = glyph.codepoint;
        cursor = textBegin + glyph.endOffset;

        const auto &character = result.segments.characters[glyph.character];
        if (codepoint == '\r' && glyphCursor < character.codepointEnd)
            continue;

        if (character.newline) {
            PushLine(result, textBegin, lineStart, charStart, lineWidth);
            lineStart = cursor;
            lineWidth = 0.0f;
            glyphCount = 0;
            lastCharacter = text::NoWord;
            lastBreak = nullptr;
            resumeAfterBreak = nullptr;
            widthAtBreak = 0.0f;
            endedWithNewline = true;
            continue;
        }

        endedWithNewline = false;

        const float glyphWidth = glyph.advance;
        const bool newCharacter = glyph.character != lastCharacter;
        const float spacing = glyphCount > 0 && newCharacter ? letterSpacing : 0.0f;
        const float nextWidth = lineWidth + spacing + glyphWidth;

        if (wrapWidth > 0.0f && glyphCount > 0 && newCharacter && nextWidth > wrapWidth) {
            if (lastBreak != nullptr && lastBreak > lineStart) {
                PushLine(result, textBegin, lineStart, lastBreak, widthAtBreak);
                lineStart = resumeAfterBreak != nullptr ? resumeAfterBreak : lastBreak;
                cursor = lineStart;
            } else {
                PushLine(result, textBegin, lineStart, charStart, lineWidth);
                lineStart = charStart;
                cursor = charStart;
            }
            glyphCursor = GlyphIndexAt(result, static_cast<size_t>(cursor - textBegin));
            lineWidth = 0.0f;
            glyphCount = 0;
            lastCharacter = text::NoWord;
            lastBreak = nullptr;
            resumeAfterBreak = nullptr;
            widthAtBreak = 0.0f;
            continue;
        }

        if (newCharacter)
            characterStartWidth = lineWidth;
        lineWidth = nextWidth;
        ++glyphCount;
        lastCharacter = glyph.character;

        if (glyphCursor != character.codepointEnd)
            continue;
        if (character.whitespace) {
            if (cursor > spaceRunEnd) {
                spaceRunEnd = cursor;
                while (spaceRunEnd < textEnd && (*spaceRunEnd == ' ' || *spaceRunEnd == '\t'))
                    ++spaceRunEnd;
            }
            lastBreak = textBegin + character.start;
            resumeAfterBreak = spaceRunEnd;
            widthAtBreak = characterStartWidth;
        } else if (IsBreakAfterChar(result.segments.codepoints[character.codepointStart].value)) {
            lastBreak = cursor;
            resumeAfterBreak = cursor;
            widthAtBreak = lineWidth;
        }
    }

    if (lineStart < textEnd)
        PushLine(result, textBegin, lineStart, textEnd, lineWidth);
    else if (endedWithNewline)
        PushLine(result, textBegin, textEnd, textEnd, 0.0f);

    if (!result.lines.empty()) {
        // Logical line height still owns wrapping, intrinsic size and spacing.
        // Align the rendered content using the same glyph rectangles RenderChar
        // consumes, so font ascent padding does not shift every label downward.
        result.inkMinY = FLT_MAX;
        result.inkMaxY = -FLT_MAX;
        float lineY = 0.0f;
        const auto *baseBaked = result.font->GetFontBaked(result.fontSize);
        const float baseBaseline = baseBaked->Ascent * result.fontSize / baseBaked->Size;
        for (auto &line : result.lines) {
            line.y = lineY;
            line.height = result.baseLineHeight;
            float baseline = baseBaseline;
            for (size_t glyphIndex = line.glyphStart; glyphIndex < line.glyphEnd; ++glyphIndex) {
                const auto &glyph = result.glyphs[glyphIndex];
                line.height = std::max(line.height, glyph.logicalFontSize);
                baseline = std::max(baseline, glyph.baseline);
            }
            bool visible = false;
            float cursorX = 0.0f;
            size_t lastCharacter = text::NoWord;
            for (size_t glyphIndex = line.glyphStart; glyphIndex < line.glyphEnd; ++glyphIndex) {
                auto &glyph = result.glyphs[glyphIndex];
                if (lastCharacter != text::NoWord && glyph.character != lastCharacter)
                    cursorX += letterSpacing;
                glyph.line = static_cast<size_t>(&line - result.lines.data());
                glyph.originX = cursorX;
                glyph.originY = lineY + baseline - glyph.baseline;
                if (glyph.visible) {
                    glyph.bounds[0] += cursorX;
                    glyph.bounds[1] += glyph.originY;
                    glyph.bounds[2] += cursorX;
                    glyph.bounds[3] += glyph.originY;
                } else {
                    glyph.bounds = {cursorX, lineY, cursorX + glyph.advance, lineY + line.height};
                }
                cursorX += glyph.advance;
                lastCharacter = glyph.character;
                if (!glyph.visible)
                    continue;
                result.inkMinY = std::min(result.inkMinY, glyph.originY + glyph.inkMinY);
                result.inkMaxY = std::max(result.inkMaxY, glyph.originY + glyph.inkMaxY);
                visible = true;
            }
            if (!visible) {
                // Preserve intentional blank/whitespace lines in the block.
                result.inkMinY = std::min(result.inkMinY, lineY);
                result.inkMaxY = std::max(result.inkMaxY, lineY + line.height);
            }
            lineY += line.height * std::max(params.lineHeight, 0.1f);
        }
        result.totalHeight = result.lines.back().y + result.lines.back().height;
        size_t lineIndex = 0;
        for (size_t index = 0; index < result.characters.size(); ++index) {
            auto &character = result.characters[index];
            const auto &segment = result.segments.characters[index];
            while (lineIndex + 1 < result.lines.size() && result.lines[lineIndex + 1].startOffset <= segment.start)
                ++lineIndex;
            character.line = lineIndex;
            character.glyphStart = segment.codepointStart;
            character.glyphEnd = segment.codepointEnd;
            const float lineY = result.lines[lineIndex].y;
            character.bounds = {result.lines[lineIndex].width, lineY, result.lines[lineIndex].width,
                                lineY + result.lines[lineIndex].height};
            bool initialized = false;
            for (size_t glyphIndex = character.glyphStart; glyphIndex < character.glyphEnd; ++glyphIndex) {
                const auto &glyph = result.glyphs[glyphIndex];
                if (glyph.line != lineIndex)
                    continue;
                if (!initialized) {
                    character.bounds = glyph.bounds;
                    initialized = true;
                } else {
                    character.bounds[0] = std::min(character.bounds[0], glyph.bounds[0]);
                    character.bounds[1] = std::min(character.bounds[1], glyph.bounds[1]);
                    character.bounds[2] = std::max(character.bounds[2], glyph.bounds[2]);
                    character.bounds[3] = std::max(character.bounds[3], glyph.bounds[3]);
                }
            }
        }
    }

    if (params.richText) {
        const auto sourceRange = [&](size_t &start, size_t &end) {
            const size_t mappedEnd = end > start                     ? markup.sources[end - 1].end
                                     : start < markup.sources.size() ? markup.sources[start].start
                                                                     : markup.sources.back().end;
            start = start < markup.sources.size() ? markup.sources[start].start : mappedEnd;
            end = mappedEnd;
        };
        for (auto &glyph : result.glyphs)
            sourceRange(glyph.startOffset, glyph.endOffset);
        for (auto &character : result.characters)
            sourceRange(character.startOffset, character.endOffset);
        for (auto &line : result.lines)
            sourceRange(line.startOffset, line.endOffset);
        for (auto &style : result.styles)
            sourceRange(style.start, style.end);
    }

    return result;
}

std::shared_ptr<TextLayoutResult> CreateTextLayout(const TextLayoutParams &params)
{
    if (ImGui::GetCurrentContext() == nullptr)
        throw std::logic_error("Text layout requires an initialized UI renderer");
    auto layout = std::make_shared<TextLayoutResult>(LayoutText(params));
    if (layout->font == nullptr)
        throw std::runtime_error("Text layout font is unavailable");
    return layout;
}

void RenderLine(ImDrawList *drawList, const TextLayoutResult &layout, const TextLine &line, float x, float y,
                ImU32 color, float letterSpacing, const ImVec4 *clipRect)
{
    if (drawList == nullptr || layout.font == nullptr)
        return;

    float cursorX = x;
    int glyphIndex = 0;
    size_t lastCharacter = text::NoWord;
    const auto base = ImGui::ColorConvertU32ToFloat4(color);
    const bool colored = layout.styles.size() > 1 ||
                         (!layout.styles.empty() && layout.styles.front().color != std::array<float, 4>{1, 1, 1, 1});
    for (size_t index = line.glyphStart; index < line.glyphEnd; ++index) {
        const TextGlyph &glyph = layout.glyphs[index];
        if (!glyph.font)
            continue;
        if (glyphIndex > 0 && glyph.character != lastCharacter)
            cursorX += letterSpacing;
        const auto codepoint = static_cast<ImWchar>(
            glyph.codepoint <= IM_UNICODE_CODEPOINT_MAX ? glyph.codepoint : IM_UNICODE_CODEPOINT_INVALID);
        const ImU32 tint = colored ? ImGui::ColorConvertFloat4ToU32({base.x * glyph.color[0], base.y * glyph.color[1],
                                                                     base.z * glyph.color[2], base.w * glyph.color[3]})
                                   : color;
        glyph.font->RenderChar(drawList, glyph.fontSize, ImVec2(cursorX, y + glyph.originY - line.y), tint, codepoint,
                               clipRect);
        cursorX += glyph.advance;
        ++glyphIndex;
        lastCharacter = glyph.character;
    }
}

void RenderTextBox(ImDrawList *drawList, float minX, float minY, float maxX, float maxY, const TextLayoutResult &layout,
                   ImU32 color, float alignX, float alignY, float letterSpacing, const ImVec4 *clipRect)
{
    if (drawList == nullptr || layout.font == nullptr || layout.lines.empty())
        return;

    const float boxWidth = maxX - minX;
    const float boxHeight = maxY - minY;
    const float inkHeight = layout.inkMaxY - layout.inkMinY;
    const float baseY = minY + (boxHeight - inkHeight) * alignY - layout.inkMinY;

    for (size_t index = 0; index < layout.lines.size(); ++index) {
        const TextLine &line = layout.lines[index];
        const float lineX = minX + (boxWidth - line.width) * alignX;
        const float lineY = baseY + line.y;
        RenderLine(drawList, layout, line, lineX, lineY, color, letterSpacing, clipRect);
    }
}

} // namespace infernux::textlayout
