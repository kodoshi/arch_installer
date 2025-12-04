# Desktop Notifications

## Built-in Notifications

### Snapshot Manager

| Event           | Message                                      |
| --------------- | -------------------------------------------- |
| UKIs created    | "Created N bootable snapshot entries"        |
| Creation failed | "Failed to create bootable snapshot entries" |

Triggered by: `manage-snapshot-ukis refresh` or pacman hook.

Disable: `SNAPSHOT_NOTIFY=false manage-snapshot-ukis refresh`

### Dotfiles Sync

| Event          | Message                                  |
| -------------- | ---------------------------------------- |
| Sync started   | "Starting push/pull..."                  |
| Sync succeeded | "Push/Pull completed - vX.Y.Z"           |
| Sync failed    | Error details with troubleshooting hints |

Triggered by: Manual `dotfiles-sync` or daily systemd timer.

Disable: `DOTFILES_NOTIFY=false dotfiles-sync push`

## Additional Notifications (DIY)

### Package Updates Available

`/etc/systemd/system/checkupdates-notify.service`:

```ini
[Unit]
Description=Check for package updates and notify

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'updates=$(checkupdates 2>/dev/null | wc -l); [ "$updates" -gt 0 ] && notify-send -u normal "Package Updates" "$updates updates available"'
User=<your-user>
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus
```

`/etc/systemd/system/checkupdates-notify.timer`:

```ini
[Unit]
Description=Check for updates daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

### Failed Service Notification

Add to any critical service:

```ini
[Unit]
OnFailure=notify-failure@%n.service
```

`/etc/systemd/system/notify-failure@.service`:

```ini
[Unit]
Description=Send failure notification for %i

[Service]
Type=oneshot
ExecStart=/usr/bin/notify-send -u critical "Service Failed" "%i has failed"
User=<your-user>
Environment=DISPLAY=:0
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus
```

### Ideas for More Notifications

| Feature             | Trigger                   | Message                    |
| ------------------- | ------------------------- | -------------------------- |
| Low disk space      | Cron checking `df`        | "Root partition 90% full"  |
| Backup completed    | Backup script             | "Backup completed (2.3GB)" |
| SSH login alerts    | PAM/sshd config           | "SSH login from X.X.X.X"   |
| VPN status          | NetworkManager dispatcher | "VPN connected to work"    |
