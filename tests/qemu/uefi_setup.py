"""UEFI Secure Boot state helpers for the QEMU VMs."""

from tests.qemu.vm import QemuVm


def read_efi_variable_byte(vm: QemuVm, variable_path: str) -> int:
    """efivarfs prefixes each value with 4 attribute bytes, so the value is the fifth byte.
    Returns -1 when the variable does not exist."""
    _exit_code, stdout, _ = vm.run_ssh_command(
        f"[ -f {variable_path} ] && od -An -t u1 -j4 -N1 {variable_path} || echo -1",
        timeout=10,
    )
    try:
        return int(stdout.strip())
    except ValueError:
        return -1


def get_verbose_secure_boot_status(vm: QemuVm) -> dict:
    result = {
        "setup_mode": False,
        "secure_boot_enabled": False,
        "pk_present": False,
        "keys_created": False,
        "all_signed": False,
        "details": "",
    }

    print("    reading UEFI variables directly...")

    # read SetupMode efivar
    setup_mode_variable = "/sys/firmware/efi/efivars/SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    setup_byte = read_efi_variable_byte(vm, setup_mode_variable)
    result["setup_mode"] = setup_byte == 1
    print(
        f"      SetupMode efivar: {setup_byte} ({'SETUP MODE' if setup_byte == 1 else 'USER MODE'})"
    )

    # read SecureBoot efivar
    secure_boot_variable = (
        "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    )
    secureboot_byte = read_efi_variable_byte(vm, secure_boot_variable)
    result["secure_boot_enabled"] = secureboot_byte == 1
    print(
        f"      SecureBoot efivar: {secureboot_byte} ({'ENABLED' if secureboot_byte == 1 else 'DISABLED'})"
    )

    # check for PK enrollment via efivar
    _exit_code, stdout, _ = vm.run_ssh_command(
        "ls /sys/firmware/efi/efivars/PK-* 2>/dev/null | wc -l",
        timeout=10,
    )
    try:
        pk_count = int(stdout.strip())
        result["pk_present"] = pk_count > 0
        print(
            f"      PK variables found: {pk_count} ({'ENROLLED' if pk_count > 0 else 'NOT ENROLLED'})"
        )
    except ValueError:
        result["pk_present"] = False
        print("      PK variables: UNKNOWN")

    # check if sbctl keys exist
    key_paths = [
        "/var/lib/sbctl/keys/PK/PK.key",
        "/var/lib/sbctl/keys/KEK/KEK.key",
        "/var/lib/sbctl/keys/db/db.key",
    ]
    keys_found = 0
    for key_path in key_paths:
        code, _, _ = vm.run_ssh_command(f"test -f {key_path}", timeout=10)
        if code == 0:
            keys_found += 1
    result["keys_created"] = keys_found == len(key_paths)
    print(
        f"      sbctl keys: {keys_found}/{len(key_paths)} ({'ALL PRESENT' if keys_found == len(key_paths) else 'INCOMPLETE'})"
    )

    # check if boot files are signed
    code, stdout, _ = vm.run_ssh_command("sbctl verify 2>&1", timeout=30)
    result["all_signed"] = code == 0
    if code == 0:
        print("      boot files: ALL SIGNED")
    else:
        # count unsigned files
        unsigned_count = stdout.lower().count("not signed")
        print(f"      boot files: {unsigned_count} UNSIGNED")

    result["details"] = (
        f"SetupMode={setup_byte}, SecureBoot={secureboot_byte}, "
        f"PK={result['pk_present']}, keys={keys_found}/3, signed={result['all_signed']}"
    )

    return result


def verify_secure_boot_properly_configured(vm: QemuVm) -> bool:
    """Checks key enrollment and signing, not the SecureBoot variable: OVMF can report
    enforcement as off even with the keys enrolled."""
    status = get_verbose_secure_boot_status(vm)

    # critical checks:
    # 1. keys must be created
    if not status["keys_created"]:
        print("    ✗ FAIL: sbctl keys not created")
        return False

    # 2. must NOT be in setup mode (keys enrolled)
    if status["setup_mode"]:
        print("    ✗ FAIL: still in UEFI setup mode (keys not enrolled)")
        return False

    # 3. all boot files must be signed
    if not status["all_signed"]:
        print("    ✗ FAIL: not all boot files are signed")
        return False

    print("    ✓ PASS: secure boot properly configured")
    print("      note: SecureBoot enforcement in QEMU/OVMF may show disabled")
    print("      key enrollment and signing verified via efivars")

    return True


def verify_setup_mode_before_install(vm: QemuVm) -> bool:
    print("\n    verifying UEFI setup mode for installation...")

    setup_mode_variable = "/sys/firmware/efi/efivars/SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    setup_byte = read_efi_variable_byte(vm, setup_mode_variable)

    if setup_byte == 1:
        print("    ✓ UEFI is in setup mode - ready for key enrollment")
        return True
    else:
        print(f"    ✗ UEFI is NOT in setup mode (SetupMode={setup_byte})")
        print("      key enrollment may fail")
        return False


def print_secure_boot_summary(vm: QemuVm, phase: str = "") -> None:
    header = f"SECURE BOOT STATUS{' - ' + phase if phase else ''}"
    print(f"\n    {'=' * 50}")
    print(f"    {header}")
    print(f"    {'=' * 50}")

    status = get_verbose_secure_boot_status(vm)

    print(f"\n    Summary: {status['details']}")
    print(f"    {'=' * 50}\n")
