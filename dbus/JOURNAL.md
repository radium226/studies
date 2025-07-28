# D-Bus

* I first started by doing a first `executord` daemon with an `executor` client
* I exposed the `StdOut` ans `StdErr` properties in the `ExecutionInterface`
* When redirecting the `StdOut` to the actual `sys.stdout`, I used `redirect`
    * We need to do some `select` stuff to be able to stop the `os.read`
    * The same with `asyncio.wait(... return_when=asyncio.FIRST_COMPLETED)` in case of async code
* Also, when TTY, we have to use `os.createpty()` instead of `os.pipe()` (because of buffering stuff, that we can see when using `tr`, but not with `cat`)
* After that, I tried to pass directly the `sys.stdin` and `sys.stdout` directly to the `Execute` method
* But `dbus_fast` cannot handle more than one file descriptor per call (see [this issue](https://github.com/Bluetooth-Devices/dbus-fast/pull/484))
* So I switched to `sdbus`
* It works weel, but lot of SIGEV if we go offroad
    * We need to use `export_with_manager`