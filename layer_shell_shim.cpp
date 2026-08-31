#include <QGuiApplication>
#include <QWindow>
#include <QtGui/qpa/qplatformnativeinterface.h>
#include <wayland-client.h>

extern "C" {
    #include "layer_shell_c.h"
}

// C++ 侧的 wrapper，名字不要和 C 层冲突
static struct layer_shell_state* g_state = nullptr;

extern "C" {

int layer_shell_init() {
    if (g_state) return 0;
    QPlatformNativeInterface* native = QGuiApplication::platformNativeInterface();
    if (!native) return -1;
    struct wl_display* display = (struct wl_display*)
        native->nativeResourceForWindow("display", nullptr);
    if (!display) return -2;
    g_state = layer_shell_create(display);
    if (!g_state) return -3;
    return 0;
}

// 改名：避免与 C 层的 create_overlay_layer_surface 冲突
void* make_overlay_surface(void* wl_surface_ptr,
                           int width, int height,
                           int pos_x, int pos_y) {
    if (!g_state || !wl_surface_ptr) return nullptr;
    return create_overlay_layer_surface(g_state,
        (struct wl_surface*)wl_surface_ptr,
        width, height, pos_x, pos_y, "meapet");
}

void destroy_overlay_surface(void* layer_surface_ptr) {
    destroy_overlay_layer_surface(layer_surface_ptr);
}

void layer_shell_cleanup() {
    if (g_state) {
        layer_shell_destroy(g_state);
        g_state = nullptr;
    }
}

// ★ 桥接：让 C 层 API（layer_shell_c.c）能拿到 C++ 侧的 g_state
extern "C" struct layer_shell_state* layer_shell_get_state() {
    return g_state;
}

void* get_wl_surface_from_window(void* qwindow_ptr) {
    if (!qwindow_ptr) return nullptr;
    QWindow* w = (QWindow*)qwindow_ptr;
    QPlatformNativeInterface* native = QGuiApplication::platformNativeInterface();
    if (!native) return nullptr;
    return native->nativeResourceForWindow("surface", w);
}

}

