"""the recovery system on the USB boot drive: the Arch live system, started by a signed UKI.

the recovery partition holds the ISO's arch/ tree. the UKI holds the ISO's kernel and
initramfs and a command line that boots that tree (archisodevice). Secure Boot checks
the UKI, and with cms_verify=y its initramfs checks the live root image against a CMS
signature before using it.

the ISO's own signature is not used: the certificate it is checked against expires (the
2026.01 ISO's on 2026-05-30), after which the live system would refuse to boot. instead
the installer signs the root image with a one-time key, discards the key, and puts its
certificate into the UKI as a second initramfs that replaces the ISO's /codesign.crt.
the certificate is inside the signed UKI, so neither it nor the root image can be
swapped on the drive.
"""

import logging

from arch_installer.executors.base import (
    SBCTL_DB_KEY,
    TARGET_ROOT,
    StepExecutor,
    file_exists,
    write_file,
)
from arch_installer.executors.usb_boot import (
    ARCHISO_DIRECTORY,
    ARCHISO_INITRAMFS,
    ARCHISO_KERNEL,
    ARCHISO_ROOT_IMAGE,
    ARCHISO_VERSION_FILE,
    RECOVERY_MOUNT,
)

logger = logging.getLogger(__name__)

RECOVERY_UKI = "/efi/EFI/recovery/arch-recovery.efi"
RECOVERY_ENTRY = "/efi/loader/entries/arch-recovery.conf"
# inside the target, where ukify runs; not /tmp, which arch-chroot replaces with a tmpfs
RECOVERY_BUILD_DIRECTORY = "/var/tmp/recovery-system"
# on the live system's tmpfs, so the signing key never reaches a disk
RECOVERY_SIGNING_DIRECTORY = "/run/dali/recovery-signing"
SIGNING_CERTIFICATE_DAYS = 36500
# the archiso hook checks against /codesign_CA.crt when present, else /codesign.crt
ARCHISO_CERTIFICATE_FILES = ("codesign.crt", "codesign_CA.crt")
CERTIFICATE_INITRAMFS = "codesign-initramfs.cpio"


def recovery_cmdline(recovery_filesystem_uuid: str) -> str:
    return (
        f"archisobasedir={ARCHISO_DIRECTORY} "
        f"archisodevice=UUID={recovery_filesystem_uuid} cms_verify=y"
    )


def recovery_entry(live_system_version: str) -> str:
    # the sort key places it after the system's own entries, which sort as "arch"
    return f"""title    Arch Linux recovery ({live_system_version})
sort-key recovery
efi      {RECOVERY_UKI.removeprefix("/efi")}
"""


class RecoverySystemStepExecutor(StepExecutor):
    def execute(self) -> None:
        recovery_partition = self._config.usb_boot.recovery_partition
        logger.info("Building the signed recovery system from %s...", recovery_partition)
        filesystem_uuid = self._runner.run(
            f"blkid -s UUID -o value {recovery_partition}"
        ).stdout.strip()
        build_directory = f"{TARGET_ROOT}{RECOVERY_BUILD_DIRECTORY}"
        self._runner.run(f"mkdir -p {RECOVERY_MOUNT} {build_directory}")
        self._runner.run(f"mount {recovery_partition} {RECOVERY_MOUNT}")
        try:
            live_system_version = self._runner.run(
                f"cat {RECOVERY_MOUNT}/{ARCHISO_VERSION_FILE}"
            ).stdout.strip()
            self._sign_root_image(build_directory)
            for boot_file in (ARCHISO_KERNEL, ARCHISO_INITRAMFS):
                self._runner.run(f"cp {RECOVERY_MOUNT}/{boot_file} {build_directory}/")
            self._build_uki(recovery_cmdline(filesystem_uuid))
        finally:
            self._runner.run(f"umount {RECOVERY_MOUNT}", raise_on_nonzero_exit=False)
            self._runner.run(f"rm -rf {build_directory}", raise_on_nonzero_exit=False)

        write_file(
            self._runner, f"{TARGET_ROOT}{RECOVERY_ENTRY}", recovery_entry(live_system_version)
        )
        self._sign_uki()

    def _sign_root_image(self, build_directory: str) -> None:
        signing = RECOVERY_SIGNING_DIRECTORY
        root_image = f"{RECOVERY_MOUNT}/{ARCHISO_ROOT_IMAGE}"
        self._runner.run(f"rm -rf {signing}")
        self._runner.run(f"mkdir -p -m 700 {signing}")
        try:
            self._runner.run(
                "openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes "
                f"-keyout {signing}/key.pem -out {signing}/{ARCHISO_CERTIFICATE_FILES[0]} "
                f"-days {SIGNING_CERTIFICATE_DAYS} -subj '/CN=DALI recovery system' "
                "-addext keyUsage=critical,digitalSignature "
                "-addext extendedKeyUsage=codeSigning,emailProtection"
            )
            self._runner.run(
                f"openssl cms -sign -binary -noattr -in {root_image} "
                f"-signer {signing}/{ARCHISO_CERTIFICATE_FILES[0]} -inkey {signing}/key.pem "
                f"-outform DER -out {root_image}.cms.sig"
            )
        finally:
            self._runner.run(f"rm -f {signing}/key.pem", raise_on_nonzero_exit=False)
        self._runner.run(
            f"cp {signing}/{ARCHISO_CERTIFICATE_FILES[0]} {signing}/{ARCHISO_CERTIFICATE_FILES[1]}"
        )
        self._runner.run(
            f"bsdtar --format newc -cf {build_directory}/{CERTIFICATE_INITRAMFS} "
            f"-C {signing} {' '.join(ARCHISO_CERTIFICATE_FILES)}"
        )
        self._runner.run(f"rm -rf {signing}", raise_on_nonzero_exit=False)

    def _build_uki(self, cmdline: str) -> None:
        build = RECOVERY_BUILD_DIRECTORY
        self._runner.run(f"mkdir -p {TARGET_ROOT}{RECOVERY_UKI.rsplit('/', 1)[0]}")
        # the kernel unpacks the initramfs images in order, so the certificate image,
        # loaded last, replaces the ISO's certificate
        self._runner.run_as_chroot(
            "ukify build "
            f"--linux={build}/{ARCHISO_KERNEL.rsplit('/', 1)[1]} "
            f"--initrd={build}/{ARCHISO_INITRAMFS.rsplit('/', 1)[1]} "
            f"--initrd={build}/{CERTIFICATE_INITRAMFS} "
            f"--cmdline='{cmdline}' "
            f"--output={RECOVERY_UKI}"
        )

    def _sign_uki(self) -> None:
        if not file_exists(self._runner, f"{TARGET_ROOT}{SBCTL_DB_KEY}"):
            logger.warning("No Secure Boot keys: the recovery UKI stays unsigned")
            return
        # -s records the file, so sbctl's pacman hook re-signs it with the other binaries
        self._runner.run_as_chroot(f"sbctl sign -s {RECOVERY_UKI}")
