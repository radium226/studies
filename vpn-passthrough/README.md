# Studies / VPN Passthrough

## Goal

The goal of this study is to experiment running apps isolated inside network namespace which is connected to PIA.

We need to check: 
- [x] If we can connect to remote ports
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


## Notes
- We need to use `pyroute2` to get the gateway
- ip route show 0.0.0.0/1
- ip route show result
- [Manual connection with PIA](https://github.com/pia-foss/manual-connections/blob/master/port_forwarding.sh)
- Todo : 
    - [ ] Put openvpn service in `Type=notify`
    - [ ] Setup `PartOf=` for timers
    - [ ] Use a `br0` bridge instead of forwarding everything
    - [ ] Use `JoinsNamespaceOf=`
    - [ ] Forward port from localhost to Networknamespace