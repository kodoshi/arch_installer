# Threat Model

What the installer defends against, what it doesn't, and what could come next.

## Scope

**In Scope**: Single-user desktop/laptop with local adversaries and network threats.

**Out of Scope**: Enterprise, server, multi-tenant, or high-effort high-skill adversaries. Glowies always got tricks up their sleeves.

## Threat Categories

### 1. Physical Access Threats

| Threat                                  | Mitigation                              | Status          |
| --------------------------------------- | --------------------------------------- | --------------- |
| Disk theft                              | LUKS2 full disk encryption              | ✅ Mitigated    |
| Cold boot attack                        | Memory zeroing on free/alloc            | ⚠️ Partial      |
| Evil maid                               | Secure Boot, signed UKIs                | ✅ Mitigated    |
| Evil maid (boot-level OS impersonation) | What do you want me to do about that ?? | ❌ Not in scope |
| Firmware tampering                      | Secure Boot                             | ⚠️ Partial      |
| USB/DMA attack                          | IOMMU forced, lockdown=integrity        | ✅ Mitigated    |
| Disk inspection reveals encryption      | USB boot drive: detached LUKS header, no partition table, random fill, no boot code on the disk ([usb-boot.md](usb-boot.md)) | ✅ With a USB boot drive |

### 2. Network Threats

| Threat                      | Mitigation                          | Status          |
| --------------------------- | ----------------------------------- | --------------- |
| Port scanning               | UFW deny incoming by default        | ✅ Mitigated    |
| Host discovery (ping sweep) | ICMP blocked                        | ✅ Mitigated    |
| Remote exploitation         | UFW, kernel lockdown                | ⚠️ Partial      |
| Man-in-the-middle           | Not addressed (user responsibility) | ❌ Not in scope |

### 3. CPU Hardware Vulnerabilities

| Threat                 | Mitigation                     | Status       |
| ---------------------- | ------------------------------ | ------------ |
| Meltdown               | `pti=on`                       | ✅ Mitigated |
| Spectre v1             | Compiler mitigations in kernel | ⚠️ Partial   |
| Spectre v2             | `spectre_v2=on`                | ✅ Mitigated |
| Spectre v4             | `spec_store_bypass_disable=on` | ✅ Mitigated |
| L1 Terminal Fault      | `l1tf=full,force`              | ✅ Mitigated |
| MDS (Zombieload, etc.) | `mds=full,nosmt`               | ✅ Mitigated |
| SRBDS                  | `srbds=on`                     | ✅ Mitigated |
| TSX Async Abort        | `tsx_async_abort=full,nosmt`   | ✅ Mitigated |

### 4. Memory Corruption

| Threat                     | Mitigation                            | Status     |
| -------------------------- | ------------------------------------- | ---------- |
| Use-after-free (info leak) | `init_on_alloc=1`, `init_on_free=1`   | ⚠️ Partial |
| Uninitialized memory       | Memory zeroing                        | ⚠️ Partial |
| Kernel exploits            | `lockdown=integrity`, hardened kernel | ⚠️ Partial |

### 5. Boot Process Attacks

| Threat                      | Mitigation                                  | Status           |
| --------------------------- | ------------------------------------------- | ---------------- |
| Bootloader tampering        | Secure Boot, UKI signing                    | ✅ Mitigated     |
| Initramfs tampering         | UKI bundles kernel + initramfs              | ✅ Mitigated     |
| Kernel parameter injection  | UKI embeds cmdline                          | ✅ Mitigated     |
| Rollback to vulnerable boot | Not addressed, snapshots are r+w, by choice | ❌ Not in scope  |

## Encryption Details

| Property          | Value                                |
| ----------------- | ------------------------------------ |
| Algorithm         | AES-XTS-PLAIN64                      |
| Key Size          | 512 bits (256-bit AES + 256-bit XTS) |
| PBKDF             | argon2id                             |
| PBKDF Memory      | 1 GB                                 |
| PBKDF Parallelism | 4 threads                            |
| PBKDF Time        | 4000 ms                              |
| Hash              | SHA-512                              |

## Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                        TRUSTED                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   UEFI      │  │  Signed     │  │   LUKS-encrypted    │  │
│  │  Firmware   │──│    UKI      │──│      Root FS        │  │
│  │  (Secure    │  │  (kernel +  │  │   (BTRFS + data)    │  │
│  │   Boot)     │  │  initramfs) │  │                     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │    UNTRUSTED      │
                    │   - Network       │
                    │   - USB devices   │
                    │   - External DMA  │
                    └───────────────────┘
```

## Known Limitations

1. **Passphrase Strength**: Security depends on LUKS passphrase entropy
2. **TPM Not Used**: Since this installer is meant to be dual-boot friendly, TPM is not used, since it heavily clashes with Windows BitLocker.
3. **AppArmor/SELinux**: Not configured by default
4. **User Applications**: Flatpak/Firejail sandboxing not enforced
5. **SMT Disabled**: Some mitigations disable hyperthreading (performance impact)

## Future Improvements

The USB boot drive ([usb-boot.md](usb-boot.md)) already makes the disk itself deniable. The LUKS header and the boot files live on the stick, and the disk holds nothing but ciphertext in random data.

**TODO**: Hidden volumes and a decoy OS, with plausible decoy content.

LUKS has no hidden volume feature. A LUKS header is a fixed, recognizable structure wherever it is stored, and it is not encrypted, so anyone holding it can read the cipher and KDF settings, list the key slots in use and see where the data segment starts. With the USB boot drive, that means whoever holds the stick, not whoever holds the disk.

Detached headers do allow a hidden volume built by hand: a second LUKS volume at an offset inside the first one's free space, with its header kept apart from the first one's. Nothing stops the outer system from writing over the hidden volume, so the outer volume has to be used carefully or read-only.

VeraCrypt has hidden volumes, and its headers are encrypted, so a VeraCrypt volume looks like random data even without a detached header. Linux can unlock one at boot: cryptsetup opens VeraCrypt volumes, hidden ones included, through the kernel's dm-crypt, and systemd-cryptsetup supports them in crypttab (`tcrypt-veracrypt`, `tcrypt-hidden`, `veracrypt-pim=`), which an initramfs reads from `/etc/crypttab.initramfs`. What is missing for this installer:

- cryptsetup cannot create VeraCrypt volumes, only open them, so the installer would need the VeraCrypt tool to format the disk.
- The `rd.luks.*` kernel parameters the UKIs use don't apply to VeraCrypt volumes.
- VeraCrypt's decoy operating system ("hidden operating system") exists only for Windows.
- Mounting the outer volume without VeraCrypt's hidden volume protection can destroy the hidden one.

| Priority | Improvement                    | Benefit                           |
| -------- | ------------------------------ | --------------------------------- |
| Medium   | AppArmor profiles              | Application sandboxing            |
| Medium   | Firejail default profiles      | Browser/app sandboxing            |
