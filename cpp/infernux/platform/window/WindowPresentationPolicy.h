#pragma once

#include <string_view>
#include <vector>

namespace infernux
{

struct WindowPresentationPolicy
{
    bool focusable = true;
    bool activateWhenShown = true;
    bool revealAfterFirstPresentation = false;
    bool activateAfterFirstPresentation = false;
    bool showBeforeSurface = false;
    bool syncInitialMaximize = true;
    bool createMaximized = false;
};

enum class WindowVisibility
{
    Visible,
    Occluded,
    Minimized,
};

/// Occlusion is only a desktop-compositor visibility hint. The swapchain is
/// still valid and off-screen render targets (including MCP captures) must
/// keep advancing. Only an actual minimize, mobile background transition, or
/// pending native-surface replacement suspends presentation.
inline constexpr bool ShouldSuspendWindowRendering(WindowVisibility visibility, bool applicationInBackground,
                                                   bool surfaceRecreationPending) noexcept
{
    return visibility == WindowVisibility::Minimized || applicationInBackground || surfaceRecreationPending;
}

/// Android emits an initial pixel-size event after the Vulkan surface already
/// exists. Rebinding that unchanged surface destroys and rebuilds the complete
/// swapchain during startup. A foreground pixel event is a presentation
/// boundary only when its native pixel extent actually changed; background
/// SurfaceView replacement is handled by the application lifecycle boundary.
inline constexpr bool ShouldRecreateAndroidSurfaceForPixelExtent(bool hasCreatedSurface, int surfaceWidth,
                                                                 int surfaceHeight, int eventWidth,
                                                                 int eventHeight) noexcept
{
    return hasCreatedSurface && eventWidth > 0 && eventHeight > 0 &&
           (surfaceWidth <= 0 || surfaceHeight <= 0 || eventWidth != surfaceWidth || eventHeight != surfaceHeight);
}

/// Validate the instance extensions returned by SDL against the selected
/// native video backend before Vulkan instance creation.  SDL is the source
/// of truth for backend selection; this helper deliberately leaves unknown
/// or custom backends to Vulkan so future SDL drivers keep their own errors.
inline bool ValidateVulkanWindowExtensions(std::string_view videoDriver,
                                           const std::vector<std::string_view> &extensions) noexcept
{
    const auto has = [&extensions](std::string_view name) {
        for (const auto extension : extensions) {
            if (extension == name)
                return true;
        }
        return false;
    };

    if (videoDriver.empty())
        return true;

    const bool knownBackend = videoDriver == "wayland" || videoDriver == "x11" || videoDriver == "windows" ||
                              videoDriver == "android" || videoDriver == "cocoa" || videoDriver == "kmsdrm";
    if (!knownBackend)
        return true;

    if (!has("VK_KHR_surface"))
        return false;

    if (videoDriver == "wayland")
        return has("VK_KHR_wayland_surface");
    if (videoDriver == "x11")
        return has("VK_KHR_xlib_surface") || has("VK_KHR_xcb_surface");
    if (videoDriver == "windows")
        return has("VK_KHR_win32_surface");
    if (videoDriver == "android")
        return has("VK_KHR_android_surface");
    if (videoDriver == "cocoa")
        return has("VK_EXT_metal_surface");
    if (videoDriver == "kmsdrm")
        return has("VK_KHR_display");

    return false;
}

/// Freeze the complete native-surface extension set before VkInstance
/// creation. SDL's X11 surface implementation may select Xlib or XCB when it
/// materializes the window surface, so an X11 instance must enable every
/// supported X11 surface extension reported by the active Vulkan loader.
inline std::vector<std::string_view> ResolveVulkanWindowExtensions(std::string_view videoDriver,
                                                                   const std::vector<std::string_view> &requested,
                                                                   const std::vector<std::string_view> &available)
{
    std::vector<std::string_view> result;
    result.reserve(requested.size() + 2);
    const auto appendUnique = [&result](std::string_view extension) {
        for (const auto existing : result) {
            if (existing == extension)
                return;
        }
        result.push_back(extension);
    };
    for (const auto extension : requested)
        appendUnique(extension);

    if (videoDriver == "x11") {
        for (const auto extension : available) {
            if (extension == "VK_KHR_xlib_surface" || extension == "VK_KHR_xcb_surface")
                appendUnique(extension);
        }
    }
    return result;
}

inline WindowPresentationPolicy ResolveWindowPresentationPolicy(bool playerMode, bool hasPlayerControlChannel,
                                                                std::string_view videoDriver)
{
    WindowPresentationPolicy policy;
    // Win32 can publish the maximized client extent as part of native-window
    // creation. This keeps the first Vulkan surface, ImGui display size, and
    // visible client area on one geometry. Maximizing a hidden 1600x900
    // window afterwards otherwise leaves a real interval where renderer and
    // docking state are authored against the provisional extent.
    policy.createMaximized = !playerMode && videoDriver == "windows";
    // SDL's X11 show path waits synchronously for the window manager while it
    // also requests activation.  A normal focusable editor launched from an
    // existing desktop session can therefore block forever in XIfEvent.  Map
    // the window without the activation request; the compositor and the user
    // can still focus it normally after the map completes.
    if (videoDriver == "x11") {
        policy.activateWhenShown = false;
        policy.syncInitialMaximize = false;
    }
    // A packaged Win32 Player stays hidden until Vulkan has presented one
    // complete startup frame. Revealing the HWND earlier lets the compositor
    // expose its unpainted black client area. Normal Players activate only at
    // this boundary; validation-controlled Players remain in the background.
    if (playerMode && videoDriver == "windows") {
        policy.activateWhenShown = false;
        policy.revealAfterFirstPresentation = true;
        policy.activateAfterFirstPresentation = !hasPlayerControlChannel;
    }
    if (videoDriver == "x11")
        policy.showBeforeSurface = true;

    if (!hasPlayerControlChannel)
        return policy;

    policy.activateAfterFirstPresentation = false;

    // A validation channel is an input source, not a different desktop-window
    // type. Windows and X11 Players must remain ordinary focusable toplevels
    // so the taskbar, Alt+Tab, close commands, and user inspection keep their
    // normal OS semantics. Wayland automation retains its non-focusable path
    // because activation is compositor mediated there.
    policy.focusable = videoDriver != "wayland";
    policy.activateWhenShown = false;
    // Wayland does not assign an xdg-surface size until the toplevel is mapped
    // and configured. X11 must also map before hidden-window maximize/surface
    // setup, otherwise a later SDL_ShowWindow can wait for a second MapNotify
    // that the window manager will never emit.
    policy.showBeforeSurface = videoDriver == "wayland" || videoDriver == "x11";
    return policy;
}

} // namespace infernux
