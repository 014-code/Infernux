#include <core/text/TextSegments.h>

#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <stdexcept>
#include <string>

int main()
{
    using namespace infernux::text;
    assert(SegmentText("").characters.empty());
    const std::string accent = "e\xCC\x81";
    const std::string family = "\xF0\x9F\x91\xA8\xE2\x80\x8D\xF0\x9F\x91\xA9\xE2\x80\x8D\xF0\x9F\x91\xA7";
    const std::string flag = "\xF0\x9F\x87\xA8\xF0\x9F\x87\xB3";
    const std::string chinese = "\xE4\xB8\xAD\xE6\x96\x87";
    const std::string source = accent + " " + family + " " + flag + "\r\n" + chinese;
    const auto segments = SegmentText(source);
    assert(segments.characters.size() == 8);
    assert(segments.words.size() == 4);
    assert(segments.characters[0].codepointEnd == 2);
    assert(segments.characters[2].codepointEnd - segments.characters[2].codepointStart == 5);
    assert(segments.characters[4].codepointEnd - segments.characters[4].codepointStart == 2);
    assert(segments.characters[5].newline && segments.characters[5].whitespace);
    assert(segments.characters[5].word == NoWord);
    assert(segments.words.back().characterStart == 6 && segments.words.back().characterEnd == 8);
    std::string rebuilt;
    for (const auto &character : segments.characters)
        rebuilt += source.substr(character.start, character.end - character.start);
    assert(rebuilt == source);
    for (const auto &codepoint : segments.codepoints) {
        const auto &character = segments.characters[codepoint.character];
        assert(codepoint.start >= character.start && codepoint.end <= character.end);
    }
    assert(SegmentText("a\xE3\x80\x80z").words.size() == 2);
    try {
        SegmentText(std::string("bad\xFF", 4));
        assert(false && "Invalid UTF-8 must be rejected at layout creation");
    } catch (const std::invalid_argument &) {
    }
    return 0;
}
