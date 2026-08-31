#ifndef LAYER_SHELL_C_H
#define LAYER_SHELL_C_H

#include <wayland-client.h>

/* ===== Phase 3: 双模切换（清空 / 移动 / 改尺寸）===== */

/* 清空 layer surface 画面（attach NULL buffer） */
void layer_clear(void* ctx);

/* 移动 layer surface 到屏幕坐标 (pos_x, pos_y) */
void layer_set_position(void* ctx, int pos_x, int pos_y);

/* 改变 layer surface 尺寸 */
void layer_set_size(void* ctx, int width, int height);

#ifdef __cplusplus
extern "C" {
#endif

struct layer_shell_state;

/* ===== 基础 API ===== */

/* 创建 layer-shell 状态并绑定全局对象（wl_compositor / wl_shm / zwlr_layer_shell_v1） */
struct layer_shell_state* layer_shell_create(struct wl_display* display);

/* 释放所有资源 */
void layer_shell_destroy(struct layer_shell_state* state);

/* 由 layer_shell_shim.cpp 提供，返回已初始化的全局 state（可能为 NULL） */
struct layer_shell_state* layer_shell_get_state(void);

/* 把 wl_surface 升级为 overlay layer surface（在已有 surface 上挂 layer role） */
void* create_overlay_layer_surface(struct layer_shell_state* state,
                                   struct wl_surface* surface,
                                   int width, int height,
                                   int pos_x, int pos_y,
                                   const char* ns);

/* 销毁 layer surface */
void destroy_overlay_layer_surface(void* layer_surface);

/* ===== layer context API（裸 surface，不占用 Qt 的 xdg_toplevel）===== */

/* 创建裸 wl_surface + layer-shell OVERLAY + 空 input region（穿透） */
void* layer_create_context(struct layer_shell_state* state,
                           int width, int height, int pos_x, int pos_y);

/* 运行时切换穿透：1=空region穿透，0=恢复全surface可点 */
void layer_set_click_through(void* ctx, int enabled);

/* 销毁 layer context */
void layer_destroy_context(void* ctx);

/* ===== Phase 2: 像素上传 ===== */

/* 查询 compositor 是否支持某 wl_shm 格式（小写 format 名，如 "argb8888"） */
int layer_shm_supports_format(struct layer_shell_state* state, const char* format_name);

/* 把 RGBA8888 像素上传到 layer surface（自动选择最佳格式） */
void layer_update_pixels(void* ctx_ptr, const unsigned char* rgba,
                         int width, int height);

/* 强制指定格式上传（0 = 自动选择），用于调试 */
void layer_update_pixels_with_format(void* ctx_ptr, const unsigned char* rgba,
                                     int width, int height, uint32_t format);

#ifdef __cplusplus
}
#endif

#endif /* LAYER_SHELL_C_H */

/* ===== Phase 3: 双模切换（清空 / 移动 / 改尺寸）===== */
void layer_clear(void* ctx);
void layer_set_position(void* ctx, int pos_x, int pos_y);
void layer_set_size(void* ctx, int width, int height);

