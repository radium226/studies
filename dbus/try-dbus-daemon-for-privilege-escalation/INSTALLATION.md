# Installation Guide

This guide explains how to install and configure the radium226-run D-Bus daemon for privilege escalation studies.

## Prerequisites

- Linux system with D-Bus and systemd
- Root access for installation
- Python 3.12+ with uv package manager

## Installation Methods

### Method 1: Arch Linux (PKGBUILD)

For Arch Linux users, use the provided PKGBUILD:

```bash
# Build and install the package
makepkg -si

# The package will automatically:
# - Install the Python package and binaries
# - Install D-Bus configuration files
# - Install systemd service file
# - Reload D-Bus and systemd configuration

# Start the service
sudo systemctl enable radium226-command-executor.service
sudo systemctl start radium226-command-executor.service
```

### Method 2: Manual Installation

## Installation Steps

### 1. Install the Python Package

```bash
# Install the package system-wide
sudo uv tool install --from . rund
sudo uv tool install --from . run

# Or install to a specific location
sudo uv sync --frozen
sudo cp .venv/bin/rund /usr/local/bin/
sudo cp .venv/bin/run /usr/local/bin/
sudo chmod +x /usr/local/bin/rund /usr/local/bin/run
```

### 2. Install D-Bus Configuration

```bash
# Copy D-Bus service file
sudo cp dbus-config/system-services/com.radium226.CommandExecutor.service \
    /usr/share/dbus-1/system-services/

# Copy D-Bus security policy
sudo cp dbus-config/system.d/com.radium226.CommandExecutor.conf \
    /etc/dbus-1/system.d/

# Reload D-Bus configuration
sudo systemctl reload dbus
```

### 3. Install Systemd Service (Optional)

For automatic startup on boot:

```bash
# Copy systemd service file
sudo cp dbus-config/radium226-command-executor.service \
    /etc/systemd/system/

# Reload systemd and enable the service
sudo systemctl daemon-reload
sudo systemctl enable radium226-command-executor.service
sudo systemctl start radium226-command-executor.service
```

### 4. Verify Installation

Check that the service is running:

```bash
# Check D-Bus service
sudo systemctl status radium226-command-executor.service

# Check D-Bus registration
dbus-send --system --print-reply --dest=org.freedesktop.DBus \
    /org/freedesktop/DBus org.freedesktop.DBus.ListNames | grep radium226

# Test basic functionality
run exec -- whoami
```

## Configuration Files

### D-Bus Service File
**Location**: `/usr/share/dbus-1/system-services/com.radium226.CommandExecutor.service`

Defines how D-Bus should start the service:
- Service name: `com.radium226.CommandExecutor`
- Executable: `/usr/local/bin/rund`
- User: `root`

### D-Bus Security Policy
**Location**: `/etc/dbus-1/system.d/com.radium226.CommandExecutor.conf`

Defines security permissions:
- Only root can own the service
- Any user can send messages to the service
- Allows introspection and properties access

### Systemd Service File
**Location**: `/etc/systemd/system/radium226-command-executor.service`

Defines system service behavior:
- Starts after D-Bus
- Runs as root
- Restarts on failure
- Security hardening settings

## Usage

Once installed, users can execute commands through the daemon:

```bash
# Basic command execution
run exec -- ls -la /root

# Long-running commands with CTRL+C support
run exec -- tail -f /var/log/syslog

# List active commands
run list

# Attach to a running command
run attach <execution-id>

# Attach to the last command
run attach last
```

## Security Considerations

### For Production Use
⚠️ **WARNING**: This tool is designed for security research and privilege escalation studies. Do not use in production environments without proper security review.

### Security Features
- Commands execute as the original user (not root)
- D-Bus access control via policy files
- Systemd security hardening
- Process isolation

### Potential Risks
- Root daemon with D-Bus access
- User command execution with preserved privileges
- Network accessibility if D-Bus is exposed

## Troubleshooting

### Service Won't Start
```bash
# Check systemd logs
sudo journalctl -u radium226-command-executor.service -f

# Check D-Bus logs
sudo journalctl -u dbus.service -f

# Test manual start
sudo /usr/local/bin/rund
```

### Permission Denied
```bash
# Check D-Bus policy
sudo dbus-send --system --print-reply --dest=com.radium226.CommandExecutor \
    /com/radium226/CommandExecutor org.freedesktop.DBus.Introspectable.Introspect

# Verify file permissions
ls -la /usr/share/dbus-1/system-services/com.radium226.CommandExecutor.service
ls -la /etc/dbus-1/system.d/com.radium226.CommandExecutor.conf
```

### Client Can't Connect
```bash
# Check if service is registered
busctl --system list | grep radium226

# Test D-Bus connection
busctl --system introspect com.radium226.CommandExecutor /com/radium226/CommandExecutor
```

## Uninstallation

```bash
# Stop and disable service
sudo systemctl stop radium226-command-executor.service
sudo systemctl disable radium226-command-executor.service

# Remove configuration files
sudo rm /etc/systemd/system/radium226-command-executor.service
sudo rm /etc/dbus-1/system.d/com.radium226.CommandExecutor.conf
sudo rm /usr/share/dbus-1/system-services/com.radium226.CommandExecutor.service

# Remove binaries
sudo rm /usr/local/bin/rund /usr/local/bin/run

# Reload services
sudo systemctl daemon-reload
sudo systemctl reload dbus
```