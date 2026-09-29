#pragma once

#include <string_view>
#include <vector>

namespace infernux
{

struct WindowPresentationPolicy
{
    bool focusable = true;
    bool activateWhenShown = true;
    bool showBeforeSurface = false;
    bool syncInitialMaximize = true;
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

inline WindowPresentationPolicy ResolveWindowPresentationPolicy(bool hasPlayerControlChannel,
                                                                std::string_view videoDriver)
{
    WindowPresentationPolicy policy;
    // SDL's X11 show path waits synchronously for the window manager while it
    // also requests activation.  A normal focusable editor launched from an
    // existing desktop session can therefore block forever in XIfEvent.  Map
    // the window without the activation request; the compositor and the user
    // can still focus it normally after the map completes.
    if (videoDriver == "x11") {
        policy.activateWhenShown = false;
        policy.syncInitialMaximize = false;
    }
    policy.showBeforeSurface = videoDriver == "x11";

    if (!hasPlayerControlChannel)
        return policy;

    // SDL's X11 backend waits for a MapNotify from the compositor while
    // showing a non-focusable window.  On headless/Xvfb validation displays
    // there may be no focus-stealing window manager, so that wait blocks the
    // Player before its control channel starts.  Keep activation disabled,
    // but let X11 create a normal focusable toplevel; input remains driven by
    // the explicit control channel.  Wayland retains the non-focusable path.
    policy.focusable = videoDriver == "x11";
    policy.activateWhenShown = false;
    // Wayland does not assign an xdg-surface size until the toplevel is mapped
    // and configured. X11 must also map before hidden-window maximize/surface
    // setup, otherwise a later SDL_ShowWindow can wait for a second MapNotify
    // that the window manager will never emit.
    policy.showBeforeSurface = videoDriver == "wayland" || videoDriver == "x11";
    return policy;
}

} // namespace infernux
