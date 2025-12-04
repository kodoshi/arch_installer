# Firewall

The installer configures UFW (Uncomplicated Firewall) with security-hardened defaults.

## Configuration

The firewall is configured via the `firewall` section in `config/config.yaml`:

```yaml
firewall:
  enabled: true
  default_incoming: deny
  default_outgoing: allow
  logging: true
  block_icmp: false

  ssh:
    enabled: false
    port: 22
    allowed_from: null

  allow_rules:
    - port: 8080
      protocol: tcp
```

## Default Settings

| Setting          | Value        | Rationale                                   |
| ---------------- | ------------ | ------------------------------------------- |
| Default incoming | **deny**     | Block all unsolicited connections           |
| Default outgoing | **allow**    | Permit normal internet access               |
| Logging          | **enabled**  | Audit security events in `/var/log/ufw.log` |
| ICMP ping        | **allowed**  | Can be blocked via `block_icmp: true`       |
| SSH              | **disabled** | Must be explicitly enabled                  |

## SSH Access

SSH is disabled by default for security. To enable:

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

To block ICMP (ping) requests and reduce network fingerprinting:

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
ENABLE_UFW=false make install
```

Or after installation:

````

## Re-enabling

```bash
sudo ufw enable
sudo systemctl enable --now ufw
````
