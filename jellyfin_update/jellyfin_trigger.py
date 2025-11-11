import requests
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any

# -----------------------------------------------------------------------------
# Configuration and Setup
# -----------------------------------------------------------------------------
CONFIG_FILE_NAME = "jellyfin_config.json"

def load_config() -> Dict[str, Any]:
    """Loads configuration settings from the external JSON file."""
    config_file_path = Path(__file__).resolve().parent / CONFIG_FILE_NAME
    try:
        with open(config_file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.critical(f"FATAL ERROR: Configuration file '{CONFIG_FILE_NAME}' not found. Cannot proceed.")
        raise
    except json.JSONDecodeError as e:
        logging.critical(f"FATAL ERROR: Failed to parse configuration file. Check JSON formatting: {e}")
        raise

def setup_logging(config: Dict[str, Any]):
    """Configures logging for the trigger script."""
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    # File Handler
    log_file_path = Path(__file__).resolve().parent / config['LOG_FILE_NAME']
    file_handler = RotatingFileHandler(
        log_file_path, 
        maxBytes=1024 * 1024, # 1MB
        backupCount=2
    )
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)

def trigger_jellyfin_scan(config: Dict[str, Any]):
    """
    Triggers a library refresh on the configured Jellyfin server using the API.
    """
    host = config.get('JELLYFIN_HOST')
    port = config.get('JELLYFIN_PORT')
    key = config.get('JELLYFIN_API_KEY')

    if not all([host, port, key]):
        logging.error("Jellyfin configuration incomplete. Missing host, port, or API key.")
        return

    # Jellyfin API Endpoint for a full refresh
    url = f"http://{host}:{port}/Library/Refresh"
    headers = {
        "X-MediaBrowser-Token": key,
        "Content-Type": "application/json"
    }

    logging.info(f"Attempting to trigger Jellyfin library scan at {url}...")
    try:
        # POST request with an empty body to initiate refresh
        response = requests.post(url, headers=headers, timeout=15)
        response.raise_for_status()
        logging.info("✅ Successfully triggered Jellyfin library refresh.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to trigger Jellyfin library scan. Check connection, port, and API key. Error: {e}")

# --- Main Execution Block ---
if __name__ == "__main__":
    try:
        config_data = load_config()
        setup_logging(config_data)
        trigger_jellyfin_scan(config_data)
        
    except Exception as main_e:
        critical_logger = logging.getLogger()
        critical_logger.critical(f"A fatal error occurred in the trigger script: {main_e}", exc_info=True)
