# Threat Model

This document outlines the security threats this installer defends against and areas for future improvement.

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
5. **User Applications**: Flatpak/Firejail sandboxing not enforced
6. **SMT Disabled**: Some mitigations disable hyperthreading (performance impact)

## Future Improvements

**TODO**: Introduce plausible deniable encryption (PDE) with hidden volumes and a decoy OS, with plausible decoy content.

LUKS does not support this at all, its headers are statically structured, and detectable. This metadata is not considered part of the encrypted volume, so an adversary can read it, list keyslots, and see volume sizes.

VeraCrypt implements hidden volumes but is not ideal for Linux systems, since it's in userspace and not natively supported (not managed by systemd, initramfs, no bootloader integration).

| Priority | Improvement                    | Benefit                           |
| -------- | ------------------------------ | --------------------------------- |
| Medium   | AppArmor profiles              | Application sandboxing            |
| Medium   | Firejail default profiles      | Browser/app sandboxing            |
