#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <wayland-client.h>
#include "wlr-layer-shell-client-protocol.h"

static struct wl_display*          g_display     = NULL;
static struct wl_compositor*       g_compositor  = NULL;
static struct wl_shm*              g_shm         = NULL;
static struct zwlr_layer_shell_v1* g_layer_shell = NULL;
static struct wl_surface*          g_surface     = NULL;   /* ★ 全局 surface 指针 */
static int g_width  = 320;
static int g_height = 320;
static int g_configured = 0;

static void registry_global(void* data, struct wl_registry* registry,
                            uint32_t id, const char* interface, uint32_t version) {
    if (strcmp(interface, wl_compositor_interface.name) == 0) {
        g_compositor = (struct wl_compositor*)
            wl_registry_bind(registry, id, &wl_compositor_interface,
                             (version >= 4) ? 4 : version);
    } else if (strcmp(interface, wl_shm_interface.name) == 0) {
        g_shm = (struct wl_shm*)
            wl_registry_bind(registry, id, &wl_shm_interface,
                             (version >= 1) ? 1 : version);
    } else if (strcmp(interface, zwlr_layer_shell_v1_interface.name) == 0) {
        g_layer_shell = (struct zwlr_layer_shell_v1*)
            wl_registry_bind(registry, id, &zwlr_layer_shell_v1_interface,
                             (version >= 4) ? 4 : version);
    }
}

static void registry_global_remove(void* data, struct wl_registry* registry, uint32_t id) {}
static const struct wl_registry_listener registry_listener = {
    registry_global, registry_global_remove
};

static struct wl_buffer* create_buffer(uint32_t width, uint32_t height) {
    size_t stride = width * 4;
    size_t size   = stride * height;

    int fd = memfd_create("meapet-shm", 0);
    if (fd < 0) { perror("memfd_create"); return NULL; }
    if (ftruncate(fd, (off_t)size) < 0) { perror("ftruncate"); close(fd); return NULL; }

    void* data = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (data == MAP_FAILED) { perror("mmap"); close(fd); return NULL; }

    uint32_t* pixels = (uint32_t*)data;
    for (uint32_t y = 0; y < height; y++) {
        for (uint32_t x = 0; x < width; x++) {
            int mx = (x < 10 || x >= (int)width  - 10) ? 0 : 1;
            int my = (y < 10 || y >= (int)height - 10) ? 0 : 1;
            if (mx && my)
                pixels[y * width + x] = (180U << 24) | (60U << 16) | (120U << 8) | 255U;
            else
                pixels[y * width + x] = 0x00000000;
        }
    }

    struct wl_shm_pool* pool = wl_shm_create_pool(g_shm, fd, (int32_t)size);
    struct wl_buffer* buffer = wl_shm_pool_create_buffer(
        pool, 0, (int32_t)width, (int32_t)height,
        (int32_t)stride, WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    munmap(data, size);
    close(fd);
    return buffer;
}

static void layer_surface_configure(void* data,
                                    struct zwlr_layer_surface_v1* surface,
                                    uint32_t serial, uint32_t width, uint32_t height) {
    zwlr_layer_surface_v1_ack_configure(surface, serial);
    g_configured = 1;

    /* ★★★ 核心修复：空 input region → pointer/touch 完全穿透 */
    struct wl_region* empty_region = wl_compositor_create_region(g_compositor);
    /* 不调 wl_region_add → region 为空 */
    wl_surface_set_input_region(g_surface, empty_region);
    wl_region_destroy(empty_region);   /* 可立即销毁 */

    fprintf(stderr, "[INFO] layer_surface configured: %ux%u, 空 input region 已设置\n", width, height);
}

static void layer_surface_closed(void* data, struct zwlr_layer_surface_v1* surface) {
    fprintf(stderr, "[INFO] layer_surface closed by compositor\n");
}
static const struct zwlr_layer_surface_v1_listener layer_surface_listener = {
    layer_surface_configure, layer_surface_closed
};

int main(int argc, char** argv) {
    fprintf(stderr, "[INFO] 连接 Wayland display...\n");
    g_display = wl_display_connect(NULL);
    if (!g_display) {
        fprintf(stderr, "FATAL: 无法连接 Wayland display (WAYLAND_DISPLAY=%s)\n",
                getenv("WAYLAND_DISPLAY") ?: "?");
        return 1;
    }

    struct wl_registry* registry = wl_display_get_registry(g_display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(g_display);
    wl_registry_destroy(registry);

    if (!g_compositor)  { fprintf(stderr, "FATAL: 缺少 wl_compositor\n");   return 1; }
    if (!g_shm)         { fprintf(stderr, "FATAL: 缺少 wl_shm\n");          return 1; }
    if (!g_layer_shell) { fprintf(stderr, "FATAL: compositor 不支持 zwlr_layer_shell_v1\n"); return 1; }
    fprintf(stderr, "[OK] 已绑定 compositor / shm / layer_shell\n");

    g_surface = wl_compositor_create_surface(g_compositor);   /* ★ 赋值给全局 */
    if (!g_surface) { fprintf(stderr, "FATAL: wl_compositor_create_surface 失败\n"); return 1; }
    fprintf(stderr, "[OK] 裸 wl_surface 已创建 (无 role)\n");

    struct zwlr_layer_surface_v1* layer_surface =
        zwlr_layer_shell_v1_get_layer_surface(
            g_layer_shell, g_surface, NULL,
            ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY, "meapet");
    if (!layer_surface) { fprintf(stderr, "FATAL: get_layer_surface 失败\n"); return 1; }

    zwlr_layer_surface_v1_add_listener(layer_surface, &layer_surface_listener, NULL);
    zwlr_layer_surface_v1_set_size(layer_surface, g_width, g_height);
    zwlr_layer_surface_v1_set_anchor(layer_surface, 0);
    zwlr_layer_surface_v1_set_margin(layer_surface, 100, 0, 0, 100);
    zwlr_layer_surface_v1_set_keyboard_interactivity(
        layer_surface, ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE);
    zwlr_layer_surface_v1_set_exclusive_zone(layer_surface, 0);

    /* 阶段一：触发 configure */
    wl_surface_commit(g_surface);
    wl_display_flush(g_display);
    fprintf(stderr, "[INFO] 已提交初始 commit，等待 configure...\n");

    /* 阶段二：等 configure + ack + 设置空 input region */
    while (!g_configured && wl_display_dispatch(g_display) != -1) { }

    /* 阶段三：attach buffer */
    struct wl_buffer* buffer = create_buffer(g_width, g_height);
    if (!buffer) { fprintf(stderr, "FATAL: 创建 buffer 失败\n"); return 1; }
    wl_surface_attach(g_surface, buffer, 0, 0);
    wl_surface_damage(g_surface, 0, 0, g_width, g_height);
    wl_surface_commit(g_surface);
    wl_display_flush(g_display);
    fprintf(stderr, "[OK] buffer 已 attach 并 commit\n");
    fprintf(stderr, "[INFO] 验证：先确认这是 meapet 的 surface\n");
    fprintf(stderr, "[INFO]   运行: niri msg layers | grep meapet\n");
    fprintf(stderr, "[INFO] 点击橙色区域 → 应穿透到下层窗口\n");
    fprintf(stderr, "[INFO] 按 Ctrl+C 退出\n");

    while (wl_display_dispatch(g_display) != -1) {
        wl_surface_commit(g_surface);
        wl_display_flush(g_display);
    }

    zwlr_layer_surface_v1_destroy(layer_surface);
    wl_surface_destroy(g_surface);
    wl_display_disconnect(g_display);
    return 0;
}

