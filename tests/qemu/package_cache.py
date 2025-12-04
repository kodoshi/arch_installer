"""pacman package cache proxy for controlled QEMU testing.

provides a local HTTP server that serves cached packages to the VM,
ensuring reproducible tests with versioned packages. the proxy can
operate in two modes:
1. cache-through: fetches from upstream mirrors and caches locally
2. offline: serves only pre-cached packages, fails if package missing

this eliminates network dependencies during tests and ensures all
package versions are controlled and reproducible.
"""

import hashlib
import http.server
import os
import shutil
import socketserver
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PackageCacheConfig:
    """configuration for the package cache proxy."""

    cache_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080
    upstream_mirrors: tuple[str, ...] = (
        "https://geo.mirror.pkgbuild.com",
        "https://mirror.rackspace.com/archlinux",
    )
    offline_mode: bool = False
    verify_signatures: bool = True


class PackageCacheHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler that serves cached packages."""

    cache_config: PackageCacheConfig

    def __init__(self, *args, cache_config: PackageCacheConfig, **kwargs):
        self.cache_config = cache_config
        super().__init__(*args, directory=str(cache_config.cache_dir), **kwargs)

    def do_GET(self):
        # path format: /repo/os/arch/package.pkg.tar.zst
        # example: /core/os/x86_64/bash-5.2.015-3-x86_64.pkg.tar.zst
        request_path = self.path.lstrip("/")
        cache_path = self.cache_config.cache_dir / request_path

        if cache_path.exists():
            self.send_cached_file(cache_path)
            return

        if self.cache_config.offline_mode:
            self.send_error(404, f"package not in cache (offline mode): {request_path}")
            return

        # fetch from upstream and cache
        if self.fetch_and_cache(request_path, cache_path):
            self.send_cached_file(cache_path)
        else:
            self.send_error(502, f"failed to fetch package: {request_path}")

    def send_cached_file(self, cache_path: Path) -> None:
        try:
            with open(cache_path, "rb") as f:
                content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"error serving file: {e}")

    def fetch_and_cache(self, request_path: str, cache_path: Path) -> bool:
        """fetch package from upstream mirror and cache locally."""
        for mirror in self.cache_config.upstream_mirrors:
            url = f"{mirror}/{request_path}"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "arch-installer-cache/1.0"}
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    content = response.read()

                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "wb") as f:
                    f.write(content)

                # also fetch signature if this is a package
                if request_path.endswith(".pkg.tar.zst") and self.cache_config.verify_signatures:
                    self.fetch_signature(request_path, mirror)

                return True
            except Exception:
                continue

        return False

    def fetch_signature(self, package_path: str, mirror: str):
        """fetch package signature file."""
        sig_path = package_path + ".sig"
        sig_cache = self.cache_config.cache_dir / sig_path
        if sig_cache.exists():
            return

        try:
            url = f"{mirror}/{sig_path}"
            req = urllib.request.Request(url, headers={"User-Agent": "arch-installer-cache/1.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read()

            sig_cache.parent.mkdir(parents=True, exist_ok=True)
            with open(sig_cache, "wb") as f:
                f.write(content)
        except Exception:
            pass  # signatures optional for testing


class PackageCacheProxy:
    """manages the package cache HTTP server.

    starts a local HTTP server that serves cached packages to QEMU VMs.
    the server runs in a background thread and can be started/stopped
    as needed for tests.
    """

    def __init__(self, config: PackageCacheConfig):
        self.config = config
        self._server: Optional[socketserver.TCPServer] = None
        self._thread: Optional[threading.Thread] = None

    def setup_cache_directory(self):
        """create cache directory structure."""
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)

        # create repo directories
        for repo in ("core", "extra", "multilib"):
            repo_dir = self.config.cache_dir / repo / "os" / "x86_64"
            repo_dir.mkdir(parents=True, exist_ok=True)

    def start(self):
        """start the cache proxy server."""
        self.setup_cache_directory()

        handler = lambda *args, **kwargs: PackageCacheHandler(
            *args, cache_config=self.config, **kwargs
        )

        self._server = socketserver.TCPServer((self.config.host, self.config.port), handler)
        self._server.allow_reuse_address = True

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """stop the cache proxy server."""
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def get_mirrorlist_content(self, host_ip: str) -> str:
        """generate mirrorlist content pointing to the cache proxy."""
        return f"Server = http://{host_ip}:{self.config.port}/$repo/os/$arch\n"

    def precache_packages(self, packages: list[str], arch: str = "x86_64"):
        """pre-download packages to ensure they are cached before tests.

        packages should be in format: repo/packagename-version-arch.pkg.tar.zst
        or just packagename (will search across repos).
        """
        for pkg in packages:
            if "/" in pkg:
                # full path provided
                repo, pkg_name = pkg.split("/", 1)
                self._fetch_package(repo, pkg_name, arch)
            else:
                # search in all repos
                for repo in ("core", "extra", "multilib"):
                    if self._fetch_package(repo, pkg, arch):
                        break

    def _fetch_package(self, repo: str, package: str, arch: str) -> bool:
        """fetch a single package to cache."""
        request_path = f"{repo}/os/{arch}/{package}"
        cache_path = self.config.cache_dir / request_path

        if cache_path.exists():
            return True

        for mirror in self.config.upstream_mirrors:
            url = f"{mirror}/{request_path}"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "arch-installer-cache/1.0"}
                )
                with urllib.request.urlopen(req, timeout=120) as response:
                    content = response.read()

                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "wb") as f:
                    f.write(content)
                return True
            except Exception:
                continue

        return False

    def cache_stats(self) -> dict:
        """get statistics about the cache."""
        total_size = 0
        file_count = 0

        for root, _, files in os.walk(self.config.cache_dir):
            for f in files:
                file_path = Path(root) / f
                total_size += file_path.stat().st_size
                file_count += 1

        return {
            "cache_dir": str(self.config.cache_dir),
            "total_files": file_count,
            "total_size_mb": total_size / (1024 * 1024),
            "offline_mode": self.config.offline_mode,
        }

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


# list of essential packages to precache for minimal installation tests
ESSENTIAL_PACKAGES = [
    "base",
    "linux",
    "linux-firmware",
    "btrfs-progs",
    "cryptsetup",
    "sbctl",
    "systemd",
    "mkinitcpio",
    "efibootmgr",
    "networkmanager",
    "openssh",
    "sudo",
    "vim",
]
