#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include <backends/imgui_impl_sdl3.h>
#include <backends/imgui_impl_sdlrenderer3.h>
#include <function/editor/EditorThemeRegistry.h>
#include <imgui.h>
#include <platform/window/EditorDisplayScale.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <mutex>
#include <string>
#include <thread>

// A separate event loop keeps progress repainting while the Editor thread or
// Python GIL is occupied. EOF also closes the window if the Editor exits.
int main(int argc, char **argv)
{
    if (argc != 3) {
        std::fprintf(stderr, "Usage: InfernuxEditorProgress <editor-font> <editor-theme>\n");
        return 1;
    }
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        std::fprintf(stderr, "%s\n", SDL_GetError());
        return 1;
    }
    constexpr char title[] = "Entering Play Mode";
    SDL_Window *window = SDL_CreateWindow(title, 560, 205,
                                          SDL_WINDOW_HIDDEN | SDL_WINDOW_BORDERLESS | SDL_WINDOW_ALWAYS_ON_TOP |
                                              SDL_WINDOW_NOT_FOCUSABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY);
    SDL_Renderer *renderer = window ? SDL_CreateRenderer(window, "software") : nullptr;
    if (!renderer) {
        std::fprintf(stderr, "%s\n", SDL_GetError());
        SDL_DestroyWindow(window);
        SDL_Quit();
        return 1;
    }
    SDL_SetWindowPosition(window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);
    const float dpi =
        infernux::ResolveEditorDisplayScale(SDL_GetWindowDisplayScale(window), SDL_GetWindowPixelDensity(window));
    SDL_SetWindowSize(window, static_cast<int>(560.0f * dpi), static_cast<int>(205.0f * dpi));
    SDL_SetWindowPosition(window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED);

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO &io = ImGui::GetIO();
    io.IniFilename = nullptr;
    io.LogFilename = nullptr;
    ImGui_ImplSDL3_InitForSDLRenderer(window, renderer);
    ImGui_ImplSDLRenderer3_Init(renderer);
    infernux::EditorThemeRegistry::SetActiveTheme(argv[2]);
    infernux::EditorThemeRegistry::ApplyImGuiColors();
    ImGuiStyle &style = ImGui::GetStyle();
    infernux::EditorThemeRegistry::ConfigureImGuiDimensions(style);
    style.ScaleAllSizes(dpi);
    style.FontSizeBase = 18.0f * dpi;
    io.FontDefault = io.Fonts->AddFontFromFileTTF(argv[1], style.FontSizeBase);
    if (io.FontDefault == nullptr) {
        std::fprintf(stderr, "Could not load the editor font: %s\n", argv[1]);
        ImGui_ImplSDLRenderer3_Shutdown();
        ImGui_ImplSDL3_Shutdown();
        ImGui::DestroyContext();
        SDL_DestroyRenderer(renderer);
        SDL_DestroyWindow(window);
        SDL_Quit();
        return 1;
    }

    std::atomic<bool> done{false};
    std::mutex mutex;
    float progress = 0.0f;
    std::string message = "Preparing Play mode...";
    std::thread input([&] {
        char line[512];
        while (std::fgets(line, sizeof(line), stdin)) {
            float value;
            int offset = 0;
            if (std::sscanf(line, "%f\t%n", &value, &offset) != 1 || offset == 0)
                continue;
            std::lock_guard lock(mutex);
            progress = std::clamp(value, 0.0f, 1.0f);
            message = line + offset;
            message.erase(message.find_last_not_of("\r\n") + 1);
        }
        done.store(true);
    });
    const Uint64 started = SDL_GetTicks();
    bool shown = false;
    while (!done.load()) {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            ImGui_ImplSDL3_ProcessEvent(&event);
        }
        const Uint64 elapsed = SDL_GetTicks() - started;
        if (!shown && elapsed >= 400) {
            SDL_ShowWindow(window);
            shown = true;
        }
        if (shown) {
            float value;
            std::string label;
            {
                std::lock_guard lock(mutex);
                value = progress;
                label = message;
            }

            ImGui_ImplSDLRenderer3_NewFrame();
            ImGui_ImplSDL3_NewFrame();
            ImGui::NewFrame();
            ImGui::SetNextWindowPos(ImVec2(0.0f, 0.0f));
            ImGui::SetNextWindowSize(io.DisplaySize);
            ImGui::PushStyleColor(ImGuiCol_WindowBg, style.Colors[ImGuiCol_PopupBg]);
            ImGui::Begin(title, nullptr,
                         ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse |
                             ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoSavedSettings |
                             ImGuiWindowFlags_NoInputs);
            ImGui::TextWrapped("%s", label.c_str());
            ImGui::Spacing();
            ImGui::ProgressBar(value, ImVec2(-1.0f, 22.0f * dpi), "");
            ImGui::Spacing();
            ImGui::TextWrapped("The editor is temporarily locked while the scene is prepared.");

            const float activityY = io.DisplaySize.y - 18.0f * dpi;
            ImVec4 accent = infernux::EditorThemeRegistry::Color("ROLE_ACCENT");
            accent.w = 0.45f + 0.55f * std::pow(std::sin(static_cast<float>(elapsed) * 0.003f), 2.0f);
            ImGui::GetWindowDrawList()->AddCircleFilled(ImVec2(14.0f * dpi, activityY), 3.0f * dpi,
                                                        ImGui::ColorConvertFloat4ToU32(accent));
            ImGui::SetCursorPos(ImVec2(26.0f * dpi, activityY - ImGui::GetFontSize() * 0.5f));
            ImGui::TextDisabled("Preparing scene resources...");
            ImGui::End();
            ImGui::PopStyleColor();
            ImGui::Render();

            SDL_SetRenderScale(renderer, io.DisplayFramebufferScale.x, io.DisplayFramebufferScale.y);
            SDL_SetRenderDrawColor(renderer, 0, 0, 0, 255);
            SDL_RenderClear(renderer);
            ImGui_ImplSDLRenderer3_RenderDrawData(ImGui::GetDrawData(), renderer);
            SDL_RenderPresent(renderer);
        }
        SDL_Delay(30);
    }
    input.join();
    ImGui_ImplSDLRenderer3_Shutdown();
    ImGui_ImplSDL3_Shutdown();
    ImGui::DestroyContext();
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return 0;
}
