from subprocess import run


def find_ip():
    command = ["curl", "https://api.ipify.org?format=text"]
    ip = run(command, check=True, text=True, capture_output=True).stdout
    return ip