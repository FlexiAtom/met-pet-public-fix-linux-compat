/* ★ 必须在所有 #include 之前：暴露 memfd_create / MFD_CLOEXEC */
#define _GNU_SOURCE

#include "layer_shell_c.h"
#include "wlr-layer-shell-client-protocol.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client-protocol.h>

#define MAX_SHM_FORMATS 32

struct layer_shell_state {
    struct wl_display* display;
    struct wl_compositor* compositor;
    struct zwlr_layer_shell_v1* layer_shell;
    struct wl_shm* shm;                          /* ★ Phase 2: 像素上传必需 */
    uint32_t shm_formats[MAX_SHM_FORMATS];       /* ★ compositor 广播的格式列表 */
    int shm_format_count;
};

/* ------------------------------------------------------------------ 格式表 */

static const struct {
    uint32_t fmt;
    const char* name;
} FORMAT_TABLE[] = {
    { WL_SHM_FORMAT_ARGB8888, "argb8888" },
    { WL_SHM_FORMAT_XRGB8888, "xrgb8888" },
    { WL_SHM_FORMAT_ABGR8888, "abgr8888" },
    { WL_SHM_FORMAT_XBGR8888, "xbgr8888" },
    { WL_SHM_FORMAT_RGBA8888, "rgba8888" },
    { WL_SHM_FORMAT_RGBX8888, "rgbx8888" },
    { WL_SHM_FORMAT_BGRA8888, "bgra8888" },
    { WL_SHM_FORMAT_BGRX8888, "bgrx8888" },
};

/* ★ 前置声明：registry_global 中引用，定义在后面 */
static void shm_format_handler(void* data, struct wl_shm* shm, uint32_t format);

static void registry_global(void* data, struct wl_registry* registry,
                            uint32_t id, const char* interface, uint32_t version) {
    struct layer_shell_state* s = (struct layer_shell_state*)data;
    if (strcmp(interface, wl_compositor_interface.name) == 0) {
        s->compositor = (struct wl_compositor*)
            wl_registry_bind(registry, id, &wl_compositor_interface,
                             version >= 4 ? 4 : version);
    } else if (strcmp(interface, zwlr_layer_shell_v1_interface.name) == 0) {
        s->layer_shell = (struct zwlr_layer_shell_v1*)
            wl_registry_bind(registry, id, &zwlr_layer_shell_v1_interface,
                             version >= 4 ? 4 : version);
    } else if (strcmp(interface, wl_shm_interface.name) == 0) {
        /* ★ Phase 2: 绑定 wl_shm 用于像素上传 */
        s->shm = (struct wl_shm*)
            wl_registry_bind(registry, id, &wl_shm_interface, 1);
        static const struct wl_shm_listener shm_lsn = {
            .format = shm_format_handler
        };
        wl_shm_add_listener(s->shm, &shm_lsn, s);
    }
}

static void registry_global_remove(void* data, struct wl_registry* registry, uint32_t id) {
    (void)data; (void)registry; (void)id;
}

/* ★ wl_shm.format 事件回调：记录 compositor 支持的格式 */
static void shm_format_handler(void* data, struct wl_shm* shm, uint32_t format) {
    (void)shm;
    struct layer_shell_state* s = (struct layer_shell_state*)data;
    if (s->shm_format_count < MAX_SHM_FORMATS)
        s->shm_formats[s->shm_format_count++] = format;
}

struct layer_shell_state* layer_shell_create(struct wl_display* display) {
    if (!display) return NULL;

    struct layer_shell_state* s = (struct layer_shell_state*)calloc(1, sizeof(*s));
    if (!s) return NULL;
    s->display = display;

    struct wl_registry* registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &(struct wl_registry_listener){
        registry_global, registry_global_remove
    }, s);
    wl_display_roundtrip(display);   /* ★ roundtrip 后 shm_formats 已填充 */
    wl_registry_destroy(registry);

    if (!s->layer_shell) {
        free(s);
        return NULL;   /* compositor 不支持 wlr-layer-shell */
    }
    return s;
}

void layer_shell_destroy(struct layer_shell_state* state) {
    if (!state) return;
    if (state->layer_shell)
        zwlr_layer_shell_v1_destroy(state->layer_shell);   /* ★ 修正：shell 用 shell 的 destroy */
    if (state->shm)
        wl_shm_destroy(state->shm);   /* ★ 避免泄漏 */
    free(state);
}

void* create_overlay_layer_surface(struct layer_shell_state* state,
                                   struct wl_surface* surface,
                                   int width, int height,
                                   int pos_x, int pos_y,
                                   const char* ns) {
    if (!state || !state->layer_shell || !surface) return NULL;

    struct zwlr_layer_surface_v1* ls =
        zwlr_layer_shell_v1_get_layer_surface(
            state->layer_shell, surface, NULL,
            ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY, ns);

    if (!ls) return NULL;

    zwlr_layer_surface_v1_set_size(ls, width, height);
    zwlr_layer_surface_v1_set_anchor(ls, 0);
    zwlr_layer_surface_v1_set_margin(ls, pos_y, 0, 0, pos_x);
    zwlr_layer_surface_v1_set_keyboard_interactivity(
        ls, ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE);
    zwlr_layer_surface_v1_set_exclusive_zone(ls, 0);

    wl_surface_commit(surface);
    wl_display_flush(state->display);

    return (void*)ls;
}

void destroy_overlay_layer_surface(void* layer_surface) {
    if (layer_surface)
        zwlr_layer_surface_v1_destroy((struct zwlr_layer_surface_v1*)layer_surface);
}

/* ===================== layer context API ===================== */

struct layer_ctx {
    struct layer_shell_state*     state;
    struct wl_surface*            surface;
    struct zwlr_layer_surface_v1* ls;
    int                          configured;
    int                          width;
    int                          height;
};

static void lc_configure(void* data, struct zwlr_layer_surface_v1* surf,
                         uint32_t serial, uint32_t w, uint32_t h) {
    zwlr_layer_surface_v1_ack_configure(surf, serial);
    struct layer_ctx* ctx = (struct layer_ctx*)data;
    if (ctx) {
        if (w > 0) ctx->width = (int)w;
        if (h > 0) ctx->height = (int)h;
        ctx->configured = 1;
    }
}

static void lc_closed(void* data, struct zwlr_layer_surface_v1* surf) {
    (void)data; (void)surf;
}

static const struct zwlr_layer_surface_v1_listener lc_listener = {
    lc_configure, lc_closed
};

void* layer_create_context(struct layer_shell_state* state,
                           int width, int height, int pos_x, int pos_y) {
    if (!state) {
        state = layer_shell_get_state();
    }
    if (!state || !state->layer_shell || !state->compositor) return NULL;

    struct layer_ctx* ctx = (struct layer_ctx*)calloc(1, sizeof(*ctx));
    if (!ctx) return NULL;
    ctx->state = state;
    ctx->width = width;
    ctx->height = height;

    ctx->surface = wl_compositor_create_surface(state->compositor);
    if (!ctx->surface) { free(ctx); return NULL; }

    ctx->ls = zwlr_layer_shell_v1_get_layer_surface(
        state->layer_shell, ctx->surface, NULL,
        ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY, "meapet");
    if (!ctx->ls) {
        wl_surface_destroy(ctx->surface);
        free(ctx);
        return NULL;
    }

    zwlr_layer_surface_v1_add_listener(ctx->ls, &lc_listener, ctx);
    zwlr_layer_surface_v1_set_size(ctx->ls, width, height);
    /* 锚定到左上角 —— 关键！
       无锚定(anchor=0)时 wlroots 会把 surface 强制居中，margin 根本不参与
       计算，set_margin / layer_set_position 全部失效。必须 TOP|LEFT 锚定，
       margin 才是精确的「距左 x、距上 y」偏移。 */
    zwlr_layer_surface_v1_set_anchor(
        ctx->ls,
        ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP | ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT);
    zwlr_layer_surface_v1_set_margin(ctx->ls, pos_y, 0, 0, pos_x);
    zwlr_layer_surface_v1_set_keyboard_interactivity(
        ctx->ls, ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE);
    zwlr_layer_surface_v1_set_exclusive_zone(ctx->ls, 0);

    /* 空 input region → 穿透 */
    struct wl_region* empty = wl_compositor_create_region(state->compositor);
    wl_surface_set_input_region(ctx->surface, empty);
    wl_region_destroy(empty);

    wl_surface_commit(ctx->surface);
    wl_display_flush(state->display);
    return ctx;
}

void layer_set_click_through(void* ctx_ptr, int enabled) {
    if (!ctx_ptr) return;
    struct layer_ctx* ctx = (struct layer_ctx*)ctx_ptr;
    if (!ctx->state) {
        ctx->state = layer_shell_get_state();
    }
    if (!ctx->state || !ctx->state->compositor) return;
    struct wl_region* region = wl_compositor_create_region(ctx->state->compositor);
    if (!region) return;
    if (!enabled) {
        wl_region_add(region, 0, 0, ctx->width, ctx->height);
    }
    wl_surface_set_input_region(ctx->surface, region);
    wl_region_destroy(region);
    wl_surface_commit(ctx->surface);
    wl_display_flush(ctx->state->display);
}

void layer_destroy_context(void* ctx_ptr) {
    if (!ctx_ptr) return;
    struct layer_ctx* ctx = (struct layer_ctx*)ctx_ptr;
    if (ctx->ls) zwlr_layer_surface_v1_destroy(ctx->ls);
    if (ctx->surface) wl_surface_destroy(ctx->surface);
    free(ctx);
}

/* ===================== Phase 2: 像素上传 ===================== */

int layer_shm_supports_format(struct layer_shell_state* state, const char* format_name) {
    if (!state || !format_name) return 0;
    for (size_t i = 0; i < sizeof(FORMAT_TABLE) / sizeof(FORMAT_TABLE[0]); i++) {
        if (strcmp(FORMAT_TABLE[i].name, format_name) == 0) {
            for (int j = 0; j < state->shm_format_count; j++) {
                if (state->shm_formats[j] == FORMAT_TABLE[i].fmt) return 1;
            }
            return 0;
        }
    }
    return 0;
}

static int has_format(struct layer_shell_state* state, uint32_t fmt) {
    if (!state) return 0;
    for (int i = 0; i < state->shm_format_count; i++) {
        if (state->shm_formats[i] == fmt) return 1;
    }
    return 0;
}

/* 选择最佳上传格式：
 *   优先 ABGR8888 —— 其内存布局 [R,G,B,A] 与 QImage Format_RGBA8888 完全一致，可 memcpy 零转换
 *   fallback ARGB8888 —— 所有 compositor 必支持，内存布局 [B,G,R,A]，需交换 R/B
 */
static uint32_t choose_upload_format(struct layer_shell_state* state) {
    if (has_format(state, WL_SHM_FORMAT_ABGR8888))
        return WL_SHM_FORMAT_ABGR8888;
    return WL_SHM_FORMAT_ARGB8888;
}

/* 把 QImage 的 [R,G,B,A] 转换为目标 format 的内存布局 */
static void convert_rgba_to_format(const unsigned char* src, unsigned char* dst,
                                   int n, uint32_t fmt) {
    if (fmt == WL_SHM_FORMAT_ABGR8888) {
        /* ABGR8888 ('AB24')：内存 [R,G,B,A] —— 与 QImage RGBA8888 一致，直接拷贝 */
        memcpy(dst, src, (size_t)n * 4);
    } else {
        /* ARGB8888 ('AR24')：内存 [B,G,R,A] —— 交换 R 与 B */
        for (int i = 0; i < n; i++) {
            unsigned char r = src[i*4+0], g = src[i*4+1], b = src[i*4+2], a = src[i*4+3];
            dst[i*4+0] = b;
            dst[i*4+1] = g;
            dst[i*4+2] = r;
            dst[i*4+3] = a;
        }
    }
}

/* ★ buffer 资源上下文：随 wl_buffer 一起释放，避免 fd / mmap 泄漏 */
struct shm_buffer_ctx {
    struct wl_shm_pool* pool;
    void* data;
    size_t size;
    int fd;
};

static void buffer_release_handler(void* data, struct wl_buffer* buffer) {
    struct shm_buffer_ctx* bctx = (struct shm_buffer_ctx*)data;
    wl_buffer_destroy(buffer);
    if (bctx->pool) wl_shm_pool_destroy(bctx->pool);
    if (bctx->data) munmap(bctx->data, bctx->size);
    if (bctx->fd >= 0) close(bctx->fd);
    free(bctx);
}

static const struct wl_buffer_listener buffer_listener = {
    .release = buffer_release_handler
};

void layer_update_pixels(void* ctx_ptr, const unsigned char* rgba,
                         int width, int height) {
    layer_update_pixels_with_format(ctx_ptr, rgba, width, height, 0);
}

void layer_update_pixels_with_format(void* ctx_ptr, const unsigned char* rgba,
                                     int width, int height, uint32_t force_fmt) {
    struct layer_ctx* ctx = (struct layer_ctx*)ctx_ptr;
    if (!ctx || !ctx->state || !ctx->state->shm) return;
    if (!ctx->configured) return;   /* 等 layer surface 配置完成 */
    if (!rgba || width <= 0 || height <= 0) return;

    struct layer_shell_state* state = ctx->state;
    uint32_t fmt = force_fmt ? force_fmt : choose_upload_format(state);

    int stride = width * 4;
    size_t size = (size_t)stride * (size_t)height;

    /* 1. 创建 memfd 共享内存 */
    int fd = memfd_create("meapet-px", MFD_CLOEXEC);
    if (fd < 0) return;
    if (ftruncate(fd, (off_t)size) < 0) { close(fd); return; }

    void* data = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (data == MAP_FAILED) { close(fd); return; }

    /* 2. 字节重排（ABGR8888 时等价于 memcpy） */
    convert_rgba_to_format(rgba, (unsigned char*)data, width * height, fmt);

    /* 3. 创建 pool + buffer */
    struct wl_shm_pool* pool = wl_shm_create_pool(state->shm, fd, (int32_t)size);
    if (!pool) { munmap(data, size); close(fd); return; }
    struct wl_buffer* buffer = wl_shm_pool_create_buffer(
        pool, 0, width, height, stride, fmt);
    if (!buffer) { wl_shm_pool_destroy(pool); munmap(data, size); close(fd); return; }

    /* 4. 绑定 release 回调：compositor 用完后自动回收 pool / mmap / fd */
    struct shm_buffer_ctx* bctx = (struct shm_buffer_ctx*)calloc(1, sizeof(*bctx));
    if (!bctx) {
        wl_buffer_destroy(buffer);
        wl_shm_pool_destroy(pool);
        munmap(data, size);
        close(fd);
        return;
    }
    bctx->pool = pool;
    bctx->data = data;
    bctx->size = size;
    bctx->fd = fd;
    wl_buffer_add_listener(buffer, &buffer_listener, bctx);

    /* 5. attach + damage + commit */
    wl_surface_attach(ctx->surface, buffer, 0, 0);
    wl_surface_damage(ctx->surface, 0, 0, width, height);
    wl_surface_commit(ctx->surface);
    wl_display_flush(state->display);
}

/* ===================== Phase 3: 双模切换 ===================== */

void layer_clear(void* ctx_ptr) {
    if (!ctx_ptr) return;
    struct layer_ctx* ctx = (struct layer_ctx*)ctx_ptr;
    if (!ctx->state) ctx->state = layer_shell_get_state();
    if (!ctx->state || !ctx->surface) return;

    /* attach NULL buffer → 清空画面（切交互模式时避免与 Qt 窗口重叠成两个桌宠） */
    wl_surface_attach(ctx->surface, NULL, 0, 0);
    wl_surface_damage(ctx->surface, 0, 0, ctx->width, ctx->height);
    wl_surface_commit(ctx->surface);
    wl_display_flush(ctx->state->display);
}

void layer_set_position(void* ctx_ptr, int pos_x, int pos_y) {
    if (!ctx_ptr) return;
    struct layer_ctx* ctx = (struct layer_ctx*)ctx_ptr;
    if (!ctx->state) ctx->state = layer_shell_get_state();
    if (!ctx->state || !ctx->ls) return;

    /* anchor=TOP|LEFT 时，margin 才是精确的「距上 y、距左 x」 */
    zwlr_layer_surface_v1_set_margin(ctx->ls, pos_y, 0, 0, pos_x);
    wl_surface_commit(ctx->surface);
    wl_display_flush(ctx->state->display);
}

void layer_set_size(void* ctx_ptr, int width, int height) {
    if (!ctx_ptr || width <= 0 || height <= 0) return;
    struct layer_ctx* ctx = (struct layer_ctx*)ctx_ptr;
    if (!ctx->state) ctx->state = layer_shell_get_state();
    if (!ctx->state || !ctx->ls) return;

    ctx->width  = width;
    ctx->height = height;
    zwlr_layer_surface_v1_set_size(ctx->ls, width, height);
    wl_surface_commit(ctx->surface);
    wl_display_flush(ctx->state->display);
}

