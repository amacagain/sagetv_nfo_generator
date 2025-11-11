import requests
import os
import xml.etree.ElementTree as ET
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional, Set, Any, Tuple
import json
import time
import re 

# -----------------------------------------------------------------------------
# Configuration File Constant
# -----------------------------------------------------------------------------
CONFIG_FILE_NAME = "config.json" # <-- Corrected to use the main generator config
# -----------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    """Loads configuration settings from the external JSON file."""
    config_file_path = Path(__file__).resolve().parent / CONFIG_FILE_NAME
    try:
        with open(config_file_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            logging.debug(f"Configuration loaded from {CONFIG_FILE_NAME}.")
            return config
    except FileNotFoundError:
        # NOTE: Logging setup hasn't run yet, so use print/raise
        print(f"FATAL ERROR: Configuration file '{CONFIG_FILE_NAME}' not found. Cannot proceed.")
        raise
    except json.JSONDecodeError as e:
        print(f"FATAL ERROR: Failed to parse configuration file. Check JSON formatting: {e}")
        raise

def setup_logging(config: Dict[str, Any]):
    """Configures logging based on the verbosity level defined in the config."""
    level = config.get('VERBOSITY_LEVEL', 1)
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    root_logger = logging.getLogger()
    
    # Clear existing handlers if script is run in an interactive environment
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    if level == 0:
        root_logger.setLevel(logging.CRITICAL)
    elif level >= 1:
        root_logger.setLevel(logging.INFO)
        
        # Console Handler (Level 1 and above)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(log_formatter)
        root_logger.addHandler(console_handler)

        if level == 2:
            # File Handler (Level 2 only)
            log_file_name = config.get('LOG_FILE_NAME', 'sagex_generator.log')
            max_size_mb = config.get('MAX_LOG_SIZE_MB', 1)
            max_count = config.get('MAX_LOG_COUNT', 5)
            
            log_file_path = Path(__file__).resolve().parent / log_file_name
            file_handler = RotatingFileHandler(
                log_file_path, 
                maxBytes=max_size_mb * 1024 * 1024, 
                backupCount=max_count
            )
            file_handler.setFormatter(log_formatter)
            root_logger.addHandler(file_handler)

# ---------------------

class SageXConverter:
    """
    Handles API interaction, metadata parsing, NFO creation, symbolic link creation,
    and change detection/cleanup based on the prompt specifications.
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # SageTV Configuration
        self.auth = (config['SAGE_USER'], config['SAGE_PASS'])
        self.host = config['SAGE_HOST']
        self.port = config['SAGE_PORT']
        self.page_size = config.get('PAGE_SIZE', 100)
        
        # File/Folder Configuration
        self.root_path = Path(config['ROOT_PATH'])
        self.flat_movie_structure = config.get('FLAT_MOVIE_STRUCTURE', False)
        self.tv_shows_root = self.root_path / "TV Shows"
        self.movies_root = self.root_path / "Movies"
        
        # Tracks shows processed in the current run to prevent multiple tvshow.nfo writes
        self.processed_tv_shows: Set[str] = set()
        
        # State management setup
        self.state_file_path = Path(__file__).resolve().parent / config.get('STATE_FILE_NAME', 'sagex_state.json')
        self.processed_state: Dict[str, Dict[str, Any]] = self._load_state()
        
        self._ensure_root_directories()
        
    def _ensure_root_directories(self):
        """Creates the main root and sub-directories."""
        logging.info(f"Ensuring root directories exist: {self.root_path}")
        try:
            self.tv_shows_root.mkdir(parents=True, exist_ok=True)
            self.movies_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logging.error(f"Failed to create directories: {e}")
            raise
            
    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        """3.2: Loads the processed files state from JSON."""
        if self.state_file_path.exists():
            try:
                with open(self.state_file_path, 'r', encoding='utf-8') as f:
                    logging.info("Loaded previous processing state from JSON.")
                    return json.load(f)
            except (IOError, json.JSONDecodeError) as e:
                logging.warning(f"Could not load or parse state file, starting fresh. Error: {e}")
                return {}
        return {}

    def _save_state(self):
        """3.2: Saves the current processed files state to JSON."""
        try:
            with open(self.state_file_path, 'w', encoding='utf-8') as f:
                json.dump(self.processed_state, f, indent=4)
            logging.info(f"Successfully saved {len(self.processed_state)} file records to state file.")
        except IOError as e:
            logging.error(f"Failed to save state file: {e}")

    def _clean_directory_name(self, name: str) -> str:
        """
        3.3: Removes illegal characters for Windows directory/file names and cleans up.
        """
        # Characters illegal in Windows file/folder names: <, >, :, ", /, \, |, ?, *
        illegal_chars = set(['<', '>', ':', '"', '/', '\\', '|', '?', '*'])
        
        cleaned_name = ''.join(['-' if c in illegal_chars else c for c in name])
        
        cleaned_name = cleaned_name.strip()
        while cleaned_name and (cleaned_name.endswith('.') or cleaned_name.endswith(' ')):
            cleaned_name = cleaned_name.rstrip('. ').strip()
            
        return cleaned_name if cleaned_name else "UnknownMedia"
    
    def _parse_sxxeyy(self, filename: str) -> Optional[Tuple[int, int]]:
        """
        4.2: Parses the SXXEYY pattern (S01E01, s10e20, S1.E1, S-1-E-1, S01 E01)
        from a filename for the Fallback step.
        """
        match = re.search(r'[sS][\.\-]?(\d+)\s*[eE][\.\-]?(\d+)', filename)
        if match:
            try:
                season = int(match.group(1))
                episode = int(match.group(2))
                return season, episode
            except ValueError:
                return None
        return None
        
    def _get_media_files_page(self, start: int) -> Optional[ET.Element]:
        """3.1: Calls the SageX API for a specific page of media files."""
        # Use basic auth directly in the URL for compatibility with SageX
        url = f"http://{self.auth[0]}:{self.auth[1]}@{self.host}:{self.port}/sagex/api"
        params = {
            "command": "GetMediaFiles",
            "format": "xml",
            "size": self.page_size,
            "start": start
        }
        
        try:
            logging.debug(f"Requesting API page at start={start}")
            # Note: Requests uses the auth tuple passed, but the URL format is used for logging/debugging
            response = requests.get(
                f"http://{self.host}:{self.port}/sagex/api", 
                params=params, 
                auth=self.auth, 
                timeout=30
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            return root
        except requests.exceptions.RequestException as e:
            logging.error(f"API Request failed at start={start}. Check SAGE_HOST, SAGE_PORT, SAGE_USER/PASS. Error: {e}")
            return None
        except ET.ParseError as e:
            logging.error(f"Failed to parse XML response at start={start}: {e}")
            return None
            
    def _extract_data(self, element: ET.Element) -> Dict[str, str]:
        """3.1: Extracts essential metadata from the XML element."""
        
        def get_text(xpath: str, default: str = '') -> str:
            val = element.findtext(xpath)
            return val.strip() if val else default

        media_file_id = element.get('ID', '') 

        file_path = get_text('./SegmentFiles/File')
        is_movie_str = get_text('./Airing/Show/IsMovie', 'false').lower()

        title = get_text('./Airing/Show/ShowTitle') or get_text('./MediaTitle')

        metadata = {
            'MediaFileID': media_file_id, 
            'IsMovie': is_movie_str == 'true',
            'Title': title,
            'Year': get_text('./Airing/Show/ShowYear'),
            'Description': get_text('./Airing/Show/ShowDescription') or get_text('./MediaFileMetadataProperties/Description'),
            'RuntimeMs': get_text('./FileDuration'),
            'EpisodeName': get_text('./Airing/Show/ShowEpisode'),
            # 3.1: Required metadata fields
            'EpisodeNumber': get_text('./Airing/Show/ShowEpisodeNumber'),
            'SeasonNumber': get_text('./Airing/Show/ShowSeasonNumber'),
            'Rated': get_text('./Airing/Show/ShowRated'),
            'Genre': get_text('./MediaFileMetadataProperties/Genre'),
            'Writers': get_text('./MediaFileMetadataProperties/Writer'),
            'Directors': get_text('./MediaFileMetadataProperties/Director'),
            'FilePath': file_path,
        }
        return metadata

    def _create_nfo_and_symlink(self, data: Dict[str, str], target_dir: Path, nfo_filename_base: str, nfo_content: str, original_mtime: float):
        """3.3: Writes the NFO, creates the symbolic link, and updates the state."""
        
        original_file_path = Path(data['FilePath'])
        if not original_file_path.is_absolute():
            logging.error(f"Skipping: File path is not absolute or accessible: {data['FilePath']}")
            return

        # 1. Ensure target directory exists
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logging.error(f"Failed to create directory {target_dir} (WinError 123 often means illegal character): {e}")
            return

        # NFO and Symlink name construction
        nfo_file = target_dir / f"{nfo_filename_base}.nfo"
        
        file_extension = original_file_path.suffix
        symlink_target_name = f"{nfo_filename_base}{file_extension}"
        symlink_path = target_dir / symlink_target_name

        # 2. Write NFO File
        try:
            nfo_file.write_text(nfo_content, encoding='utf-8')
            logging.debug(f"Created NFO: {nfo_file.name} in {target_dir}")
        except Exception as e:
            logging.error(f"Failed to write NFO {nfo_file}: {e}")
            return
            
        # 3. Create Symbolic Link (3.3: Delete existing symlink first)
        try:
            if symlink_path.is_symlink():
                 symlink_path.unlink()
                 
            # Symlink Creation
            symlink_path.symlink_to(original_file_path)
            logging.info(f"Symlink created: '{symlink_path.name}' -> '{original_file_path.name}'")
            
            # 4. Update State Dictionary (3.2: Store mtime and paths)
            self.processed_state[data['FilePath']] = {
                'mtime': original_mtime,
                'symlink_path': str(symlink_path),
                'nfo_path': str(nfo_file)
            }

        except Exception as e:
            logging.error(f"Failed to create symlink {symlink_path}. Admin privileges may be required on Windows: {e}")
            
    def _create_series_nfo(self, data: Dict[str, str], show_path: Path):
        """4.1: Generates the tvshow.nfo file in the main series folder."""
        
        # FIX: Robust check for Directors/Writers before processing
        directors_xml = ""
        if data.get('Directors'):
            directors_xml = "".join(f"<director>{d.strip()}</director>" for d in data['Directors'].split(';') if d.strip())
            
        writers_xml = ""
        if data.get('Writers'):
            writers_xml = "".join(f"<writer>{w.strip()}</writer>" for w in data['Writers'].split(';') if w.strip())
        
        nfo_content = f"""<tvshow>
    <title>{data['Title']}</title>
    <plot>{data['Description']}</plot>
    <premiered>{data['Year']}-01-01</premiered>
    <year>{data['Year']}</year>
    <genre>{data['Genre']}</genre>
    {directors_xml}
    {writers_xml}
</tvshow>"""
        
        nfo_file = show_path / "tvshow.nfo"
        try:
            show_path.mkdir(parents=True, exist_ok=True)
            nfo_file.write_text(nfo_content, encoding='utf-8')
            logging.info(f"Created Series NFO: {nfo_file.name}")
            self.processed_tv_shows.add(self._clean_directory_name(data['Title'].strip()))
        except Exception as e:
            logging.error(f"Failed to write series NFO {nfo_file}: {e}")

    def _process_tv_show(self, data: Dict[str, str], original_mtime: float):
        """4.0: Processes a TV show episode using the 3-Tier Fallback."""
        if not data['Title']:
            logging.warning(f"Skipping TV episode due to missing title: {data['Title']}")
            return

        # 4.2: 1. Primary (EPG)
        try:
            sage_season_num = int(data['SeasonNumber'] or 0)
            sage_episode_num = int(data['EpisodeNumber'] or 0)
        except ValueError:
            sage_season_num = 0
            sage_episode_num = 0

        use_fallback = (sage_season_num == 0 and sage_episode_num == 0)
        
        season_num = sage_season_num
        episode_num = sage_episode_num

        if use_fallback:
            sxe_result = self._parse_sxxeyy(data['FilePath'])
            if sxe_result:
                # 4.2: 2. Fallback (Filename)
                season_num, episode_num = sxe_result
                logging.info(f"Using **filename fallback** for '{data['Title']}': S{season_num}E{episode_num}")
            else:
                # 4.2: 3. Default (S00)
                season_num = 0
                episode_num = 1
                logging.warning(f"Using **Season 0 fallback** for '{data['Title']}': No valid S/E numbers found in EPG or filename.")
        
        # 4.1: Folder structure
        raw_show_name = data['Title'].strip()
        cleaned_show_name = self._clean_directory_name(raw_show_name)
        
        show_path = self.tv_shows_root / cleaned_show_name
        season_path = show_path / f"Season {season_num:02d}"
        
        if cleaned_show_name not in self.processed_tv_shows:
            self._create_series_nfo(data, show_path)

        # FIX: Safe Runtime conversion
        try:
            runtime_min = round(int(data['RuntimeMs']) / 60000)
        except (ValueError, TypeError):
            runtime_min = '0'
        
        nfo_content = f"""<episodedetails>
    <title>{data['EpisodeName'].strip() or data['Title'].strip()}</title>
    <showtitle>{data['Title']}</showtitle>
    <season>{season_num}</season>
    <episode>{episode_num}</episode>
    <plot>{data['Description']}</plot>
    <year>{data['Year']}</year>
    <genre>{data['Genre']}</genre>
    <runtime>{runtime_min} min</runtime>
</episodedetails>"""

        raw_episode_name = data['EpisodeName'].strip() if data['EpisodeName'] else 'Episode'
        episode_name_clean = self._clean_directory_name(raw_episode_name)
        
        nfo_filename_base = f"{cleaned_show_name} - S{season_num:02d}E{episode_num:02d} - {episode_name_clean}"
        
        self._create_nfo_and_symlink(data, season_path, nfo_filename_base, nfo_content, original_mtime)


    def _process_movie(self, data: Dict[str, str], original_mtime: float):
        """5.0: Processes a Movie, respecting FLAT_MOVIE_STRUCTURE."""
        if not data['Title']:
            logging.warning(f"Skipping Movie due to missing title: {data['Title']}")
            return

        raw_movie_name = data['Title'].strip()
        movie_year = data['Year']
        movie_name_with_year = f"{raw_movie_name} ({movie_year})" if movie_year else raw_movie_name
        
        cleaned_dir_name = self._clean_directory_name(movie_name_with_year)

        # 5. Structure logic
        if self.flat_movie_structure:
            target_dir = self.movies_root # 5. Structure (Flat)
            nfo_filename_base = cleaned_dir_name
        else:
            target_dir = self.movies_root / cleaned_dir_name # 5. Structure (Folder)
            nfo_filename_base = cleaned_dir_name 
            
        # FIX: Safe Runtime conversion
        try:
            runtime_min = round(int(data['RuntimeMs']) / 60000)
        except (ValueError, TypeError):
            runtime_min = '0'
        
        # FIX: Robust check for Directors/Writers before processing
        directors_xml = ""
        if data.get('Directors'):
            directors_xml = "".join(f"<director>{d.strip()}</director>" for d in data['Directors'].split(';') if d.strip())
            
        writers_xml = ""
        if data.get('Writers'):
            writers_xml = "".join(f"<writer>{w.strip()}</writer>" for w in data['Writers'].split(';') if w.strip())

        nfo_content = f"""<movie>
    <title>{raw_movie_name}</title>
    <originaltitle>{raw_movie_name}</originaltitle>
    <year>{movie_year}</year>
    <plot>{data['Description']}</plot>
    <rating>{data['Rated']}</rating>
    <runtime>{runtime_min} min</runtime>
    <genre>{data['Genre']}</genre>
    {directors_xml}
    {writers_xml}
</movie>"""

        self._create_nfo_and_symlink(data, target_dir, nfo_filename_base, nfo_content, original_mtime)

    def _delete_empty_folders(self):
        """6.2: Safely removes empty season and show folders."""
        deleted_folder_count = 0
        
        # Iterate in reverse to ensure sub-folders are checked before parent folders
        for show_dir in sorted(self.tv_shows_root.iterdir(), reverse=True):
            if show_dir.is_dir():
                for season_dir in sorted(show_dir.iterdir(), reverse=True):
                    if season_dir.is_dir() and not list(season_dir.iterdir()):
                        try:
                            season_dir.rmdir()
                            logging.info(f"Deleted empty Season folder: {season_dir.name} in {show_dir.name}")
                            deleted_folder_count += 1
                        except OSError:
                            pass # Skip if rmdir fails (e.g., permissions or hidden files)

                remaining_items = list(show_dir.iterdir())
                
                is_empty = len(remaining_items) == 0
                is_only_nfo = len(remaining_items) == 1 and remaining_items[0].name.lower() == "tvshow.nfo"
                
                if is_empty or is_only_nfo:
                    if is_only_nfo:
                        (show_dir / "tvshow.nfo").unlink(missing_ok=True)
                        
                    try:
                        show_dir.rmdir()
                        logging.info(f"Deleted empty Show folder: {show_dir.name}")
                        deleted_folder_count += 1
                    except OSError:
                        pass # Skip if rmdir fails

        logging.info(f"Empty folder cleanup complete. {deleted_folder_count} folders deleted.")

    def _cleanup_deleted_files(self):
        """6.1: Checks the state file for symlinks that no longer exist and removes orphans."""
        deleted_count = 0
        current_state_keys = list(self.processed_state.keys())
        
        for original_path in current_state_keys:
            state_record = self.processed_state[original_path]
            symlink_path = Path(state_record['symlink_path'])
            nfo_path = Path(state_record['nfo_path'])
            
            # 6.1: If the symlink is gone (meaning the source file or symlink was deleted)
            if not symlink_path.is_symlink(): 
                if nfo_path.exists():
                    nfo_path.unlink(missing_ok=True) # Delete the orphaned NFO
                    logging.info(f"Cleanup: Removed NFO for deleted item: {nfo_path.name}")

                logging.debug(f"Cleanup: Removing state record for: {original_path}")
                del self.processed_state[original_path] # Remove from state
                deleted_count += 1
                    
        logging.info(f"Symlink/NFO cleanup complete. {deleted_count} file records removed from state.")
        
        # 6.2: Call empty folder deletion
        self._delete_empty_folders()


    def process_all_media_files(self):
        """Iterates through all media files from the API and processes them."""
        start = 0
        total_processed = 0
        
        logging.info("Starting media file retrieval and processing...")
        
        while True:
            logging.info(f"Requesting page starting at index: {start}")
            root_element = self._get_media_files_page(start)
            
            if root_element is None:
                break

            media_files = root_element.findall('./MediaFile')
            
            if not media_files:
                logging.info(f"No more media files found. Finished API processing.")
                break

            for file_element in media_files:
                metadata = self._extract_data(file_element)
                original_path = metadata['FilePath']
                
                if not original_path:
                    logging.warning(f"Skipping file with missing path: {metadata['Title']}")
                    continue
                
                original_file_path = Path(original_path)
                
                # --- 3.2: Change Detection Logic ---
                try:
                    current_mtime = original_file_path.stat().st_mtime
                except FileNotFoundError:
                    logging.warning(f"Original file not found on disk: {original_path}. Skipping.")
                    continue
                except OSError as e:
                    logging.error(f"Error accessing original file stats for {original_path}: {e}. Skipping.")
                    continue
                
                if original_path in self.processed_state:
                    stored_mtime = self.processed_state[original_path]['mtime']
                    
                    if current_mtime == stored_mtime:
                        # 3.2: Skip processing (log as debug)
                        logging.debug(f"Skipping unchanged file: {metadata['Title']}")
                        continue
                        
                    else:
                        logging.info(f"**Re-processing UPDATED file:** {metadata['Title']} (mtime changed)")
                        
                else:
                    logging.info(f"**Processing NEW file:** {metadata['Title']}")
                    
                # Process NEW or UPDATED files
                is_movie = metadata['IsMovie']

                try:
                    if is_movie:
                        self._process_movie(metadata, current_mtime)
                    else:
                        self._process_tv_show(metadata, current_mtime)

                    total_processed += 1
                except Exception as e:
                    logging.error(f"Error processing {metadata['Title']}: {e}", exc_info=True)
                    
            if len(media_files) < self.page_size:
                break # Processed last page
                
            start += self.page_size
            
        logging.info(f"Processing complete. Total items created/updated: {total_processed}")
        
        # 6. Final Cleanup and State Save
        self._cleanup_deleted_files()
        self._save_state()


# --- Main Execution Block ---
if __name__ == "__main__":
    
    try:
        config_data = load_config()
        setup_logging(config_data)

        logging.info(f"Configuration: Root Path='{config_data['ROOT_PATH']}', Verbosity Level={config_data['VERBOSITY_LEVEL']}")
    
        if os.name == 'nt':
            logging.warning("⚠️ On Windows, creating symbolic links often requires Admin privileges to avoid a permission error.")
        
        converter = SageXConverter(config_data)
        converter.process_all_media_files()
        
    except Exception as main_e:
        # Catch any critical errors during config load or main execution
        critical_logger = logging.getLogger()
        critical_logger.critical(f"A fatal error occurred during program execution: {main_e}", exc_info=True)
