import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

state_dir = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "data",
    "location-server",
)

# Get the number of history days from environment variable, default to 7
__history_days_env = os.getenv("LOCATION_SERVER_HISTORY_DAYS", "7")
try:
    HISTORY_DAYS = int(__history_days_env)
except ValueError:
    logger.warning(
        f"Invalid LOCATION_SERVER_HISTORY_DAYS value: {__history_days_env}. Defaulting to 7."
    )
    HISTORY_DAYS = 7
