#pragma once

#include "InxTextLayout.h"

namespace infernux::textlayout
{

enum class TextGeometryLayer
{
    Manual,
    Effects,
    Appearance
};

class TextCharacterBuffer
{
  public:
    static constexpr size_t Columns = 30;
    using Row = std::array<float, Columns>;

    TextCharacterBuffer(std::shared_ptr<const std::string> content, size_t count);
    void Reset();
    size_t GetCount() const
    {
        return m_rows.size();
    }
    float *GetData()
    {
        return m_rows.empty() ? nullptr : m_rows.front().data();
    }

  private:
    friend class TextGeometry;
    std::shared_ptr<const std::string> m_content;
    std::vector<Row> m_rows;
};

class TextGeometry
{
  public:
    explicit TextGeometry(TextLayoutParams params);
    void SetLayout(TextLayoutParams params);
    std::shared_ptr<TextLayoutResult> GetLayout();
    std::shared_ptr<TextCharacterBuffer> CreateCharacterBuffer();
    void SetCharacterData(const std::shared_ptr<TextCharacterBuffer> &buffer, TextGeometryLayer layer);
    void ResetCharacterData(int layer = -1);
    void SetVisibleCharacters(int count);
    int GetVisibleCharacters() const
    {
        return m_visibleCharacters;
    }
    uint64_t GetRevision() const
    {
        return m_revision;
    }
    uint64_t GetLayoutRevision() const
    {
        return m_layoutRevision;
    }
    void Prepare(float scale);
    float Render(ImDrawList &draw, float minX, float minY, float maxX, float maxY, ImVec4 color, float alignX,
                 float alignY, float scale, float rotation = 0.0f, bool mirrorH = false, bool mirrorV = false,
                 bool allowHDR = true, bool clip = false);

  private:
    void EnsureLayout();
    void CompileCharacters();
    struct CharacterState
    {
        float xx = 1.0f, xy = 0.0f, yx = 0.0f, yy = 1.0f;
        ImVec2 offset{};
        std::array<ImVec2, 4> vertices{};
        std::array<ImVec4, 4> colors{{{1, 1, 1, 1}, {1, 1, 1, 1}, {1, 1, 1, 1}, {1, 1, 1, 1}}};
    };
    TextLayoutParams m_params;
    std::shared_ptr<TextLayoutResult> m_layout;
    std::shared_ptr<const std::string> m_content;
    std::array<std::shared_ptr<TextCharacterBuffer>, 3> m_layers{};
    std::vector<CharacterState> m_compiled;
    float m_colorScale = 1.0f;
    float m_preparedScale = 0.0f;
    bool m_stateDirty = true;
    uint64_t m_fontGeneration = 0;
    uint64_t m_layoutRevision = 0;
    uint64_t m_revision = 0;
    int m_visibleCharacters = -1;
};

} // namespace infernux::textlayout
