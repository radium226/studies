# D-Bus Configuration Files

This document describes the D-Bus configuration files added for the radium226-run daemon.

## Files Overview

### 1. D-Bus Service File
**Path**: `dbus-config/system-services/com.radium226.CommandExecutor.service`
**Install to**: `/usr/share/dbus-1/system-services/`

```ini
[D-BUS Service]
Name=com.radium226.CommandExecutor
Exec=/usr/local/bin/rund
User=root
```

This file tells D-Bus how to automatically start the service when needed:
- **Name**: The D-Bus service name clients will connect to
- **Exec**: Path to the executable (rund binary)
- **User**: Service runs as root for privilege escalation

### 2. D-Bus Security Policy
**Path**: `dbus-config/system.d/com.radium226.CommandExecutor.conf`
**Install to**: `/etc/dbus-1/system.d/`

This XML configuration defines access permissions:
- Only root can own the CommandExecutor service
- Any user can send messages to the service (required for privilege escalation)
- Allows introspection and properties access
- Standard D-Bus security policy format

### 3. Systemd Service File
**Path**: `dbus-config/radium226-command-executor.service`
**Install to**: `/usr/lib/systemd/system/`

Defines the systemd service:
- **Type=dbus**: Integrates with D-Bus activation
- **BusName**: D-Bus service name for coordination
- **User=root**: Runs with root privileges
- **Security settings**: Basic hardening while preserving functionality

## Installation via PKGBUILD

The `PKGBUILD` file automates installation:

```bash
# Install all configuration files
install -Dm644 dbus-config/system-services/com.radium226.CommandExecutor.service \
    "$pkgdir/usr/share/dbus-1/system-services/com.radium226.CommandExecutor.service"

install -Dm644 dbus-config/system.d/com.radium226.CommandExecutor.conf \
    "$pkgdir/etc/dbus-1/system.d/com.radium226.CommandExecutor.conf"

install -Dm644 dbus-config/radium226-command-executor.service \
    "$pkgdir/usr/lib/systemd/system/radium226-command-executor.service"
```

The `.install` file handles post-installation tasks:
- Reloads systemd and D-Bus configuration
- Provides usage instructions
- Handles service lifecycle during package operations

## Usage After Installation

1. **Enable and start the service**:
   ```bash
   sudo systemctl enable radium226-command-executor.service
   sudo systemctl start radium226-command-executor.service
   ```

2. **Use the client**:
   ```bash
   run exec -- whoami  # Executes as your user, not root
   run list            # List running commands
   run attach last     # Attach to most recent command
   ```

3. **Monitor the service**:
   ```bash
   systemctl status radium226-command-executor.service
   journalctl -u radium226-command-executor.service -f
   ```

## Security Model

The configuration implements a secure privilege escalation model:

1. **Service runs as root**: Has necessary privileges to switch users
2. **D-Bus access control**: Any user can connect (required for escalation)
3. **User context preservation**: Commands execute as the original user
4. **Process isolation**: Each command runs in its own process
5. **Systemd hardening**: Additional security constraints where possible

This setup follows D-Bus best practices while enabling the privilege escalation study scenario.