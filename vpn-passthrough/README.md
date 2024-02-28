# Studies / VPN Passthrough

## Goal

The goal of this study is to experiment running apps isolated inside network namespace which is connected to PIA.

We need to check: 
- [ ] If we can connect to remote ports
- [ ] How to use of `NetworkNamespacePath` or `JoinsNamespaceOf`
- [ ] How to use `PartOf`, `BindTo`, etc. to drive all the `vpn-passthrough-*.services`
- [ ] How to use the `StopWhenUnneeded`
- [ ] See if D-Bus works


## Usage
Run `make test` to:
- Build the `dummy-app` package
- Build the `vpn-passthrough` package
- Install both of them
- Run `systemd-run` to display the IP