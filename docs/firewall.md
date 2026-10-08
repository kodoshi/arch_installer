# Firewall

The installer sets up UFW (Uncomplicated Firewall) from the `firewall` section of `config.yaml`.

## Configuration

The firewall is configured via the `firewall` section in `config/config.yaml`:

```yaml
firewall:
  enabled: true
  default_incoming: deny
  default_outgoing: allow
  logging: true
  block_icmp: true

  ssh:
    enabled: false
    port: 22
    allowed_from: ''

  allow_rules: []
```

## Shipped Settings

| Setting          | Value        | Why                                         |
| ---------------- | ------------ | ------------------------------------------- |
| Default incoming | **deny**     | No unsolicited connections                  |
| Default outgoing | **allow**    | Normal internet access                      |
| Logging          | **enabled**  | Blocked traffic goes to `/var/log/ufw.log`  |
| ICMP             | **blocked**  | `block_icmp: false` allows ping again       |
| SSH              | **closed**   | Has to be opened explicitly                 |

## SSH Access

SSH is closed in the shipped config. To open it:

```yaml
firewall:
  ssh:
    enabled: true
    port: 22
```

To restrict SSH to a specific network:

```yaml
firewall:
  ssh:
    enabled: true
    port: 22
    allowed_from: '192.168.1.0/24'
```

## ICMP Blocking

`block_icmp: true` (the shipped value) drops ICMP, ping included, which makes the machine harder to discover and fingerprint:

```yaml
firewall:
  block_icmp: true
```

This removes ICMP accept rules from `/etc/ufw/before.rules`:

- `icmp-type destination-unreachable`
- `icmp-type time-exceeded`
- `icmp-type parameter-problem`
- `icmp-type echo-request` (ping)

## Custom Port Rules

Add custom allow rules for specific applications:

```yaml
firewall:
  allow_rules:
    - port: 80
      protocol: tcp
    - port: 443
      protocol: tcp
```

## Post-Installation Usage

```bash
# check status
sudo ufw status verbose

# allow ssh (if needed)
sudo ufw allow ssh

# allow a specific port
sudo ufw allow 8080/tcp

# deny a specific IP
sudo ufw deny from 192.168.1.100

# view logs
sudo journalctl -u ufw
tail -f /var/log/ufw.log
```

## Disabling the Firewall

Via config.yaml:

```yaml
firewall:
  enabled: false
```

Or at runtime:

```bash
ENABLE_FIREWALL=false make install
```

Or after installation:

```bash
sudo ufw disable
sudo systemctl disable --now ufw
```

## Re-enabling

```bash
sudo ufw enable
sudo systemctl enable --now ufw
```
