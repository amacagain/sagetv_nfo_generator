
## 🎯 Features

* **SageTV Integration:** Pulls detailed metadata (Title, Season/Episode, Description, Year) directly from the SageTV API.

* **NFO Generation:** Creates Kodi-compliant `.nfo` files for robust metadata importing.

* **Symbolic Link Creation:** Links to the original media without duplicating large files, saving disk space.

* **Change Detection:** Skips files that have already been processed and whose modification time (`mtime`) has not changed since the last run, ensuring fast execution.

* **Cleanup:** Automatically removes orphaned symlinks and NFOs if the original SageTV file is deleted or no longer found.

* **Separation of Concerns:** The core generation logic is separate from media server trigger calls.

## 🛠️ Prerequisites

1.  **Python 3.x:** Installed and accessible via your system's PATH.

2.  **Python `requests` library:** Used for API communication with both SageTV and Jellyfin.

### Installing Libraries

Open your terminal or command prompt and run:

pip install requests


## ⚙️ Configuration

The project uses two separate configuration files to maintain the separation between media generation and media server updates.

### 1. Core Configuration (`config.json`)

This file configures the connection to your SageTV server and defines the output structure.

| Field | Description | Required | Notes |
| :--- | :--- | :--- | :--- |
| `SAGE_HOST` | The IP address or hostname of your SageTV server. | Yes | |
| `SAGE_PORT` | The port SageTV is running on (usually `8080`). | Yes | |
| `SAGE_USER` / `SAGE_PASS` | Username and password for basic authentication. | Yes | |
| `ROOT_PATH` | **The directory where the symlinks and NFOs will be created.** | Yes | This folder must be configured as a media library source in your media server (Jellyfin/Plex/Kodi). |
| `FLAT_MOVIE_STRUCTURE` | If `true`, movies are placed directly in the `Movies` folder. If `false`, they get their own subfolder. | No | Default: `false` |
| `VERBOSITY_LEVEL` | Logging level (0=Critical, 1=Info, 2=Debug/File Logging). | No | Default: `1` |

### 2. Jellyfin Trigger Configuration (`jellyfin_config.json`)

This file is only required if you intend to use the separate `jellyfin_trigger.py` script to automatically initiate a library scan.

| Field | Description | Required | Notes |
| :--- | :--- | :--- | :--- |
| `JELLYFIN_HOST` / `JELLYFIN_PORT` | Address and port of your Jellyfin server. | Yes | |
| `JELLYFIN_API_KEY` | **Crucial:** An API key generated within your Jellyfin Dashboard (under API Keys) with **Library Access** permissions. | Yes | **Never share this key publicly.** |

## 🚀 Running the Program

### **⚠️ CRITICAL: Admin Privileges are Required on Windows**

The core script, `sagex_nfo_generator.py`, relies on the Python `os.symlink()` function to create **symbolic links**.

* On **Windows**, creating symbolic links typically requires **Administrator privileges** by default.

* If you do not run the script with elevation (Run as Administrator), you will encounter a `PermissionError` when the script attempts to create links.

**To run the program successfully on Windows, you must execute the script from an elevated Command Prompt or PowerShell window.**

### Execution Steps

The recommended way to run the process is in two sequential steps to ensure all files are created before the media server starts scanning.

| Step | Script | Description |
| :--- | :--- | :--- |
| **1. Generate Files** | `python sagex_nfo_generator.py` | This script connects to SageTV, checks for new/updated files, creates the necessary folders, NFOs, and symbolic links, and performs cleanup. |
| **2. Trigger Scan** | `python jellyfin_trigger.py` | *(Optional)* If using Jellyfin, this separate script sends an API command to your server, telling it to immediately scan its libraries for the newly created files. |

**Example Execution (from the script directory):**

Step 1: File Generation and Cleanup

python sagex_nfo_generator.py

Step 2: (Optional) Tell Jellyfin to scan

python jellyfin_trigger.py


## 💻 Compatibility

|

 Operating System | Status | Notes |
| :--- | :--- | :--- |
| **Windows** | **Tested** | Requires running the script as **Administrator** for symbolic link creation. |
| **Linux/macOS** | Likely Compatible | Python and symbolic links generally work out-of-the-box, no root/admin access should be required unless the target directory has restricted permissions. |

