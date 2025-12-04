# Troubleshooting

## Boot Issues

**Secure Boot rejects UKIs:**

```bash
sbctl verify /efi/EFI/Linux/*.efi
sbctl sign-all
```

**Snapshot UKI won't boot:**

```bash
snapper -c root list           # Check if snapshot exists
manage-snapshot-ukis refresh   # Regenerate UKIs
```

## Disk Space

**EFI partition full:**

```bash
manage-snapshot-ukis space     # Check usage
manage-snapshot-ukis cleanup   # Remove snapshot UKIs
```

**Root partition full (snapshots):**

```bash
snapper -c root list               # See snapshots
snapper -c root cleanup timeline   # Clean based on limits
btrfs filesystem usage /           # Check actual usage
```

## Encryption

**Forgot LUKS password:**

- No recovery possible without password
- Wipe and reinstall: `WIPE_METHOD=1`

**Slow unlock:**

- argon2id is intentionally slow
- Reduce `pbkdf_time_ms` in config for faster (less secure) unlock

## Network

**No connectivity after install:**

```bash
systemctl status NetworkManager
systemctl enable --now NetworkManager
nmcli device wifi list
nmcli device wifi connect "SSID" password "password"
```

## Services

**Service won't start:**

```bash
systemctl status <service>
journalctl -xeu <service>
```

## Verification Failures

Run the verification tool:

```bash
verify-install --fix --verbose
```

Or via make:

```bash
make verify
```
