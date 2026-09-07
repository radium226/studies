# Data for every host, mesh and foreign_lan alike -- pyinfra auto-loads this
# via the implicit "all" group. See ../inventory.py's _node(): ssh_hostname
# and ssh_key are the only genuinely per-host connection values (they depend
# on the host's own name/ip), so those still live there.
ssh_user = "vagrant"
ssh_known_hosts_file = "/dev/null"
ssh_strict_host_key_checking = "no"
_sudo = True
