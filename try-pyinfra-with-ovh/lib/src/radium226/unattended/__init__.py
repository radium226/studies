from loguru import logger

# Libraries using loguru stay silent until the application opts in with
# `logger.enable("radium226.unattended")`.
logger.disable(__name__)
