"""生成不含私人配置和运行数据的 MeaPet 分享包。

默认只打包运行代码与界面资源。字典、模型权重等大型离线资源需要显式传入
``--include-optional-assets``；Git LFS 指针只会记录在清单中，不会写入 ZIP，
也不会触发任何 LFS 下载。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Iterable, Sequence
import zipfile


APP_NAME = "MeaPet"
MANIFEST_SCHEMA_VERSION = 1

REQUIRED_RELEASE_PATHS = frozenset(
    {
        "pet.py",
        "setup_wizard.py",
        "启动桌宠.bat",
        "打包发布.bat",
        "config.example.json",
        "meapet/__init__.py",
        "wizard/__init__.py",
        "scripts/package_release.py",
    }
)

ALLOWED_ROOT_FILES = frozenset(
    {
        ".python-version",
        "LICENSE",
        "README.md",
        "THIRD-PARTY-NOTICE.md",
        "config.example.json",
        "linux_requirements.txt",
        "pet.py",
        "pyproject.toml",
        "setup_wizard.py",
        "start.sh",
        "uv.lock",
        "vits_requirements.txt",
        "weight.json",
        "启动桌宠.bat",
        "打包发布.bat",
    }
)

ALLOWED_CORE_PREFIXES = (
    "GPT-Sovits/",
    "live2d/",
    "meapet/",
    "sprites/",
    "vits_core/",
    "vits_models/",
    "wizard/",
)

ALLOWED_SCRIPT_FILES = frozenset(
    {
        "scripts/__init__.py",
        "scripts/package_release.py",
    }
)

OPTIONAL_ASSET_PREFIXES = (
    "dic/",
    "models/",
)

MODEL_WEIGHT_SUFFIXES = frozenset(
    {
        ".bin",
        ".ckpt",
        ".onnx",
        ".pt",
        ".pth",
        ".safetensors",
    }
)

PRIVATE_OR_RUNTIME_ROOTS = frozenset(
    {
        ".git",
        ".github",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".vscode",
        "__pycache__",
        "audio_cache",
        "build",
        "design-system",
        "dist",
        "env",
        "htmlcov",
        "screenshots",
        "tests",
        "venv",
        "voice_cache",
        "wheels",
        "_python",
    }
)

PRIVATE_FILE_NAMES = frozenset(
    {
        ".coverage",
        ".env",
        "config.json",
        "config_settings.json",
        "mea_memory.db",
        "ollamasetup.exe",
        "thumbs.db",
    }
)

PRIVATE_SUFFIXES = frozenset(
    {
        ".bak",
        ".db",
        ".db-journal",
        ".db-shm",
        ".db-wal",
        ".key",
        ".log",
        ".pem",
        ".pyc",
        ".pyo",
    }
)

TEXT_SUFFIXES = frozenset(
    {
        "",
        ".bat",
        ".cfg",
        ".css",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".lock",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)

SECRET_PATTERNS = (
    ("OpenAI 风格密钥", re.compile(rb"sk-[A-Za-z0-9_-]{20,}")),
    ("AWS Access Key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("GitHub Token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("Slack Token", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}")),
    (
        "私钥",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)

OBVIOUS_PLACEHOLDER_BYTES = frozenset({b"x", b"X", b"0"})

LFS_HEADER = b"version https://git-lfs.github.com/spec/v1\n"
MAX_LFS_POINTER_BYTES = 4 * 1024
MAX_SECRET_SCAN_BYTES = 5 * 1024 * 1024
HASH_CHUNK_SIZE = 1024 * 1024
ZIP_MIN_EPOCH = 315_532_800


class PackagingError(RuntimeError):
    """发布包无法安全生成。"""


class SensitiveContentError(PackagingError):
    """候选文件中检测到高置信度密钥。"""


@dataclass(frozen=True)
class ReleaseSelection:
    """经过发布边界过滤后的文件选择结果。"""

    included_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    optional_asset_paths: tuple[str, ...]
    lfs_pointer_paths: tuple[str, ...]


@dataclass(frozen=True)
class BuildResult:
    """一次成功发布构建的输出。"""

    zip_path: Path
    checksum_path: Path
    archive_root: str
    selection: ReleaseSelection
    sha256: str


def _normalize_candidate(raw_path: str | os.PathLike[str]) -> str:
    text = os.fspath(raw_path).replace("\\", "/")
    candidate = PurePosixPath(text)
    parts = candidate.parts
    if (
        not parts
        or candidate.is_absolute()
        or ".." in parts
        or parts[0] in {"", "."}
        or ":" in parts[0]
    ):
        raise PackagingError(f"非法候选路径，必须位于项目目录内：{text}")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise PackagingError(f"非法候选路径，必须指向普通文件：{text}")
    return normalized


def _is_private_or_runtime_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    lowered_parts = tuple(part.lower() for part in path.parts)
    if any(part == "__pycache__" for part in lowered_parts):
        return True
    if lowered_parts and lowered_parts[0] in PRIVATE_OR_RUNTIME_ROOTS:
        return True

    name = path.name.lower()
    if name in PRIVATE_FILE_NAMES or name.startswith(".env"):
        return True
    if name.startswith("config") and (name.endswith(".bak") or ".bak." in name):
        return True
    if name.startswith(".coverage") or ".log." in name:
        return True
    return any(name.endswith(suffix) for suffix in PRIVATE_SUFFIXES)


def _is_allowed_release_path(relative: str) -> bool:
    if relative in ALLOWED_ROOT_FILES or relative in ALLOWED_SCRIPT_FILES:
        return True
    return relative.startswith(ALLOWED_CORE_PREFIXES + OPTIONAL_ASSET_PREFIXES)


def _is_optional_asset(relative: str) -> bool:
    if relative.startswith(OPTIONAL_ASSET_PREFIXES):
        return True
    return PurePosixPath(relative).suffix.lower() in MODEL_WEIGHT_SUFFIXES


def _source_path(root: Path, relative: str) -> Path:
    lexical_path = root.joinpath(*PurePosixPath(relative).parts)
    if lexical_path.is_symlink():
        raise PackagingError(f"发布候选不能是符号链接：{relative}")
    resolved_root = root.resolve()
    resolved_path = lexical_path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise PackagingError(f"候选路径越过项目目录：{relative}") from exc
    return resolved_path


def is_git_lfs_pointer(path: Path) -> bool:
    """只读取指针头判断 LFS 文件，不调用 git-lfs，也不下载对象。"""
    try:
        if path.stat().st_size > MAX_LFS_POINTER_BYTES:
            return False
        with path.open("rb") as stream:
            head = stream.read(512)
    except OSError as exc:
        raise PackagingError(f"无法读取候选文件：{path.name}: {exc}") from exc
    return (
        head.startswith(LFS_HEADER)
        and b"\noid sha256:" in head
        and b"\nsize " in head
    )


def collect_release_files(
    root: Path | str,
    candidates: Iterable[str | os.PathLike[str]],
    *,
    include_optional_assets: bool = False,
) -> ReleaseSelection:
    """按白名单和隐私边界选择发布文件。"""
    project_root = Path(root).resolve()
    if not project_root.is_dir():
        raise PackagingError(f"项目目录不存在：{project_root}")

    included: set[str] = set()
    excluded: set[str] = set()
    optional: set[str] = set()
    lfs_pointers: set[str] = set()

    for raw_path in candidates:
        relative = _normalize_candidate(raw_path)
        if relative in included or relative in excluded:
            continue
        if _is_private_or_runtime_path(relative):
            excluded.add(relative)
            continue
        if not _is_allowed_release_path(relative):
            excluded.add(relative)
            continue

        source = _source_path(project_root, relative)
        if not source.is_file():
            excluded.add(relative)
            continue
        if is_git_lfs_pointer(source):
            lfs_pointers.add(relative)
            excluded.add(relative)
            continue
        if _is_optional_asset(relative) and not include_optional_assets:
            optional.add(relative)
            excluded.add(relative)
            continue
        included.add(relative)

    return ReleaseSelection(
        included_paths=tuple(sorted(included)),
        excluded_paths=tuple(sorted(excluded)),
        optional_asset_paths=tuple(sorted(optional)),
        lfs_pointer_paths=tuple(sorted(lfs_pointers)),
    )


def discover_tracked_files(root: Path | str) -> tuple[str, ...]:
    """从 Git 索引读取候选，避免无意纳入未跟踪的私人文件。"""
    project_root = Path(root).resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", os.fspath(project_root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PackagingError(
            "无法读取 Git 文件清单。请在完整仓库中运行打包脚本，"
            "不要从包含私人文件的普通目录直接打包。"
        ) from exc

    paths = [os.fsdecode(raw) for raw in proc.stdout.split(b"\0") if raw]
    if not paths:
        raise PackagingError("Git 文件清单为空，拒绝生成不完整发布包。")
    return tuple(sorted(paths))


def _validate_required_files(selection: ReleaseSelection) -> None:
    missing = sorted(REQUIRED_RELEASE_PATHS - set(selection.included_paths))
    if missing:
        raise PackagingError("缺少发布必需文件：" + "、".join(missing))


def _scan_for_secrets(root: Path, selection: ReleaseSelection) -> None:
    for relative in selection.included_paths:
        path = _source_path(root, relative)
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_SECRET_SCAN_BYTES:
                continue
            data = path.read_bytes()
        except OSError as exc:
            raise PackagingError(f"密钥扫描无法读取文件：{relative}: {exc}") from exc
        if b"\0" in data:
            continue
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(data):
                if _is_obvious_secret_placeholder(match.group(0)):
                    continue
                raise SensitiveContentError(
                    f"检测到高置信度密钥模式（{label}）：{relative}"
                )


def _is_obvious_secret_placeholder(value: bytes) -> bool:
    """只放行 sk- 后全为 x/X/0 的文档占位串。"""
    if not value.startswith(b"sk-"):
        return False
    payload = value[3:]
    return bool(payload) and payload[:1] in OBVIOUS_PLACEHOLDER_BYTES and len(set(payload)) == 1


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _read_project_version(root: Path) -> str:
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    project_block = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", text)
    if project_block:
        match = re.search(
            r"(?m)^version\s*=\s*['\"]([^'\"]+)['\"]\s*$",
            project_block.group(1),
        )
        if match:
            return match.group(1).strip()
    return "0.0.0"


def _git_value(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", os.fspath(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip()


def _git_revision(root: Path) -> str:
    return _git_value(root, "rev-parse", "--short=12", "HEAD") or "unknown"


def _git_is_dirty(root: Path) -> bool:
    return bool(_git_value(root, "status", "--porcelain", "--untracked-files=no"))


def _default_source_epoch(root: Path) -> int:
    configured = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if configured:
        try:
            return max(0, int(configured))
        except ValueError as exc:
            raise PackagingError("SOURCE_DATE_EPOCH 必须是整数秒。") from exc
    commit_epoch = _git_value(root, "show", "-s", "--format=%ct", "HEAD")
    if commit_epoch.isdigit():
        return int(commit_epoch)
    return int(time.time())


def _safe_label(value: str, fallback: str) -> str:
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return label or fallback


def _zip_datetime(source_epoch: int) -> tuple[int, int, int, int, int, int]:
    moment = datetime.fromtimestamp(
        max(int(source_epoch), ZIP_MIN_EPOCH),
        tz=timezone.utc,
    )
    return (
        moment.year,
        moment.month,
        moment.day,
        moment.hour,
        moment.minute,
        moment.second,
    )


def _write_zip_bytes(
    archive: zipfile.ZipFile,
    archive_path: str,
    data: bytes,
    *,
    zip_datetime: tuple[int, int, int, int, int, int],
    executable: bool = False,
) -> None:
    info = zipfile.ZipInfo(archive_path, date_time=zip_datetime)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o755 if executable else 0o644) << 16
    archive.writestr(
        info,
        data,
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    )


def _quick_start_text(profile: str, lfs_count: int) -> str:
    optional_note = (
        "本包已显式包含当前仓库中可用的字典和真实模型权重。"
        if profile == "with-optional-assets"
        else (
            "为控制体积，本包不包含离线字典和模型权重。需要本地 TTS 时，"
            "请按 README 另行配置。"
        )
    )
    return "\n".join(
        [
            "MeaPet 快速开始",
            "================",
            "",
            "Windows：解压全部文件后，双击“启动桌宠.bat”。",
            "Linux：在终端运行 ./start.sh。",
            "首次启动会创建本地环境并打开配置页。",
            "",
            "隐私说明：",
            "- 发布包不包含 config.json；接收者需要自行配置。",
            "- 发布包不包含密钥、聊天数据库、日志、截图、缓存或虚拟环境。",
            f"- 检测到 {lfs_count} 个 Git LFS 指针；这些指针只记录、不打包。",
            f"- {optional_note}",
            "",
            "完整使用方法、依赖和模型说明请查看 README.md。",
            "ZIP 同目录的 .sha256 文件可用于核对下载完整性。",
            "",
        ]
    )


def _manifest(
    root: Path,
    selection: ReleaseSelection,
    *,
    version: str,
    revision: str,
    source_epoch: int,
    profile: str,
    file_metadata: Sequence[dict[str, object]],
) -> dict[str, object]:
    generated_at = datetime.fromtimestamp(
        max(0, int(source_epoch)),
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "application": APP_NAME,
        "version": version,
        "revision": revision,
        "source_dirty": _git_is_dirty(root),
        "source_date_epoch": int(source_epoch),
        "generated_at": generated_at,
        "profile": profile,
        "file_count": len(selection.included_paths),
        "total_uncompressed_bytes": sum(
            int(item["size"]) for item in file_metadata
        ),
        "files": list(file_metadata),
        "optional_assets_omitted": list(selection.optional_asset_paths),
        "lfs_pointers": list(selection.lfs_pointer_paths),
        "excluded_path_count": len(selection.excluded_paths),
    }


def build_release_archive(
    root: Path | str,
    output_dir: Path | str,
    *,
    candidates: Iterable[str | os.PathLike[str]] | None = None,
    include_optional_assets: bool = False,
    version: str | None = None,
    revision: str | None = None,
    source_epoch: int | None = None,
) -> BuildResult:
    """安全、原子且可复现地生成 ZIP 和对应 SHA-256 文件。"""
    project_root = Path(root).resolve()
    release_candidates = (
        tuple(candidates)
        if candidates is not None
        else discover_tracked_files(project_root)
    )
    selection = collect_release_files(
        project_root,
        release_candidates,
        include_optional_assets=include_optional_assets,
    )
    _validate_required_files(selection)
    _scan_for_secrets(project_root, selection)

    resolved_version = (version or _read_project_version(project_root)).strip()
    resolved_revision = (revision or _git_revision(project_root)).strip()
    resolved_epoch = (
        _default_source_epoch(project_root)
        if source_epoch is None
        else int(source_epoch)
    )
    safe_version = _safe_label(resolved_version, "0.0.0")
    safe_revision = _safe_label(resolved_revision, "unknown")
    archive_root = f"{APP_NAME}-{safe_version}"
    archive_name = f"{APP_NAME}-{safe_version}-{safe_revision}.zip"
    profile = "with-optional-assets" if include_optional_assets else "standard"

    metadata: list[dict[str, object]] = []
    for relative in selection.included_paths:
        path = _source_path(project_root, relative)
        try:
            size = path.stat().st_size
            digest = _hash_file(path)
        except OSError as exc:
            raise PackagingError(f"无法读取发布文件：{relative}: {exc}") from exc
        metadata.append({"path": relative, "size": size, "sha256": digest})

    manifest = _manifest(
        project_root,
        selection,
        version=resolved_version,
        revision=resolved_revision,
        source_epoch=resolved_epoch,
        profile=profile,
        file_metadata=metadata,
    )
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    guide_bytes = _quick_start_text(
        profile,
        len(selection.lfs_pointer_paths),
    ).encode("utf-8")

    destination = Path(output_dir)
    if not destination.is_absolute():
        destination = project_root / destination
    destination.mkdir(parents=True, exist_ok=True)
    zip_path = destination / archive_name
    checksum_path = destination / f"{archive_name}.sha256"
    temp_zip = destination / f".{archive_name}.tmp"
    temp_checksum = destination / f".{archive_name}.sha256.tmp"
    zip_time = _zip_datetime(resolved_epoch)

    try:
        temp_zip.unlink(missing_ok=True)
        temp_checksum.unlink(missing_ok=True)
        with zipfile.ZipFile(temp_zip, "w", allowZip64=True) as archive:
            for relative in selection.included_paths:
                source = _source_path(project_root, relative)
                try:
                    data = source.read_bytes()
                except OSError as exc:
                    raise PackagingError(
                        f"压缩时无法读取文件：{relative}: {exc}"
                    ) from exc
                _write_zip_bytes(
                    archive,
                    f"{archive_root}/{relative}",
                    data,
                    zip_datetime=zip_time,
                    executable=relative.endswith(".sh"),
                )

            _write_zip_bytes(
                archive,
                f"{archive_root}/快速开始.txt",
                guide_bytes,
                zip_datetime=zip_time,
            )
            _write_zip_bytes(
                archive,
                f"{archive_root}/RELEASE-MANIFEST.json",
                manifest_bytes,
                zip_datetime=zip_time,
            )

        os.replace(temp_zip, zip_path)
        archive_digest = _hash_file(zip_path)
        temp_checksum.write_text(
            f"{archive_digest}  {zip_path.name}\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temp_checksum, checksum_path)
    except Exception:
        temp_zip.unlink(missing_ok=True)
        temp_checksum.unlink(missing_ok=True)
        raise

    return BuildResult(
        zip_path=zip_path,
        checksum_path=checksum_path,
        archive_root=archive_root,
        selection=selection,
        sha256=archive_digest,
    )


def _human_size(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _print_selection(selection: ReleaseSelection, root: Path) -> None:
    total_size = sum(
        _source_path(root, relative).stat().st_size
        for relative in selection.included_paths
    )
    print(
        f"[MeaPet] 将打包 {len(selection.included_paths)} 个文件，"
        f"未压缩大小约 {_human_size(total_size)}。"
    )
    print(f"[MeaPet] 已排除 {len(selection.excluded_paths)} 个非发布文件。")
    if selection.optional_asset_paths:
        print(
            f"[MeaPet] 已省略 {len(selection.optional_asset_paths)} 个可选大型资源；"
            "如需包含，请加 --include-optional-assets。"
        )
    if selection.lfs_pointer_paths:
        print(
            f"[MeaPet] 检测到 {len(selection.lfs_pointer_paths)} 个 Git LFS 指针，"
            "只报告、不打包、不下载："
        )
        for relative in selection.lfs_pointer_paths:
            print(f"  - {relative}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成不含私人配置、运行数据和无效 LFS 指针的 MeaPet 分享包。"
    )
    parser.add_argument(
        "--output-dir",
        default="dist",
        help="输出目录，默认是项目内的 dist。",
    )
    parser.add_argument(
        "--include-optional-assets",
        action="store_true",
        help="包含本地字典和真实模型权重；LFS 指针仍不会打包。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查并显示文件范围，不生成 ZIP。",
    )
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = (
        Path(args.root).resolve()
        if args.root
        else Path(__file__).resolve().parents[1]
    )
    try:
        candidates = discover_tracked_files(root)
        selection = collect_release_files(
            root,
            candidates,
            include_optional_assets=args.include_optional_assets,
        )
        _validate_required_files(selection)
        _scan_for_secrets(root, selection)
        _print_selection(selection, root)
        if args.dry_run:
            print("[MeaPet] 检查完成；dry-run 未生成任何文件。")
            return 0

        result = build_release_archive(
            root,
            args.output_dir,
            candidates=candidates,
            include_optional_assets=args.include_optional_assets,
        )
    except PackagingError as exc:
        print(f"[MeaPet] 打包失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[MeaPet] 已取消打包。", file=sys.stderr)
        return 130

    print(f"[MeaPet] 打包完成：{result.zip_path}")
    print(f"[MeaPet] 校验文件：{result.checksum_path}")
    print(f"[MeaPet] SHA-256：{result.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
