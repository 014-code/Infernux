#include <platform/window/WindowPresentationPolicy.h>
#include <platform/window/WindowSizingPolicy.h>

#include <cassert>
#include <stdexcept>
#include <string_view>
#include <vector>

static_assert(!infernux::ShouldRecreateAndroidSurfaceForPixelExtent(false, 0, 0, 3200, 1440));
static_assert(!infernux::ShouldRecreateAndroidSurfaceForPixelExtent(true, 3200, 1440, 3200, 1440));
static_assert(infernux::ShouldRecreateAndroidSurfaceForPixelExtent(true, 3200, 1440, 1440, 3200));
static_assert(!infernux::ShouldRecreateAndroidSurfaceForPixelExtent(true, 3200, 1440, 0, 0));

int main()
{
    using infernux::ResolveEditorInitialWindowSize;

    const auto reference = ResolveEditorInitialWindowSize(1600, 900, 1920, 1080);
    assert(reference.width == 1600);
    assert(reference.height == 900);

    const auto constrained = ResolveEditorInitialWindowSize(1600, 900, 1024, 768);
    assert(constrained.width == 921);
    assert(constrained.height == 691);

    const auto compact = ResolveEditorInitialWindowSize(800, 600, 2560, 1440);
    assert(compact.width == 800);
    assert(compact.height == 600);

    bool rejected = false;
    try {
        (void)ResolveEditorInitialWindowSize(1600, 900, 0, 768);
    } catch (const std::invalid_argument &) {
        rejected = true;
    }
    assert(rejected);

    const auto ordinaryWayland = infernux::ResolveWindowPresentationPolicy(false, "wayland");
    assert(ordinaryWayland.focusable);
    assert(ordinaryWayland.activateWhenShown);
    assert(!ordinaryWayland.showBeforeSurface);
    assert(ordinaryWayland.syncInitialMaximize);

    const auto ordinaryX11 = infernux::ResolveWindowPresentationPolicy(false, "x11");
    assert(ordinaryX11.focusable);
    assert(!ordinaryX11.activateWhenShown);
    assert(ordinaryX11.showBeforeSurface);
    assert(!ordinaryX11.syncInitialMaximize);

    const auto controlledWayland = infernux::ResolveWindowPresentationPolicy(true, "wayland");
    assert(!controlledWayland.focusable);
    assert(!controlledWayland.activateWhenShown);
    assert(controlledWayland.showBeforeSurface);

    const auto controlledX11 = infernux::ResolveWindowPresentationPolicy(true, "x11");
    assert(controlledX11.focusable);
    assert(!controlledX11.activateWhenShown);
    assert(controlledX11.showBeforeSurface);

    const auto controlledWindows = infernux::ResolveWindowPresentationPolicy(true, "windows");
    assert(!controlledWindows.focusable);
    assert(!controlledWindows.activateWhenShown);
    assert(!controlledWindows.showBeforeSurface);

    const std::vector<std::string_view> waylandExtensions = {"VK_KHR_surface", "VK_KHR_wayland_surface"};
    const std::vector<std::string_view> xlibExtensions = {"VK_KHR_surface", "VK_KHR_xlib_surface"};
    const std::vector<std::string_view> xcbExtensions = {"VK_KHR_surface", "VK_KHR_xcb_surface"};
    const std::vector<std::string_view> surfaceOnlyExtensions = {"VK_KHR_surface"};
    assert(infernux::ValidateVulkanWindowExtensions("wayland", waylandExtensions));
    assert(infernux::ValidateVulkanWindowExtensions("x11", xlibExtensions));
    assert(infernux::ValidateVulkanWindowExtensions("x11", xcbExtensions));
    assert(!infernux::ValidateVulkanWindowExtensions("wayland", xlibExtensions));
    assert(!infernux::ValidateVulkanWindowExtensions("x11", surfaceOnlyExtensions));
    assert(infernux::ValidateVulkanWindowExtensions("custom", surfaceOnlyExtensions));

    using infernux::ShouldSuspendWindowRendering;
    using infernux::WindowVisibility;
    assert(!ShouldSuspendWindowRendering(WindowVisibility::Visible, false, false));
    assert(!ShouldSuspendWindowRendering(WindowVisibility::Occluded, false, false));
    assert(ShouldSuspendWindowRendering(WindowVisibility::Minimized, false, false));
    assert(ShouldSuspendWindowRendering(WindowVisibility::Visible, true, false));
    assert(ShouldSuspendWindowRendering(WindowVisibility::Visible, false, true));

    using infernux::ShouldRecreateAndroidSurfaceForPixelExtent;
    assert(!ShouldRecreateAndroidSurfaceForPixelExtent(false, 0, 0, 3200, 1440));
    assert(!ShouldRecreateAndroidSurfaceForPixelExtent(true, 3200, 1440, 3200, 1440));
    assert(ShouldRecreateAndroidSurfaceForPixelExtent(true, 3200, 1440, 1440, 3200));
    assert(!ShouldRecreateAndroidSurfaceForPixelExtent(true, 3200, 1440, 0, 0));
    return 0;
}
