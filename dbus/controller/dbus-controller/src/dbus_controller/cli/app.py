from click import command
from subprocess import Popen
import socket
import threading
from time import sleep
from loguru import logger
import os
from pathlib import Path


from sdbus import sd_bus_open






@command()
def app() -> None:
    log_parent_socket, log_child_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    controller_parent_socket, controller_child_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)


    def handle_connection_thread_target(sock: socket.socket) -> None:
        while True:
            input_data = sock.recv(1024)
            if input_data == b'':
                logger.info("Controller disconnected")
                break

            logger.info("Received data from controller (data={data})", data=input_data)
            controller_parent_socket.sendall(input_data)
            output_data = controller_parent_socket.recv(1024)
            logger.info("Sending data to controller (data={data})", data=output_data)
            sock.sendall(output_data)



    def pathrough_thread_target() -> None:
        if os.path.exists("./controller.sock"):
            os.remove("./controller.sock")
        controller_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        controller_socket.bind("./controller.sock")
        while True:
            controller_socket.listen(1)
            logger.info("Waiting for controller connection...")
            conn, _ = controller_socket.accept()
            logger.info("Controller connected")
            handler_connection_thread = threading.Thread(target=handle_connection_thread_target, args=(conn,))
            handler_connection_thread.start()

    command = [
        "dbus-broker", 
            "--controller", str(controller_child_socket.fileno()),
            "--log", str(log_child_socket.fileno()),
            "--machine-id", "00000000000000000000000000000001",
    ]
    process = Popen(
        command,
        pass_fds=[log_child_socket.fileno(), controller_child_socket.fileno()],
    )

    log_child_socket.close()
    controller_child_socket.close()

    def read_log_thread_target() -> None:
        with log_parent_socket:
            while True:
                data = log_parent_socket.recv(1024)
                logger.info("Received log data (data={data})", data=data)
                if not data:
                    break
                print(data.decode('utf-8'), end='', flush=True)

    log_thread = threading.Thread(target=read_log_thread_target)
    log_thread.start()

    pathrough_thread = threading.Thread(target=pathrough_thread_target)
    pathrough_thread.start()

    logger.info("DBus controller started with PID {pid}", pid=process.pid)


    os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={str(Path.cwd())}/controller.sock"

    sleep(1)

    # bus = sd_bus_open()
    # bus.request_name("org.freedesktop.DBus.Controller", 0)

    



    sleep(60)

    process.terminate()
    process.wait()

    log_thread.join()
    pathrough_thread.join()


    log_parent_socket.close()
    controller_parent_socket.close()


