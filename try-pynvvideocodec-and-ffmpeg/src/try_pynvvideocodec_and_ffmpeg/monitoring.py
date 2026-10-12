import subprocess
import threading
from collections.abc import Generator
from contextlib import contextmanager

import structlog

log = structlog.get_logger()


class PCIeMonitor:
    @staticmethod
    @contextmanager
    def monitor_pcie(gpu_id: int = 0) -> Generator[None]:
        """Log the GPU's PCIe throughput every second, using `nvidia-smi dmon -s t`.

        `rx_mb_s` is what the GPU receives (host → GPU), `tx_mb_s` what it sends
        (GPU → host). It counts every transfer, including the ones made inside
        PyNvVideoCodec, so it shows whether raw frames cross the bus.
        """
        try:
            process = subprocess.Popen(
                ["nvidia-smi", "dmon", "-s", "t", "-d", "1", "-i", str(gpu_id)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError:
            log.warning("pcie.unavailable", reason="nvidia-smi not found")
            yield
            return

        samples: list[tuple[int, int]] = []

        def read() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                # Lines look like "    0     14     15" (gpu, rxpci, txpci); headers start with "#"
                if line.startswith("#"):
                    continue
                _, rx, tx = line.split()
                if not (rx.isdigit() and tx.isdigit()):
                    continue
                samples.append((int(rx), int(tx)))
                log.info("pcie", rx_mb_s=int(rx), tx_mb_s=int(tx))

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        try:
            yield
        finally:
            process.terminate()
            process.wait()
            thread.join()

        if samples:
            rx, tx = zip(*samples)
            log.info(
                "pcie.summary",
                rx_avg_mb_s=round(sum(rx) / len(rx)),
                rx_max_mb_s=max(rx),
                tx_avg_mb_s=round(sum(tx) / len(tx)),
                tx_max_mb_s=max(tx),
                samples=len(samples),
            )
