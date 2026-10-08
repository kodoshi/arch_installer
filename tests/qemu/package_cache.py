"""pacman package cache proxy for controlled QEMU testing.

provides a local HTTP server that serves cached packages to the VM,
ensuring reproducible tests with versioned packages. the proxy can
operate in two modes:
1. cache-through: fetches from upstream mirrors and caches locally
2. offline: serves only pre-cached packages, fails if package missing

this eliminates network dependencies during tests and ensures all
package versions are controlled and reproducible.
"""

import functools
import http.server
import os
import socketserver
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PackageCacheConfig:
    """configuration for the package cache proxy."""

    cache_directory: Path
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
        super().__init__(*args, directory=str(cache_config.cache_directory), **kwargs)

    def do_GET(self):
        # path format: /repo/os/arch/package.pkg.tar.zst
        # example: /core/os/x86_64/bash-5.2.015-3-x86_64.pkg.tar.zst
        request_path = self.path.lstrip("/")
        cache_path = self.cache_config.cache_directory / request_path

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
            with open(cache_path, "rb") as cache_file:
                content = cache_file.read()

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as error:
            self.send_error(500, f"error serving file: {error}")

    def fetch_and_cache(self, request_path: str, cache_path: Path) -> bool:
        """fetch package from upstream mirror and cache locally."""
        for mirror in self.cache_config.upstream_mirrors:
            url = f"{mirror}/{request_path}"
            try:
                upstream_request = urllib.request.Request(
                    url, headers={"User-Agent": "arch-installer-cache/1.0"}
                )
                with urllib.request.urlopen(upstream_request, timeout=60) as response:
                    content = response.read()

                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "wb") as cache_file:
                    cache_file.write(content)

                # also fetch signature if this is a package
                if request_path.endswith(".pkg.tar.zst") and self.cache_config.verify_signatures:
                    self.fetch_signature(request_path, mirror)

                return True
            except Exception:
                continue

        return False

    def fetch_signature(self, package_path: str, mirror: str):
        """fetch package signature file."""
        signature_path = package_path + ".sig"
        signature_cache = self.cache_config.cache_directory / signature_path
        if signature_cache.exists():
            return

        try:
            url = f"{mirror}/{signature_path}"
            upstream_request = urllib.request.Request(
                url, headers={"User-Agent": "arch-installer-cache/1.0"}
            )
            with urllib.request.urlopen(upstream_request, timeout=30) as response:
                content = response.read()

            signature_cache.parent.mkdir(parents=True, exist_ok=True)
            with open(signature_cache, "wb") as cache_file:
                cache_file.write(content)
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
        self._server: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None

    def setup_cache_directory(self):
        """create cache directory structure."""
        self.config.cache_directory.mkdir(parents=True, exist_ok=True)

        # create repo directories
        for repository in ("core", "extra", "multilib"):
            repository_directory = self.config.cache_directory / repository / "os" / "x86_64"
            repository_directory.mkdir(parents=True, exist_ok=True)

    def start(self):
        """start the cache proxy server."""
        self.setup_cache_directory()

        handler = functools.partial(PackageCacheHandler, cache_config=self.config)

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

    def precache_packages(self, packages: list[str], architecture: str = "x86_64"):
        """pre-download packages to ensure they are cached before tests.

        packages should be in format: repo/packagename-version-arch.pkg.tar.zst
        or just packagename (will search across repos).
        """
        for package in packages:
            if "/" in package:
                # full path provided
                repository, package_name = package.split("/", 1)
                self._fetch_package(repository, package_name, architecture)
            else:
                # search in all repos
                for repository in ("core", "extra", "multilib"):
                    if self._fetch_package(repository, package, architecture):
                        break

    def _fetch_package(self, repository: str, package: str, architecture: str) -> bool:
        """fetch a single package to cache."""
        request_path = f"{repository}/os/{architecture}/{package}"
        cache_path = self.config.cache_directory / request_path

        if cache_path.exists():
            return True

        for mirror in self.config.upstream_mirrors:
            url = f"{mirror}/{request_path}"
            try:
                upstream_request = urllib.request.Request(
                    url, headers={"User-Agent": "arch-installer-cache/1.0"}
                )
                with urllib.request.urlopen(upstream_request, timeout=120) as response:
                    content = response.read()

                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "wb") as cache_file:
                    cache_file.write(content)
                return True
            except Exception:
                continue

        return False

    def cache_stats(self) -> dict:
        """get statistics about the cache."""
        total_size = 0
        file_count = 0

        for root, _, files in os.walk(self.config.cache_directory):
            for cache_file in files:
                file_path = Path(root) / cache_file
                total_size += file_path.stat().st_size
                file_count += 1

        return {
            "cache_dir": str(self.config.cache_directory),
            "total_files": file_count,
            "total_size_mb": total_size / (1024 * 1024),
            "offline_mode": self.config.offline_mode,
        }

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exception_type, exception, exception_traceback):
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
