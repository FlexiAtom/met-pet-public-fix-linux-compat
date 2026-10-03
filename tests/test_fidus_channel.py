"""fidus 取件路径的回归测试：离线，注入假的 opener / runner。

这里守的是"三步缺一不可"这件事本身，不是网络通不通：钉住的 URL 文本、边车与
代码里所钉哈希的比对、核验必须**先于** pip、装完必须回读 `__git_commit__`。
任何一步被跳过、或被写成"尽量"，这个文件就该红。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from meapet import fidus_channel as fc

_WHEEL_ASSET_URL = "https://api.github.com/repos/FlexiAtom/fidus/releases/assets/111"
_SIDECAR_ASSET_URL = "https://api.github.com/repos/FlexiAtom/fidus/releases/assets/222"
_METADATA_URL = (
    "https://api.github.com/repos/FlexiAtom/fidus/releases/tags/v0.1.0-beta.2"
)
_GOOD_SIDECAR = f"{fc.FIDUS_WHEEL_SHA256}  {fc.FIDUS_WHEEL_NAME}\n"


def _metadata(wheel_name: str | None = None, sidecar_name: str | None = None) -> bytes:
    """release 上就是两只资产，元数据得同时答出它们，否则边车那一步会假失败。"""
    return json.dumps(
        {
            "assets": [
                {
                    "name": fc.FIDUS_WHEEL_NAME if wheel_name is None else wheel_name,
                    "url": _WHEEL_ASSET_URL,
                },
                {
                    "name": fc.FIDUS_SIDECAR_NAME if sidecar_name is None else sidecar_name,
                    "url": _SIDECAR_ASSET_URL,
                },
            ]
        }
    ).encode()


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._whole = payload
        self._chunks = [
            payload[start : start + fc._CHUNK_BYTES]
            for start in range(0, len(payload), fc._CHUNK_BYTES)
        ]
        self._index = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._whole
        if self._index >= len(self._chunks):
            return b""
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _Net:
    """假传输：按 URL 分发内容，并可指名让某条路由失败。"""

    def __init__(
        self,
        *,
        wheel: bytes = b"WHEEL-BYTES",
        sidecar: str = _GOOD_SIDECAR,
        metadata: bytes | None = None,
        broken: tuple[str, ...] = (),
    ) -> None:
        self.responses = {
            _METADATA_URL: metadata if metadata is not None else _metadata(),
            _WHEEL_ASSET_URL: wheel,
            _SIDECAR_ASSET_URL: sidecar.encode("utf-8"),
            fc.FIDUS_WHEEL_URL: wheel,
            fc.FIDUS_SIDECAR_URL: sidecar.encode("utf-8"),
        }
        self.broken = broken
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, timeout: float, headers):
        self.calls.append((url, dict(headers)))
        if any(marker in url for marker in self.broken):
            raise OSError(f"注入的故障：{url}")
        if url not in self.responses:
            raise AssertionError(f"取件路径请求了预期之外的地址：{url}")
        return _Response(self.responses[url])

    @property
    def requested_urls(self) -> list[str]:
        return [url for url, _headers in self.calls]

    def headers_for(self, url: str) -> dict[str, str]:
        return next(headers for asked, headers in self.calls if asked == url)


class _Runner:
    """假 subprocess.run：记录命令，按命令形态给返回码与输出。"""

    def __init__(
        self,
        *,
        sha256sum_rc: int = 0,
        pip_rc: int = 0,
        identity: str = f"0.1.0-beta.2\n{fc.FIDUS_GIT_COMMIT}\n",
        identity_rc: int = 0,
    ) -> None:
        self.sha256sum_rc = sha256sum_rc
        self.pip_rc = pip_rc
        self.identity = identity
        self.identity_rc = identity_rc
        self.commands: list[list[str]] = []
        self.cwd_of_sha256sum: str | None = None

    def __call__(self, command, **kwargs):
        command = list(command)
        self.commands.append(command)
        if command[0] == "sha256sum":
            self.cwd_of_sha256sum = kwargs.get("cwd")
            return subprocess.CompletedProcess(
                command, self.sha256sum_rc, stdout="", stderr="注入的校验失败"
            )
        if command[1:4] == ["-m", "pip", "install"]:
            return subprocess.CompletedProcess(
                command, self.pip_rc, stdout="", stderr="注入的 pip 失败"
            )
        return subprocess.CompletedProcess(
            command, self.identity_rc, stdout=self.identity, stderr="注入的 import 失败"
        )


@pytest.fixture(autouse=True)
def _source_runtime(monkeypatch):
    """取件是源码态/构建期的动作：别让本机是否冻结左右这些断言。"""
    monkeypatch.setattr(fc, "is_frozen", lambda: False)


@pytest.fixture(autouse=True)
def _require_sha256sum(monkeypatch):
    monkeypatch.setattr(fc.shutil, "which", lambda name: f"/usr/bin/{name}")


# --- 第 1 步：钉的是完整 URL -------------------------------------------------


def test_pinned_urls_are_the_exact_declared_direct_links():
    """URL 由 tag 与文件名拼出，但**文本**必须逐字等于发布方声明的那两条。

    改 tag 或改归一形态都会让这条红：直链文本本身就是身份的第一来源。
    """
    assert fc.FIDUS_WHEEL_URL == (
        "https://github.com/FlexiAtom/fidus/releases/download/"
        "v0.1.0-beta.2/fidus-0.1.0b2-cp310-abi3-manylinux_2_35_x86_64.whl"
    )
    assert fc.FIDUS_SIDECAR_URL == f"{fc.FIDUS_WHEEL_URL}.sha256"


def test_release_url_splits_into_project_tag_and_name():
    assert fc._split_release_url(fc.FIDUS_WHEEL_URL) == (
        "FlexiAtom/fidus",
        "v0.1.0-beta.2",
        fc.FIDUS_WHEEL_NAME,
    )


def test_slash_bearing_tag_is_not_cut_in_half():
    """git tag 本身可以含斜杠，按"第 5 段就是 tag"切会解析出一只不相干的资产。"""
    assert fc._split_release_url(
        "https://github.com/o/r/releases/download/release/1.0/x.whl"
    ) == ("o/r", "release/1.0", "x.whl")


@pytest.mark.parametrize(
    "url",
    [
        "https://files.example.invalid/x.whl",
        "https://github.com/o/r/releases/upload/t/n.whl",
        "https://github.com/o/r/releases/download/onlytag",
    ],
)
def test_non_release_url_is_refused_instead_of_guessed(url):
    with pytest.raises(fc.FidusChannelError):
        fc._split_release_url(url)


# --- 传输：两条路由，同一只字节 ---------------------------------------------


def test_download_resolves_the_asset_from_the_pinned_url(tmp_path):
    net = _Net()
    wheel, sidecar, route = fc.download(tmp_path, opener=net)
    assert route == "api 资产路由"
    assert wheel.read_bytes() == b"WHEEL-BYTES"
    assert sidecar.read_text() == _GOOD_SIDECAR
    # 先向 api 要元数据（顺带证明这只资产真在钉的那个 tag 下面），再按资产地址取字节
    assert net.requested_urls[0] == _METADATA_URL
    assert net.headers_for(_METADATA_URL)["Accept"] == "application/vnd.github+json"
    assert net.requested_urls[1] == _WHEEL_ASSET_URL
    assert net.headers_for(_WHEEL_ASSET_URL)["Accept"] == "application/octet-stream"


def test_asset_missing_under_pinned_tag_fails_loudly(tmp_path):
    """tag 下面没有这个文件名 ⇒ 身份在源头就不成立，不能"退而求其次"装别的。"""
    net = _Net(metadata=_metadata(wheel_name="别的.whl"))
    with pytest.raises(fc.FidusChannelError, match="资产"):
        fc.download(tmp_path, opener=net)


def test_api_route_down_falls_back_to_the_direct_url(tmp_path):
    net = _Net(broken=(_WHEEL_ASSET_URL,))
    wheel, _sidecar, route = fc.download(tmp_path, opener=net)
    assert route == "直链"
    assert wheel.read_bytes() == b"WHEEL-BYTES"


def test_both_routes_failing_is_not_silently_swallowed(tmp_path):
    net = _Net(broken=("github.com",))
    with pytest.raises(fc.FidusChannelError, match="取不回来"):
        fc.download(tmp_path, opener=net)


def test_stalled_transfer_is_cut_off_by_the_total_budget():
    """超时算的是整只对象的总预算，不是单次 socket 调用。

    实测这条路均速只有 6 KB/s 且会中途停住：若只靠 socket 超时，每一小段都"没超时"，
    整个取件就能无限挂着。
    """

    class _Endless:
        def read(self, size: int = -1) -> bytes:
            return b"x" * size

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    def opener(url: str, timeout: float, headers):
        return _Endless()

    with pytest.raises(fc._TransportFailure, match="只收到"):
        fc._transfer("https://example.invalid/blob", timeout=0.0, opener=opener)


# --- 第 2 步：边车核验 ------------------------------------------------------


def test_sidecar_format_is_hash_two_spaces_bare_name(tmp_path):
    path = tmp_path / fc.FIDUS_SIDECAR_NAME
    path.write_text(_GOOD_SIDECAR, encoding="utf-8")
    assert fc.parse_sidecar(path) == (fc.FIDUS_WHEEL_SHA256, fc.FIDUS_WHEEL_NAME)


@pytest.mark.parametrize("text", ["", "只有一串", "notahash  x.whl\n", "0" * 63 + "  x.whl\n"])
def test_malformed_sidecar_is_rejected(tmp_path, text):
    path = tmp_path / fc.FIDUS_SIDECAR_NAME
    path.write_text(text, encoding="utf-8")
    with pytest.raises(fc.FidusChannelError):
        fc.parse_sidecar(path)


def _pair(tmp_path: Path, sidecar_text: str = _GOOD_SIDECAR) -> tuple[Path, Path]:
    wheel = tmp_path / fc.FIDUS_WHEEL_NAME
    wheel.write_bytes(b"WHEEL-BYTES")
    sidecar = tmp_path / fc.FIDUS_SIDECAR_NAME
    sidecar.write_text(sidecar_text, encoding="utf-8")
    return wheel, sidecar


def test_verify_runs_sha256sum_c_inside_the_sidecar_directory(tmp_path):
    wheel, sidecar = _pair(tmp_path)
    runner = _Runner()
    assert fc.verify_wheel(wheel, sidecar, runner=runner) == "sha256sum -c"
    assert runner.commands == [["sha256sum", "-c", fc.FIDUS_SIDECAR_NAME]]
    assert runner.cwd_of_sha256sum == str(tmp_path)


def test_sidecar_disagreeing_with_the_pinned_hash_never_reaches_pip(tmp_path):
    """这一条是整条链的意义：轮子与边车出自同一台服务器，它俩自洽不等于身份对。"""
    wheel, sidecar = _pair(tmp_path, f"{'f' * 64}  {fc.FIDUS_WHEEL_NAME}\n")
    runner = _Runner()
    with pytest.raises(fc.FidusChannelError, match="钉住"):
        fc.verify_wheel(wheel, sidecar, runner=runner)
    assert runner.commands == []


def test_sidecar_naming_another_file_is_rejected(tmp_path):
    wheel, sidecar = _pair(tmp_path, f"{fc.FIDUS_WHEEL_SHA256}  别的.whl\n")
    with pytest.raises(fc.FidusChannelError, match="取回的却是"):
        fc.verify_wheel(wheel, sidecar, runner=_Runner())


def test_sha256sum_failure_is_not_treated_as_passing(tmp_path):
    wheel, sidecar = _pair(tmp_path)
    with pytest.raises(fc.FidusChannelError, match="没放过"):
        fc.verify_wheel(wheel, sidecar, runner=_Runner(sha256sum_rc=1))


def test_missing_sha256sum_falls_back_to_recomputing_not_to_skipping(tmp_path, monkeypatch):
    monkeypatch.setattr(fc.shutil, "which", lambda _name: None)
    declared = hashlib.sha256(b"WHEEL-BYTES").hexdigest()
    wheel, sidecar = _pair(tmp_path, f"{declared}  {fc.FIDUS_WHEEL_NAME}\n")
    monkeypatch.setattr(fc, "FIDUS_WHEEL_SHA256", declared)
    assert fc.verify_wheel(wheel, sidecar, runner=_Runner()) == "本进程 hashlib"


def test_missing_sha256sum_still_catches_tampered_bytes(tmp_path, monkeypatch):
    """现场没有 sha256sum 不等于放弃核验——本进程复算同一件事。"""
    monkeypatch.setattr(fc.shutil, "which", lambda _name: None)
    declared = hashlib.sha256(b"WHEEL-BYTES").hexdigest()
    wheel, sidecar = _pair(tmp_path, f"{declared}  {fc.FIDUS_WHEEL_NAME}\n")
    monkeypatch.setattr(fc, "FIDUS_WHEEL_SHA256", declared)
    wheel.write_bytes(b"TAMPERED")
    with pytest.raises(fc.FidusChannelError, match="不符"):
        fc.verify_wheel(wheel, sidecar, runner=_Runner())


# --- 装机与第 3 步：读回 __git_commit__ -------------------------------------


def test_frozen_program_does_not_run_itself_as_python(monkeypatch):
    monkeypatch.setattr(fc, "is_frozen", lambda: True)
    assert fc.install_command(Path("/tmp/x.whl")) is None


def test_install_command_refuses_dependency_resolution():
    """放开解析会顺着索引把 numpy 抬到 2.x，撞穿 linux_requirements.txt 里那条 numpy<2。"""
    command = fc.install_command(Path("/tmp/x.whl"))
    assert command[0] == sys.executable
    assert command[1:4] == ["-m", "pip", "install"]
    assert "--no-deps" in command
    assert "--index-url" not in command


def test_install_fidus_runs_the_three_steps_in_order(tmp_path):
    runner = _Runner()
    report = fc.install_fidus(tmp_path, opener=_Net(), runner=runner)
    assert "0.1.0-beta.2" in report and fc.FIDUS_GIT_COMMIT in report
    assert [command[0] for command in runner.commands] == [
        "sha256sum",
        sys.executable,
        sys.executable,
    ]
    assert runner.commands[1][1:4] == ["-m", "pip", "install"]
    assert "fidus.__git_commit__" in runner.commands[2][2]


def test_install_fidus_aborts_before_pip_when_the_hash_is_wrong(tmp_path):
    """反证：核验失败必须停在装之前，否则"钉住哈希"就只剩日志作用。"""
    runner = _Runner()
    net = _Net(sidecar=f"{'a' * 64}  {fc.FIDUS_WHEEL_NAME}\n")
    with pytest.raises(fc.FidusChannelError):
        fc.install_fidus(tmp_path, opener=net, runner=runner)
    assert runner.commands == []


def test_install_fidus_rejects_a_wheel_whose_commit_is_not_the_pinned_one(tmp_path):
    runner = _Runner(identity="0.1.0-beta.2\nv0.1.0-beta.1-61-gf75e05b\n")
    with pytest.raises(fc.FidusChannelError, match="最后一环"):
        fc.install_fidus(tmp_path, opener=_Net(), runner=runner)


def test_install_fidus_reports_when_pip_fails(tmp_path):
    with pytest.raises(fc.FidusChannelError, match="pip 安装失败"):
        fc.install_fidus(tmp_path, opener=_Net(), runner=_Runner(pip_rc=1))


def test_install_fidus_refuses_to_install_into_a_frozen_program(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "is_frozen", lambda: True)
    with pytest.raises(fc.FidusChannelError, match="冻结态"):
        fc.install_fidus(tmp_path, opener=_Net(), runner=_Runner())


def test_identity_probe_fails_loudly_when_import_breaks():
    with pytest.raises(fc.FidusChannelError, match="import 不进去"):
        fc.read_installed_identity(runner=_Runner(identity_rc=1))


def test_identity_probe_needs_both_strings():
    with pytest.raises(fc.FidusChannelError, match="两个身份串"):
        fc.read_installed_identity(runner=_Runner(identity="0.1.0-beta.2\n"))


# --- 落点 -------------------------------------------------------------------


def test_cache_dir_lives_outside_the_repository():
    """把 wheel 存进仓库＝二次分发，发布方明令拒绝，所以落点必须在本仓之外。"""
    root = Path(__file__).resolve().parents[1]
    assert not (fc.default_cache_dir() / fc.FIDUS_WHEEL_NAME).is_relative_to(root)
