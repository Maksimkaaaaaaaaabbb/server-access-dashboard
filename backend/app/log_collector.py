import os
import re
import json
import stat
import gzip
import glob
import geoip2.database
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import LogEntry
from .database import SessionLocal
from dotenv import load_dotenv
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    logger.info(f"Log Collector: Load .env from {dotenv_path}")
else:
    logger.info(
        f"Log Collector: No .env Datei under {dotenv_path} found, use envirionment variables."
    )

GEOIP_DB_PATH = os.getenv("GEOIP_DATABASE_PATH")
LOG_DIR = os.getenv("LOG_DIRECTORY")
geoip_reader = None
if GEOIP_DB_PATH and os.path.exists(GEOIP_DB_PATH):
    try:
        geoip_reader = geoip2.database.Reader(GEOIP_DB_PATH)
        logger.info(f"GeoIP DB loaded: {GEOIP_DB_PATH}")
    except Exception as e:
        logger.error(f"Error load GeoIP DB ({GEOIP_DB_PATH}): {e}")
else:
    logger.warning("GEOIP_DB_PATH not found. GeoIP deactivated.")

log_pattern_str = r"""
    ^\[(?P<time_local>\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}\s[+-]\d{4})\]\s+
    -\s+
    (?:-\s+)?
    (?P<status>\d{3})
    .*?\s+
    (?P<request_method>GET|POST|PUT|DELETE|HEAD|OPTIONS|PATCH)\s+
    (?P<scheme>https?)\s+
    (?P<host>[^\s]+)\s+
    (?P<request_uri>"[^"]*"|[^"\s]+)\s+
    .*?
    \[Client\s(?P<remote_addr>[^\]]+)\]
    .*$
"""
log_pattern = re.compile(log_pattern_str, re.VERBOSE)
logger.info("Regex Log Parser initialized.")

# --- Path to statefile ---
STATE_FILE_PATH = os.path.join(os.path.dirname(__file__), "log_state.json")


# --- function for loading and saving state ---
def load_log_state() -> dict:
    default_log_entry = {"offset": 0, "inode": None}
    default_gz_entry = {"processed": False}
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            valid_state = {}
            for filename, data in state.items():
                if isinstance(data, dict):
                    if filename.endswith(".gz"):
                        valid_state[filename] = {
                            "processed": data.get("processed", False)
                        }
                    elif filename.endswith(".log"):
                        try:
                            offset = int(data.get("offset", 0))
                        except (ValueError, TypeError):
                            offset = 0
                        inode = data.get("inode")
                        if inode is not None:
                            try:
                                inode = int(inode)
                            except (ValueError, TypeError):
                                inode = None
                        valid_state[filename] = {"offset": offset, "inode": inode}
                    else:
                        logger.warning(
                            f"Unknown Filetype in Statefile: {filename}. Ignoring."
                        )
                else:
                    logger.warning(
                        f"Invalid entry for {filename} found in state file. Resetting."
                    )
                    if filename.endswith(".gz"):
                        valid_state[filename] = default_gz_entry.copy()
                    elif filename.endswith(".log"):
                        valid_state[filename] = default_log_entry.copy()
            return valid_state
        except (json.JSONDecodeError, ValueError, TypeError, IOError) as e:
            logger.error(
                f"Error loading/parsing state file {STATE_FILE_PATH}: {e}. Starting from beginning."
            )
            try:
                os.rename(STATE_FILE_PATH, STATE_FILE_PATH + ".bak")
            except OSError:
                pass
            return {}
    return {}


def save_log_state(state: dict):
    try:
        temp_path = STATE_FILE_PATH + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
        os.replace(temp_path, STATE_FILE_PATH)
    except (IOError, OSError) as e:
        logger.error(f"Error saving state file {STATE_FILE_PATH}: {e}")


# --- get_country_from_ip function ---
def get_country_from_ip(ip_address: str) -> str | None:
    if not geoip_reader or not ip_address:
        return None
    try:
        response = geoip_reader.country(ip_address)
        return response.country.iso_code
    except geoip2.errors.AddressNotFoundError:
        return None
    except Exception as e:
        logger.error(f"Error in GeoIP for {ip_address}: {e}")
        return None


# --- parse_log_line function ---
def parse_log_line(line: str, filename: str = "") -> dict | None:
    match = log_pattern.match(line)
    if not match:
        return None
    try:
        entry = match.groupdict()
        timestamp_str = entry.get("time_local")
        timestamp_dt_utc = None
        if timestamp_str:
            try:
                timestamp_dt_aware = datetime.strptime(
                    timestamp_str, "%d/%b/%Y:%H:%M:%S %z"
                )
                timestamp_dt_utc = timestamp_dt_aware.astimezone(timezone.utc)
            except ValueError as e_ts:
                logger.error(f"timestamp error '{timestamp_str}': {e_ts}")
                return None
        else:
            return None
        status_str = entry.get("status")
        status_code = None
        if status_str:
            try:
                status_code = int(status_str)
            except ValueError:
                pass
        ip_address = entry.get("remote_addr")
        domain = entry.get("host")
        request_path_raw = entry.get("request_uri")
        request_path = request_path_raw.strip('"') if request_path_raw else None
        if not ip_address or not domain or request_path is None:
            return None
        return {
            "ip": ip_address,
            "timestamp": timestamp_dt_utc,
            "status_code": status_code,
            "request_path": request_path,
            "domain": domain,
            "raw": line,
        }
    except Exception as e:
        logger.error(f"Error in parse_log_line for line '{line}': {e}", exc_info=True)
        return None


# --- process_log_files function ---
def process_log_files():
    if not LOG_DIR or not os.path.isdir(LOG_DIR):
        logger.error(f"Log-Directory not found: {LOG_DIR}")
        return

    db: Session = SessionLocal()
    log_state = load_log_state()
    new_log_entries_to_add = []
    total_new_entries_count = 0

    try:
        active_log_pattern = os.path.join(LOG_DIR, "proxy-host-*_access.log")
        rotated_log_pattern = os.path.join(LOG_DIR, "proxy-host-*_access.log.*.gz")
        active_files = glob.glob(active_log_pattern)
        rotated_files = glob.glob(rotated_log_pattern)
        rotated_files.sort(
            key=lambda x: int(x.split(".")[-2]) if x.split(".")[-2].isdigit() else 0,
            reverse=True,
        )
        all_files_to_process = rotated_files + active_files
        if not all_files_to_process:
            logger.info(f"No Log-Files found in {LOG_DIR}")
            db.close()
            return
        logger.info(
            f"Files to process ({len(all_files_to_process)}): {all_files_to_process}"
        )

        latest_db_timestamp_result = db.query(func.max(LogEntry.timestamp)).scalar()
        latest_db_timestamp = datetime.min.replace(tzinfo=timezone.utc)
        if latest_db_timestamp_result:
            if latest_db_timestamp_result.tzinfo is None:
                latest_db_timestamp = latest_db_timestamp_result.replace(
                    tzinfo=timezone.utc
                )
            else:
                latest_db_timestamp = latest_db_timestamp_result.astimezone(
                    timezone.utc
                )
        else:
            logger.info(
                f"No Entry found in DB, use minimal UTC timestamp: {latest_db_timestamp}"
            )

        for filepath in all_files_to_process:
            filename = os.path.basename(filepath)
            added_in_this_file = 0
            is_gzipped = filename.endswith(".gz")
            logger.info(f"Starting Collection: {filename} (Gzipped: {is_gzipped})")

            try:
                if is_gzipped:
                    file_state = log_state.get(filename, {"processed": False})
                    if file_state.get("processed"):
                        logger.info(f"File {filename} already collected. Skipping.")
                        continue
                    offset_to_read_from = 0
                    current_inode = None
                else:
                    file_stat = os.stat(filepath)
                    current_inode = file_stat[stat.ST_INO]
                    current_file_size = file_stat[stat.ST_SIZE]
                    file_state = log_state.get(filename, {"offset": 0, "inode": None})
                    last_offset = file_state.get("offset", 0)
                    last_inode = file_state.get("inode")
                    offset_to_read_from = 0
                    if last_inode is None:
                        logger.info(f"File {filename} seen for the first time.")
                    elif current_inode != last_inode:
                        logger.warning(
                            f"File {filename} has new inode (rotated?). Reading from start."
                        )
                    elif current_file_size < last_offset:
                        logger.warning(
                            f"File {filename} is smaller (truncated?). Reading from start."
                        )
                    elif current_file_size == last_offset:
                        logger.info(
                            f"File {filename} no new data (offset {last_offset}, inode matches)."
                        )
                        log_state[filename] = {
                            "offset": current_file_size,
                            "inode": current_inode,
                        }
                        continue
                    else:
                        logger.info(
                            f"File {filename}: Reading from offset {last_offset} (inode matches)."
                        )
                        offset_to_read_from = last_offset

                new_lines_in_file = []
                current_offset = offset_to_read_from
                open_func = gzip.open if is_gzipped else open
                mode = "rt" if is_gzipped else "r"

                with open_func(filepath, mode, encoding="utf-8", errors="ignore") as f:
                    if offset_to_read_from > 0 and not is_gzipped:
                        f.seek(offset_to_read_from)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        parsed_data = parse_log_line(line, filename)
                        if not parsed_data:
                            continue
                        if parsed_data["timestamp"] <= latest_db_timestamp:
                            continue
                        new_lines_in_file.append(parsed_data)
                    if not is_gzipped:
                        current_offset = f.tell()

                if new_lines_in_file:
                    potential_new_keys = set(
                        (d["timestamp"], d["ip"], d["raw"]) for d in new_lines_in_file
                    )
                    min_ts = min(k[0] for k in potential_new_keys)
                    if min_ts.tzinfo is None:
                        min_ts = min_ts.replace(tzinfo=timezone.utc)

                    existing_keys_query = db.query(
                        LogEntry.timestamp, LogEntry.ip_address, LogEntry.raw_log
                    ).filter(LogEntry.timestamp >= min_ts)
                    existing_keys_in_db = set(existing_keys_query.all())

                    for parsed_data in new_lines_in_file:
                        entry_key = (
                            parsed_data["timestamp"],
                            parsed_data["ip"],
                            parsed_data["raw"],
                        )
                        if entry_key not in existing_keys_in_db:
                            country = get_country_from_ip(parsed_data["ip"])

                            if country is None:
                                country = "Unknown"

                            db_entry = LogEntry(
                                ip_address=parsed_data["ip"],
                                timestamp=parsed_data["timestamp"],
                                status_code=parsed_data.get("status_code"),
                                country=country,
                                request_path=parsed_data["request_path"],
                                domain=parsed_data.get("domain"),
                                raw_log=parsed_data["raw"],
                            )
                            new_log_entries_to_add.append(db_entry)
                            added_in_this_file += 1
                            existing_keys_in_db.add(entry_key)

                if is_gzipped:
                    log_state[filename] = {"processed": True}
                    logger.info(
                        f"File {filename}: {added_in_this_file} new entries added to the DB. Marked as processed."
                    )
                else:
                    log_state[filename] = {
                        "offset": current_offset,
                        "inode": current_inode,
                    }
                    logger.info(
                        f"File {filename}: {added_in_this_file} new entries added to the DB. New offset: {current_offset}, inode: {current_inode}"
                    )

            except FileNotFoundError:
                logger.error(f"File not found during processing: {filepath}")
            except gzip.BadGzipFile:
                logger.error(f"Error: Invalid Gzip file: {filepath}")
            except Exception as e:
                logger.error(f"Error processing file {filepath}: {e}", exc_info=True)

        if new_log_entries_to_add:
            db.add_all(new_log_entries_to_add)
            db.commit()
            total_new_entries_count = len(new_log_entries_to_add)
            logger.info(
                f"Successfully wrote {total_new_entries_count} new log entries to the DB in total."
            )
        else:
            logger.info("No new unique log entries found.")

        save_log_state(log_state)

    except Exception as e:
        logger.error(f"Critical error in process_log_files: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()
        logger.info("Log processing run finished.")


# --- close_geoip_reader function ---
def close_geoip_reader():
    if geoip_reader:
        geoip_reader.close()
        logger.info("GeoIP Reader closed.")
