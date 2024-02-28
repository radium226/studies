from click import command, group
from subprocess import run


@group()
def app():
    pass

@app.command()
def show_ip():
    command = ["curl", "https://api.ipify.org?format=text"]
    run(command, check=True)