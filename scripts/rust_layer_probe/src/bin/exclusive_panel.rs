//! 合成一枚**保留可用区**的层面板：Top 层、贴顶、横向铺满、`exclusive_zone = ZONE`。
//!
//! 只服务一件事：让 `QScreen.availableGeometry()` 与 `.geometry()` 真的分叉，
//! 好在真桌面上量「可用区原点偏移」那一格（`scripts/fidus_positioning_probe/` 的 H16）。
//! 面板内容是一整块纯色，不参与任何判据；进程退出即撤销保留区。
//!
//! 环境变量：`ZONE`（必填，px）、`HEIGHT`（面板自身高度，默认 40）、`TTL_SECS`（默认 60，
//! 到点自己退出，防止我忘了收尸把用户的桌面一直占着）、`LAYER`（`top` 默认 / `overlay`——
//! 后者用来造"别的东西压在桌宠上面"的真遮挡相）。

use std::ffi::CString;
use std::os::fd::{AsFd, FromRawFd, OwnedFd};
use std::ptr;
use std::time::{Duration, Instant};

use wayland_client::globals::{registry_queue_init, GlobalList, GlobalListContents};
use wayland_client::protocol::wl_buffer::WlBuffer;
use wayland_client::protocol::wl_compositor::WlCompositor;
use wayland_client::protocol::wl_output::WlOutput;
use wayland_client::protocol::wl_region::WlRegion;
use wayland_client::protocol::wl_registry::WlRegistry;
use wayland_client::protocol::wl_shm::WlShm;
use wayland_client::protocol::wl_shm::{self, Format};
use wayland_client::protocol::wl_shm_pool::WlShmPool;
use wayland_client::protocol::wl_surface::WlSurface;
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_shell_v1::{
    Layer, ZwlrLayerShellV1,
};
use wayland_protocols_wlr::layer_shell::v1::client::zwlr_layer_surface_v1::{
    Anchor, Event as LayerEvent, ZwlrLayerSurfaceV1,
};

#[derive(Default)]
struct Panel {
    configured: bool,
    width: u32,
    height: u32,
}

// 本探针只关心 configure，其余事件一律空实现（编译器要求逐类型 Dispatch）。
impl Dispatch<WlRegistry, GlobalListContents> for Panel {
    fn event(_: &mut Panel, _: &WlRegistry, _: <WlRegistry as Proxy>::Event, _: &GlobalListContents, _: &Connection, _: &QueueHandle<Panel>) {}
}
impl Dispatch<WlCompositor, ()> for Panel {
    fn event(_: &mut Panel, _: &WlCompositor, _: <WlCompositor as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Panel>) {}
}
impl Dispatch<WlShm, ()> for Panel {
    fn event(_: &mut Panel, _: &WlShm, event: <WlShm as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Panel>) {
        if let wl_shm::Event::Format { format } = event {
            let _ = format_raw(format);
        }
    }
}
impl Dispatch<ZwlrLayerShellV1, ()> for Panel {
    fn event(_: &mut Panel, _: &ZwlrLayerShellV1, _: <ZwlrLayerShellV1 as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Panel>) {}
}
impl Dispatch<ZwlrLayerSurfaceV1, ()> for Panel {
    fn event(data: &mut Panel, proxy: &ZwlrLayerSurfaceV1, event: LayerEvent, _: &(), _: &Connection, _: &QueueHandle<Panel>) {
        if let LayerEvent::Configure { serial, width, height } = event {
            proxy.ack_configure(serial);
            data.configured = true;
            if width > 0 {
                data.width = width;
            }
            if height > 0 {
                data.height = height;
            }
        }
    }
}
impl Dispatch<WlBuffer, ()> for Panel {
    fn event(_: &mut Panel, _: &WlBuffer, _: <WlBuffer as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Panel>) {}
}
impl Dispatch<WlSurface, ()> for Panel {
    fn event(_: &mut Panel, _: &WlSurface, _: <WlSurface as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Panel>) {}
}
impl Dispatch<WlShmPool, ()> for Panel {
    fn event(_: &mut Panel, _: &WlShmPool, _: <WlShmPool as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Panel>) {}
}
impl Dispatch<WlRegion, ()> for Panel {
    fn event(_: &mut Panel, _: &WlRegion, _: <WlRegion as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Panel>) {}
}
impl Dispatch<WlOutput, ()> for Panel {
    fn event(_: &mut Panel, _: &WlOutput, _: <WlOutput as Proxy>::Event, _: &(), _: &Connection, _: &QueueHandle<Panel>) {}
}

fn format_raw(f: WEnum<Format>) -> u32 {
    match f {
        WEnum::Value(v) => v as u32,
        WEnum::Unknown(raw) => raw,
    }
}

fn env_int(name: &str, default: i32) -> i32 {
    match std::env::var(name) {
        Ok(s) => s.trim().parse::<i32>().unwrap_or(default),
        Err(_) => default,
    }
}

fn env_layer() -> (Layer, &'static str) {
    match std::env::var("LAYER").unwrap_or_default().trim().to_ascii_lowercase().as_str() {
        "overlay" => (Layer::Overlay, "overlay"),
        _ => (Layer::Top, "top"),
    }
}

fn shm_alloc(size: usize) -> (OwnedFd, *mut u8) {
    unsafe {
        let name = CString::new("exclusive-panel").unwrap();
        let fd = libc::syscall(libc::SYS_memfd_create, name.as_ptr(), libc::MFD_CLOEXEC) as i32;
        assert!(fd >= 0, "memfd_create failed");
        assert_eq!(libc::ftruncate(fd, size as libc::off_t), 0, "ftruncate failed");
        let addr = libc::mmap(
            ptr::null_mut(),
            size,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED,
            fd,
            0,
        );
        assert_ne!(addr, libc::MAP_FAILED, "mmap failed");
        (OwnedFd::from_raw_fd(fd), addr as *mut u8)
    }
}

fn main() {
    let zone = env_int("ZONE", -1);
    let height = env_int("HEIGHT", 40);
    let ttl = env_int("TTL_SECS", 60);
    let (layer, layer_name) = env_layer();
    if zone < 0 {
        println!("usage: ZONE=<px> HEIGHT=<px> TTL_SECS=<s> LAYER=<top|overlay>（ZONE 必填，0=不保留）");
        std::process::exit(2);
    }
    println!("ZONE_REQ={zone} HEIGHT_REQ={height} LAYER={layer_name} TTL={ttl}");

    let conn = match Connection::connect_to_env() {
        Ok(c) => c,
        Err(e) => {
            println!("CONNECT=fail {e:?}");
            std::process::exit(1);
        }
    };
    let (globals, mut queue): (GlobalList, EventQueue<Panel>) = match registry_queue_init::<Panel>(&conn) {
        Ok(v) => v,
        Err(e) => {
            println!("REGISTRY=fail {e:?}");
            std::process::exit(1);
        }
    };
    let qh = queue.handle();
    let compositor: WlCompositor = globals.bind(&qh, 1..=4, ()).expect("compositor");
    let shm: WlShm = globals.bind(&qh, 1..=1, ()).expect("shm");
    let layer_shell: ZwlrLayerShellV1 = globals.bind(&qh, 1..=4, ()).expect("layer_shell");

    let mut state = Panel::default();
    queue.roundtrip(&mut state).expect("roundtrip");

    let surface = compositor.create_surface(&qh, ());
    let ls = layer_shell.get_layer_surface(
        &surface,
        Option::<&WlOutput>::None,
        layer,
        "h16-exclusive-panel".to_string(),
        &qh,
        (),
    );
    // 宽度给 0 + 左右都锚 ⇒ 让合成器自己定铺满宽度；高度给死值。
    ls.set_size(0, height as u32);
    ls.set_anchor(Anchor::Top | Anchor::Left | Anchor::Right);
    ls.set_margin(0, 0, 0, 0);
    ls.set_exclusive_zone(zone);
    // 面板不吞输入也无所谓，但别挡住桌宠的抓取：空输入区域。
    let region = compositor.create_region(&qh, ());
    surface.set_input_region(Some(&region));
    region.destroy();
    surface.commit();
    conn.flush().ok();

    let deadline = Instant::now() + Duration::from_secs(3);
    while !state.configured && Instant::now() < deadline {
        let _ = queue.blocking_dispatch(&mut state);
    }
    if !state.configured {
        println!("CONFIGURED=false");
        std::process::exit(3);
    }
    let (w, h) = (state.width.max(1), state.height.max(1));
    println!("CONFIGURED {w}x{h}");

    let stride = (w as usize) * 4;
    let size = stride * (h as usize);
    let (fd, data) = shm_alloc(size);
    unsafe {
        let px = data as *mut u32;
        for i in 0..(w * h) as usize {
            *px.add(i) = 0xFF40_40C0; // 不透明色块，内容不参与判据
        }
    }
    let pool = shm.create_pool(fd.as_fd(), size as i32, &qh, ());
    let buffer = pool.create_buffer(0, w as i32, h as i32, stride as i32, Format::Abgr8888, &qh, ());
    surface.attach(Some(&buffer), 0, 0);
    surface.damage(0, 0, w as i32, h as i32);
    surface.commit();
    conn.flush().ok();
    let _keep = (pool, buffer, fd, data as usize); // 退出前别回收

    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(ttl as u64) {
        let _ = queue.blocking_dispatch(&mut state);
    }
    println!("TTL_DONE {:?}", start.elapsed());
}
