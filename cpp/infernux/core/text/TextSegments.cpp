#include "TextSegments.h"

#include <memory>
#include <stdexcept>
#include <string>
#include <utf8proc.h>

namespace infernux::text
{

std::string NormalizeTextNFC(std::string_view text)
{
    utf8proc_uint8_t *data = nullptr;
    const auto length =
        utf8proc_map(reinterpret_cast<const utf8proc_uint8_t *>(text.data()),
                     static_cast<utf8proc_ssize_t>(text.size()), &data, UTF8PROC_STABLE | UTF8PROC_COMPOSE);
    std::unique_ptr<utf8proc_uint8_t, decltype(&utf8proc_free)> normalized(data, &utf8proc_free);
    if (length < 0)
        throw std::invalid_argument(utf8proc_errmsg(length));
    return {reinterpret_cast<const char *>(data), static_cast<size_t>(length)};
}

Segments SegmentText(std::string_view text)
{
    Segments result;
    utf8proc_int32_t previous = 0;
    utf8proc_int32_t state = 0;
    size_t offset = 0;
    while (offset < text.size()) {
        utf8proc_int32_t value;
        const auto consumed = utf8proc_iterate(reinterpret_cast<const utf8proc_uint8_t *>(text.data() + offset),
                                               static_cast<utf8proc_ssize_t>(text.size() - offset), &value);
        if (consumed < 0)
            throw std::invalid_argument("Invalid UTF-8 at byte " + std::to_string(offset));
        const size_t end = offset + static_cast<size_t>(consumed);
        const size_t codepoint = result.codepoints.size();
        if (codepoint == 0 || utf8proc_grapheme_break_stateful(previous, value, &state)) {
            const auto category = utf8proc_category(value);
            const bool newline =
                value == '\r' || value == '\n' || category == UTF8PROC_CATEGORY_ZL || category == UTF8PROC_CATEGORY_ZP;
            const bool whitespace =
                newline || category == UTF8PROC_CATEGORY_ZS || (value >= '\t' && value <= '\r') || value == 0x85;
            result.characters.push_back({offset, end, codepoint, codepoint + 1, NoWord, whitespace, newline});
        } else {
            result.characters.back().end = end;
            result.characters.back().codepointEnd = codepoint + 1;
        }
        result.codepoints.push_back({static_cast<uint32_t>(value), offset, end, result.characters.size() - 1});
        previous = value;
        offset = end;
    }

    bool inWord = false;
    for (size_t index = 0; index < result.characters.size(); ++index) {
        auto &character = result.characters[index];
        if (character.whitespace) {
            inWord = false;
            continue;
        }
        if (!inWord)
            result.words.push_back({index, index + 1});
        else
            result.words.back().characterEnd = index + 1;
        character.word = result.words.size() - 1;
        inWord = true;
    }
    return result;
}

} // namespace infernux::text
