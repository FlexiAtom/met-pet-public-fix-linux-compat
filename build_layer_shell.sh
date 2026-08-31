#!/bin/bash
set -e
cd "$(dirname "$0")"

CFLAGS="$(pkg-config --cflags wayland-client)"

echo ">>> 编译 layer_shell_c.c"
gcc -c -fPIC $CFLAGS layer_shell_c.c -o layer_shell_c.o

echo ">>> 编译 wlr-layer-shell-client-protocol.c"
gcc -c -fPIC $CFLAGS wlr-layer-shell-client-protocol.c -o wlr-layer-shell-client-protocol.o

echo ">>> 编译 xdg-shell-client-protocol.c"
gcc -c -fPIC $CFLAGS xdg-shell-client-protocol.c -o xdg-shell-client-protocol.o

echo ">>> 编译 layer_shell_shim.cpp"
g++ -c -fPIC \
    -I/usr/include/qt \
    -I/usr/include/qt/QtGui/5.15.19 \
    -I/usr/include/qt/QtGui \
    -I/usr/include/qt/QtCore \
    -I/usr/include/qt/QtWaylandClient \
    $CFLAGS \
    layer_shell_shim.cpp -o layer_shell_shim.o

echo ">>> 链接 liblayer_shell_shim.so"
g++ -shared -fPIC \
    layer_shell_shim.o \
    layer_shell_c.o \
    wlr-layer-shell-client-protocol.o \
    xdg-shell-client-protocol.o \
    -o liblayer_shell_shim.so \
    $(pkg-config --libs Qt5WaylandClient Qt5Gui Qt5Core wayland-client) \
    -Wno-attributes

echo ">>> 检查未定义符号"
if nm liblayer_shell_shim.so | grep -q " U "; then
    echo "[WARN] 仍存在未定义符号："
    nm liblayer_shell_shim.so | grep " U "
else
    echo "[OK] 无未定义符号"
fi

echo ">>> 验证导出符号"
nm -D liblayer_shell_shim.so | grep -E "layer_shell_init|make_overlay_surface|destroy_overlay_surface|layer_shell_cleanup|get_wl_surface_from_window"

echo ">>> 编译完成"

