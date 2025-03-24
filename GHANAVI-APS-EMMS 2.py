import sqlite3
import threading
import sv_ttk
import os
import xlsxwriter
import random
import string
import tkinter as tk
from tkinter import messagebox, ttk, simpledialog, filedialog
from tkinter import Tk, Label, Button, Toplevel, StringVar
from datetime import datetime, timedelta
from datetime import timezone
import re
from PIL import Image, ImageTk
import bcrypt
import subprocess
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from dateutil import parser
import pickle
import io
import sys
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
import pickle
import time
import shutil
import requests
import zipfile
import base64
import ttkthemes



# Define the resource_path function at the top of the file

def resource_path(relative_path):
    """Get the absolute path to the resource, works for dev and for PyInstaller."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def writable_path(relative_path):
    """Get the absolute path to a writable resource."""
    # Use the user's home directory for writable files
    base_path = os.path.expanduser("~")
    return os.path.join(base_path, ".Ghanavi_membership", relative_path)


# Ensure the writable directory exists
os.makedirs(writable_path(""), exist_ok=True)

# Constants
SCOPES = ['https://www.googleapis.com/auth/drive.file']
DATABASE_PATH = resource_path("GhanaVi-members.db")  # Now this will work
GOOGLE_DRIVE_FILE_ID = '1WXK58wUGWM41RQdW9tfFc9_do1KHJd8c'  # Replace with your actual file ID
TOKEN_PATH = resource_path("token.pickle")
CREDENTIALS_PATH = resource_path("credentials.json")
# Constants
CURRENT_VERSION = "1.0.1"
UPDATE_INFO_URL = "https://raw.githubusercontent.com/GHVI-EMMS/GHANAVI-APS-EMMS/refs/heads/main/version_info.txt"  # Replace with your actual URL
UPDATE_FILE_URL = "https://raw.githubusercontent.com/GHVI-EMMS/GHANAVI-APS-EMMS/refs/heads/main/GHANAVI-APS-EMMS.py"  # Replace with your actual URL

# Global sync lock
sync_lock = threading.Lock()

# Global variable to track the last sync time
last_sync_time = 0
SYNC_INTERVAL = 300  # 5 minutes (in seconds)

# Global variable for the last sync label
last_sync_label = None



def get_google_drive_service():
    """Get or create Google Drive service."""
    creds = None

    try:
        if os.path.exists(TOKEN_PATH):
            with open(TOKEN_PATH, 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(CREDENTIALS_PATH):
                    raise FileNotFoundError("credentials.json not found. Please download it from Google Cloud Console.")

                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_PATH, SCOPES)
                creds = flow.run_local_server(port=0)

            with open(TOKEN_PATH, 'wb') as token:
                pickle.dump(creds, token)

        return build('drive', 'v3', credentials=creds)

    except Exception as e:
        print(f"Error in authentication: {str(e)}")
        raise


def download_db_from_drive(service):
    """Download database from Google Drive."""
    request = service.files().get_media(fileId=GOOGLE_DRIVE_FILE_ID)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False

    while done is False:
        status, done = downloader.next_chunk()
        print(f"Download {int(status.progress() * 100)}%")

    fh.seek(0)
    with open(DATABASE_PATH, 'wb') as f:
        f.write(fh.read())



def upload_db_to_drive(service):
    """Upload database to Google Drive."""
    file_metadata = {
        'name': 'GhanaVi-members.db',
        'mimeType': 'application/x-sqlite3'
    }

    media = MediaFileUpload(DATABASE_PATH,
                            mimetype='application/x-sqlite3',
                            resumable=True)

    if not GOOGLE_DRIVE_FILE_ID:
        file = service.files().create(body=file_metadata,
                                      media_body=media,
                                      fields='id').execute()
        print(f'File ID: {file.get("id")}')
        return file.get('id')
    else:
        file = service.files().update(fileId=GOOGLE_DRIVE_FILE_ID,
                                      media_body=media).execute()
        return GOOGLE_DRIVE_FILE_ID

def get_local_mod_time():
    """Get local file modification time in UTC"""
    local_ts = os.path.getmtime(DATABASE_PATH)
    return datetime.fromtimestamp(local_ts, tz=timezone.utc).timestamp()

def get_google_drive_mod_time(service):
    """Get the modification time of the Google Drive file with proper timezone handling."""
    try:
        file_metadata = service.files().get(fileId=GOOGLE_DRIVE_FILE_ID, fields='modifiedTime').execute()
        remote_mod_time_str = file_metadata.get('modifiedTime')

        # Parse with timezone information
        remote_mod_time = parser.parse(remote_mod_time_str)

        # Convert to UTC timestamp
        remote_mod_time_utc = remote_mod_time.astimezone(timezone.utc)
        return remote_mod_time_utc.timestamp()
    except Exception as e:
        print(f"Error getting Google Drive file modification time: {e}")
        return 0  # Return a default value if an error occurs

def sync_database_with_conflict_resolution():
    """Synchronize database with Google Drive, resolving conflicts only when necessary."""
    try:
        print("Starting sync with Google Drive...")
        service = get_google_drive_service()

        # Check if members table exists locally
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='members';")
            if not cursor.fetchone():
                print("Initializing database...")
                initialize_db()  # Re-initialize if missing

        # Check if the members table exists locally
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='members';")
            if not cursor.fetchone():
                print("Members table does not exist locally. Initializing database...")
                initialize_db()

        local_mod_time = os.path.getmtime(DATABASE_PATH)
        remote_mod_time = get_google_drive_mod_time(service)

        if remote_mod_time is None or remote_mod_time == 0:
            print("Failed to get remote modification time. Aborting sync.")
            return

        if remote_mod_time > local_mod_time:
            print("Remote version is newer. Downloading...")
            download_db_from_drive(service)
            resolve_conflicts(DATABASE_PATH, DATABASE_PATH + ".remote")
        elif local_mod_time > remote_mod_time:
            print("Local version is newer. Uploading...")
            upload_db_to_drive(service)
        else:
            print("Both versions are up to date.")

        print("Sync complete.")

    except Exception as e:
        print(f"Sync error: {e}")
        messagebox.showerror("Sync Error", f"Database sync failed: {e}")


def is_valid_date_tz(date_string):
    """Validate date with timezone awareness"""
    try:
        parsed = parser.parse(date_string)
        return True
    except parser.ParserError:
        return False

def sync_database():
    """Synchronize database with Google Drive."""
    try:
        print("Attempting to sync database with Google Drive...")
        service = get_google_drive_service()

        # Get local file modification time
        local_mod_time = os.path.getmtime(DATABASE_PATH)

        # Get remote file metadata
        file_metadata = service.files().get(fileId=GOOGLE_DRIVE_FILE_ID, fields='modifiedTime').execute()
        remote_mod_time = file_metadata.get('modifiedTime')
        remote_mod_time = datetime.strptime(remote_mod_time[:-5], '%Y-%m-%dT%H:%M:%S')  # Convert to datetime object
        remote_mod_time = remote_mod_time.timestamp()  # Convert to timestamp

        # Compare modification times
        if remote_mod_time > local_mod_time:
            print("Remote file is more recent. Downloading...")
            download_db_from_drive(service)
        elif local_mod_time > remote_mod_time:
            print("Local file is more recent. Uploading...")
            upload_db_to_drive(service)
        else:
            print("Both files are up to date.")

        print("Database synchronized successfully!")

    except Exception as e:
        print(f"Error synchronizing database: {e}")
        messagebox.showerror("Sync Error", f"Failed to sync database: {e}")


def backup_database(db_path):
    """Create a timestamped backup of the database before resolving conflicts."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{db_path}_{timestamp}.backup"
    shutil.copyfile(db_path, backup_filename)
    print(f"Backup created: {backup_filename}")


def log_change(action, record_id, data):
    """Log changes for audit tracking."""
    with open("change_log.txt", "a") as log:
        log.write(f"{datetime.now()} | {action} | ID: {record_id} | Data: {data}\n")


def resolve_conflicts_advanced(local_db, remote_db, last_synced_db):
    """Advanced conflict resolution with three-way merge and conflict logging"""
    try:
        backup_database(local_db)

        with sqlite3.connect(local_db) as conn_local, \
                sqlite3.connect(remote_db) as conn_remote, \
                sqlite3.connect(last_synced_db) as conn_synced:

            cur_local = conn_local.cursor()
            cur_remote = conn_remote.cursor()
            cur_synced = conn_synced.cursor()

            # Get all versions
            cur_local.execute("SELECT * FROM members")
            local_data = {row[0]: row for row in cur_local.fetchall()}

            cur_remote.execute("SELECT * FROM members")
            remote_data = {row[0]: row for row in cur_remote.fetchall()}

            cur_synced.execute("SELECT * FROM members")
            synced_data = {row[0]: row for row in cur_synced.fetchall()}

            merged_data = []
            conflicts = []
            auto_resolved = []

            for record_id in set(local_data.keys()).union(remote_data.keys()):
                local = local_data.get(record_id)
                remote = remote_data.get(record_id)
                base = synced_data.get(record_id)

                # No conflict cases
                if local == remote:
                    merged_data.append(local or remote)
                    continue

                if not remote:
                    merged_data.append(local)
                    log_conflict(conn_local, 'members', record_id,
                                 local, None, 'keep_local', 'auto')
                    continue

                if not local:
                    merged_data.append(remote)
                    log_conflict(conn_local, 'members', record_id,
                                 None, remote, 'keep_remote', 'auto')
                    continue

                # Three-way merge attempt
                merged = []
                resolvable = True
                for i in range(len(local)):
                    if local[i] == remote[i]:
                        merged.append(local[i])
                    elif base and (local[i] == base[i] or remote[i] == base[i]):
                        merged.append(remote[i] if local[i] == base[i] else local[i])
                    else:
                        resolvable = False
                        break

                if resolvable:
                    merged_data.append(tuple(merged))
                    auto_resolved.append(record_id)
                    log_conflict(conn_local, 'members', record_id,
                                 local, remote, 'merged', 'auto')
                else:
                    conflicts.append({
                        'id': record_id,
                        'local': local,
                        'remote': remote,
                        'base': base
                    })

            # Apply automatic resolutions
            if auto_resolved:
                cur_local.executemany("""
                    UPDATE members 
                    SET FirstName=?, LastName=?, Sex=?, Email=?, Phone=?, 
                        Address=?, Postcode=?, Town=?, Province=?, CodiceFiscale=?, 
                        DateOfBirth=?, PlaceOfBirth=?, JoinDate=?, ExpirationDate=?, 
                        DismissalDate=?, MembershipType=?, Status=?, PrefersEmail=?, 
                        PrefersSMS=?, PreferredLanguage=?, EmergencyContactName=?, 
                        EmergencyContactPhone=?
                    WHERE MemberID=?
                """, [row[1:] + (row[0],) for row in merged_data])
                conn_local.commit()

            # Handle remaining conflicts
            if conflicts:
                handle_conflicts_gui(conflicts, local_db)

        print(f"Resolved {len(auto_resolved)} conflicts automatically")
        print(f"Manual resolution needed for {len(conflicts)} conflicts")

    except Exception as e:
        print(f"Conflict resolution failed: {e}")
        messagebox.showerror("Conflict Error", f"Failed to resolve conflicts: {e}")


def log_conflict(connection, table_name, record_id, local, remote, action, resolution_type):
    """Log conflict resolution to database"""
    try:
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO conflict_logs (
                table_name, record_id, local_version, remote_version,
                resolution_action, resolution_type, resolved_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            table_name,
            record_id,
            str(local) if local else None,
            str(remote) if remote else None,
            action,
            resolution_type,
            get_current_user() or 'system'
        ))
        connection.commit()
    except Exception as e:
        print(f"Failed to log conflict: {e}")


def handle_conflicts_gui(conflicts, local_db):
    """GUI for manual conflict resolution with proper logging"""
    conflict_window = tk.Toplevel()
    conflict_window.title("Resolve Conflicts")
    conflict_window.geometry("800x600")

    current_conflict = 0
    resolved_data = []

    def show_conflict():
        nonlocal current_conflict
        if current_conflict >= len(conflicts):
            conflict_window.destroy()
            apply_resolutions()
            return

        conflict = conflicts[current_conflict]
        for widget in conflict_window.winfo_children():
            widget.destroy()

        # Conflict details frame
        details_frame = ttk.Frame(conflict_window)
        details_frame.pack(pady=10, fill='x')

        ttk.Label(details_frame, text=f"Conflict for Member ID: {conflict['id']}",
                  font=('Arial', 14, 'bold')).pack()

        # Local version
        local_frame = ttk.LabelFrame(details_frame, text="Local Version")
        local_frame.pack(side='left', padx=10, fill='both', expand=True)
        populate_conflict_view(local_frame, conflict['local'])

        # Remote version
        remote_frame = ttk.LabelFrame(details_frame, text="Remote Version")
        remote_frame.pack(side='right', padx=10, fill='both', expand=True)
        populate_conflict_view(remote_frame, conflict['remote'])

        # Resolution buttons
        button_frame = ttk.Frame(conflict_window)
        button_frame.pack(pady=20)

        ttk.Button(button_frame, text="Keep Local",
                   command=lambda: resolve('keep_local')).pack(side='left', padx=10)
        ttk.Button(button_frame, text="Keep Remote",
                   command=lambda: resolve('keep_remote')).pack(side='left', padx=10)
        ttk.Button(button_frame, text="Merge Manually",
                   command=lambda: manual_merge(conflict)).pack(side='left', padx=10)

    def populate_conflict_view(parent, data):
        """Populate conflict view with data"""
        if not data:
            ttk.Label(parent, text="No version available").pack()
            return

        fields = [
            ("First Name", data[1]),
            ("Last Name", data[2]),
            ("Email", data[4]),
            ("Phone", data[5]),
            ("Membership Type", data[16]),
            ("Expiration Date", data[14])
        ]

        for label, value in fields:
            row = ttk.Frame(parent)
            row.pack(fill='x', padx=5, pady=2)
            ttk.Label(row, text=label, width=15, anchor='w').pack(side='left')
            ttk.Label(row, text=value).pack(side='left')

    def resolve(action):
        """Handle resolution choice"""
        nonlocal current_conflict
        conflict = conflicts[current_conflict]

        with sqlite3.connect(local_db) as conn:
            # Update local database
            cursor = conn.cursor()
            if action == 'keep_local':
                cursor.execute("DELETE FROM members WHERE MemberID=?", (conflict['id'],))
                cursor.execute("INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               conflict['local'])
            elif action == 'keep_remote':
                cursor.execute("DELETE FROM members WHERE MemberID=?", (conflict['id'],))
                cursor.execute("INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               conflict['remote'])

            # Log resolution
            log_conflict(conn, 'members', conflict['id'],
                         conflict['local'], conflict['remote'],
                         action, 'manual')

            conn.commit()

        current_conflict += 1
        show_conflict()

    def manual_merge(conflict):
        """Open detailed merge window"""
        merge_window = tk.Toplevel(conflict_window)
        merge_window.title("Manual Merge")
        merge_window.geometry("1000x800")

        # Create comparison grid
        columns = ["Field", "Local Value", "Remote Value", "Choose"]
        tree = ttk.Treeview(merge_window, columns=columns, show='headings')

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=200 if col == "Field" else 250)

        # Populate fields
        field_names = [
            "MemberID", "FirstName", "LastName", "Sex", "Email", "Phone",
            "Address", "Postcode", "Town", "Province", "CodiceFiscale",
            "DateOfBirth", "PlaceOfBirth", "JoinDate", "ExpirationDate",
            "DismissalDate", "MembershipType", "Status", "PrefersEmail",
            "PrefersSMS", "PreferredLanguage", "EmergencyContactName",
            "EmergencyContactPhone"
        ]

        for i in range(23):  # Number of fields in members table
            local_val = str(conflict['local'][i]) if conflict['local'] else "N/A"
            remote_val = str(conflict['remote'][i]) if conflict['remote'] else "N/A"

            tree.insert("", 'end', values=(
                field_names[i],
                local_val,
                remote_val,
                "➔" if local_val != remote_val else "✓"
            ))

        tree.pack(fill='both', expand=True)

        # Merge controls
        control_frame = ttk.Frame(merge_window)
        control_frame.pack(pady=10)

        ttk.Button(control_frame, text="Use Local Version",
                   command=lambda: finalize_merge(conflict['local'])).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Use Remote Version",
                   command=lambda: finalize_merge(conflict['remote'])).pack(side='left', padx=5)

        def finalize_merge(version):
            with sqlite3.connect(local_db) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM members WHERE MemberID=?", (conflict['id'],))
                cursor.execute("INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", version)
                log_conflict(conn, 'members', conflict['id'],
                             conflict['local'], conflict['remote'],
                             'manual_merge', 'manual')
                conn.commit()
            merge_window.destroy()
            nonlocal current_conflict
            current_conflict += 1
            show_conflict()

    def apply_resolutions():
        """Final cleanup after all conflicts are resolved"""
        refresh_member_table()
        messagebox.showinfo("Conflicts Resolved",
                            f"Successfully resolved {len(conflicts)} conflicts")

    show_conflict()
    conflict_window.mainloop()




def is_internet_connected():
    """Check if the application is connected to the internet."""
    try:
        import socket
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except:
        return False




def save_last_sync_time(sync_timestamp: float):
    """Save timestamp as UTC datetime string"""
    sync_dt = datetime.fromtimestamp(sync_timestamp, tz=timezone.utc)
    sync_str = sync_dt.strftime("%d-%m-%Y %H:%M:%S")
    with open(writable_path("last_sync_time.txt"), 'w') as f:
        f.write(sync_str)


def read_last_sync_time() -> float:
    """Return UTC timestamp instead of datetime object"""
    try:
        with open(writable_path("last_sync_time.txt"), 'r') as f:
            sync_str = f.read().strip()
            dt = datetime.strptime(sync_str, "%d-%m-%Y %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc).timestamp()
    except (FileNotFoundError, ValueError):
        return 0.0


def sync_db_if_needed_with_conflict_resolution(last_sync_label):
    """Sync database if connected to internet, with proper lock handling"""
    global last_sync_time

    current_time = time.time()
    if current_time - last_sync_time < SYNC_INTERVAL:
        return

    # Attempt to acquire lock non-blocking
    if not sync_lock.acquire(blocking=False):
        print("Sync already in progress. Skipping this attempt.")
        return

    try:
        print("Attempting to sync database with Google Drive...")
        if is_internet_connected():
            service = get_google_drive_service()

            # Get timestamps
            local_mod_time = os.path.getmtime(DATABASE_PATH)
            remote_mod_time = get_google_drive_mod_time(service)

            # Compare timestamps
            if remote_mod_time > local_mod_time:
                print("Remote file is more recent. Downloading...")
                download_db_from_drive(service)
                resolve_conflicts_advanced(DATABASE_PATH, DATABASE_PATH + ".remote", DATABASE_PATH)
            elif local_mod_time > remote_mod_time:
                print("Local file is more recent. Uploading...")
                upload_db_to_drive(service)

            # Update sync time
            last_sync_time = current_time
            save_last_sync_time(last_sync_time)
            update_last_sync_label(last_sync_label, last_sync_time)
            refresh_member_table()

    except Exception as e:
        print(f"Sync failed: {str(e)}")
        messagebox.showerror("Sync Error", f"Failed to sync: {str(e)}")
    finally:
        sync_lock.release()
        print("Sync lock released")



def sync_and_exit(app_root, last_sync_label):
    """Sync the database with Google Drive and then exit the application."""
    try:
        print("Attempting to sync database with Google Drive before exiting...")
        # Force sync by setting last_sync_time to 0
        global last_sync_time
        last_sync_time = 0
        sync_db_if_needed_with_conflict_resolution(last_sync_label)
        print("Database synchronized successfully!")
    except Exception as e:
        print(f"Error synchronizing database: {e}")
        messagebox.showerror("Sync Error", f"Failed to sync database: {e}")
    finally:
        app_root.quit()




def update_last_sync_label(last_sync_label, last_sync_time):
    """Update the last sync label with the current sync time."""
    sync_time_str = "Never" if last_sync_time == 0 else datetime.fromtimestamp(last_sync_time).strftime("%d-%m-%Y %H:%M:%S")
    last_sync_label.config(text=f"Last Sync: {sync_time_str}")


def setup_connectivity_light(root):
    """Setup the connectivity light and buttons."""
    # Place the connectivity light in the root window (top-right corner)
    connectivity_light_frame = ttk.Frame(root)
    connectivity_light_frame.grid(row=0, column=2, sticky="ne", padx=10, pady=10)

    connectivity_light = tk.Canvas(connectivity_light_frame, width=20, height=20, bg="white", highlightthickness=0)
    connectivity_light.pack()

    # Initial state: red (no internet)
    connectivity_light.create_oval(2, 2, 18, 18, fill="red", outline="black")

    # Add a label to explain the connectivity light
    connectivity_label = ttk.Label(connectivity_light_frame, text="No Internet")
    connectivity_label.pack(pady=5)

    # Read the last sync time from a file
    global last_sync_time
    last_sync_time = read_last_sync_time() or 0  # Default to 0 if no sync time is found

    # Add a label to display the last sync time
    global last_sync_label
    last_sync_time_str = "Never" if last_sync_time == 0 else datetime.fromtimestamp(last_sync_time).strftime("%d-%m-%Y %H:%M:%S")
    last_sync_label = ttk.Label(connectivity_light_frame, text=f"Last Sync: {last_sync_time_str}")
    last_sync_label.pack(pady=5)

    # Add the "Sync Now" button
    sync_now_button = ttk.Button(connectivity_light_frame, text="Sync Now", command=lambda: sync_db_if_needed_with_conflict_resolution(last_sync_label))
    sync_now_button.pack(pady=5)

    # Add the "Refresh Table" button
    refresh_table_button = ttk.Button(connectivity_light_frame, text="Refresh Table", command=refresh_member_table)
    refresh_table_button.pack(pady=5)

    # Add a dropdown menu for theme selection
    theme_var = tk.StringVar(value="arc")  # Default theme
    theme_combobox = ttk.Combobox(connectivity_light_frame, textvariable=theme_var, values=get_available_themes(), state="readonly", width=15)
    theme_combobox.pack(pady=5)

    # Function to change the theme
    def change_theme():
        selected_theme = theme_var.get()
        style = ttkthemes.ThemedStyle(root)
        style.set_theme(selected_theme)
        print(f"Theme changed to {selected_theme}")

    # Add the "Change Theme" button
    change_theme_button = ttk.Button(connectivity_light_frame, text="Change Theme", command=change_theme)
    change_theme_button.pack(pady=5)

    # Add the "Exit" button with a command to sync before exiting
    exit_button = ttk.Button(connectivity_light_frame, text="Exit",
                             command=lambda: sync_and_exit(root, last_sync_label))  # Pass last_sync_label here
    exit_button.pack(pady=5)

    # Start periodic connectivity check
    check_connectivity(connectivity_light, connectivity_label, root, last_sync_label)  # Pass last_sync_label here


def check_connectivity(light_canvas, label, root, last_sync_label):
    """Check internet connectivity and update the light and label."""
    if is_internet_connected():
        light_canvas.create_oval(2, 2, 18, 18, fill="green", outline="black")
        label.config(text="Connected")
        print("Internet connection detected.")
        sync_db_if_needed_with_conflict_resolution(last_sync_label)  # Sync and update label
    else:
        light_canvas.create_oval(2, 2, 18, 18, fill="red", outline="black")
        label.config(text="No Internet")
        print("No internet connection detected.")

    # Check again after 10 seconds
    root.after(10000, check_connectivity, light_canvas, label, root, last_sync_label)


def sync_button_click():
    sync_db_if_needed_with_conflict_resolution(last_sync_label)  # Pass last_sync_label here


def start_real_time_sync(interval_minutes=1):
    """Start a background thread to periodically sync the database."""
    def sync_task():
        while True:
            try:
                sync_db_if_needed_with_conflict_resolution(last_sync_label)  # Pass last_sync_label here
            except Exception as e:
                print(f"Error during sync: {e}")
            time.sleep(interval_minutes * 60)  # Sleep for the specified interval

    # Start the sync thread
    sync_thread = threading.Thread(target=sync_task, daemon=True)
    sync_thread.start()


def start_real_time_listener(root, refresh_callback):
    """
    Start a background thread to monitor the database for changes.
    :param root: The Tkinter root window.
    :param refresh_callback: The function to call when changes are detected.
    """
    def monitor_database():
        global last_modified_time
        while True:
            try:
                # Get the Google Drive service
                service = get_google_drive_service()

                # Fetch the remote file's modification time
                remote_mod_time = get_remote_file_mod_time(service)
                if remote_mod_time is None:
                    print("Failed to fetch remote file modification time. Skipping sync.")
                    time.sleep(15)  # Sleep for a short interval (e.g., 15 seconds)
                    continue

                # Check the last modified time of the local database file
                local_mod_time = os.path.getmtime(DATABASE_PATH)

                if last_modified_time is None:
                    last_modified_time = local_mod_time
                elif remote_mod_time > last_modified_time:
                    print("Remote file is more recent. Syncing...")
                    if sync_lock.acquire(blocking=False):  # Try to acquire the lock
                        try:
                            sync_database()
                            last_modified_time = remote_mod_time  # Update the last modified time
                            refresh_callback()  # Refresh the member table
                        except Exception as e:
                            print(f"Error during sync: {e}")
                            messagebox.showerror("Sync Error", f"Failed to sync database: {e}")
                        finally:
                            sync_lock.release()  # Release the lock
                    else:
                        print("Sync already in progress. Skipping this attempt.")
                elif local_mod_time > remote_mod_time:
                    print("Local file is more recent. Syncing...")
                    if sync_lock.acquire(blocking=False):  # Try to acquire the lock
                        try:
                            sync_database()
                            last_modified_time = local_mod_time  # Update the last modified time
                            refresh_callback()  # Refresh the member table
                        except Exception as e:
                            print(f"Error during sync: {e}")
                            messagebox.showerror("Sync Error", f"Failed to sync database: {e}")
                        finally:
                            sync_lock.release()  # Release the lock
                    else:
                        print("Sync already in progress. Skipping this attempt.")
                else:
                    print("Both files are up to date.")

            except Exception as e:
                print(f"Error monitoring database: {e}")
            finally:
                time.sleep(15)  # Sleep for a short interval (e.g., 15 seconds)

    # Start the monitoring thread
    listener_thread = threading.Thread(target=monitor_database, daemon=True)
    listener_thread.start()



def get_available_themes():
    """Get a list of available ttkthemes."""
    style = ttkthemes.ThemedStyle()
    return style.theme_names()




# SQLite Database Configuration
DB_FILE = DATABASE_PATH  # Optionally, assign to another variable if needed


# ==================== ENHANCED DATABASE SCHEMA ====================

def initialize_db():
    try:
        db_path = resource_path("GhanaVi-members.db")
        print(f"Attempting to connect to database at: {db_path}")

        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()

            # Main members table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS members (
                    MemberID TEXT PRIMARY KEY,
                    FirstName TEXT NOT NULL,
                    LastName TEXT NOT NULL,
                    Sex TEXT CHECK(Sex IN ('Male', 'Female')) NOT NULL,
                    Email TEXT NOT NULL,
                    Phone TEXT NOT NULL,
                    Address TEXT NOT NULL,
                    Postcode TEXT NOT NULL,
                    Town TEXT NOT NULL,
                    Province TEXT NOT NULL,
                    CodiceFiscale TEXT NOT NULL,
                    DateOfBirth TEXT NOT NULL,
                    PlaceOfBirth TEXT NOT NULL,
                    JoinDate TEXT NOT NULL,
                    ExpirationDate TEXT NOT NULL,
                    DismissalDate TEXT,
                    MembershipType TEXT DEFAULT 'Ordinary',
                    Status TEXT DEFAULT 'Active',
                    PrefersEmail BOOLEAN DEFAULT 1,
                    PrefersSMS BOOLEAN DEFAULT 0,
                    PreferredLanguage TEXT DEFAULT 'en',
                    EmergencyContactName TEXT,
                    EmergencyContactPhone TEXT
                )
            """)

            # New conflict resolution table
            cursor.execute("""
                            CREATE TABLE IF NOT EXISTS conflict_logs (
                                conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                                table_name TEXT NOT NULL,
                                record_id TEXT NOT NULL,
                                local_version TEXT,
                                remote_version TEXT,
                                resolution_action TEXT CHECK(resolution_action IN ('keep_local', 'keep_remote', 'merged')),
                                resolved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                resolved_by TEXT,
                                conflict_details TEXT
                            )
                        """)

            # Additional tables
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    PaymentID INTEGER PRIMARY KEY,
                    MemberID TEXT,
                    Amount DECIMAL NOT NULL,
                    PaymentDate TEXT NOT NULL,
                    PaymentMethod TEXT,
                    FOREIGN KEY(MemberID) REFERENCES members(MemberID)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    LogID INTEGER PRIMARY KEY,
                    MemberID TEXT,
                    ModifiedBy TEXT,
                    ModificationDate TEXT,
                    ChangesMade TEXT,
                    FOREIGN KEY(MemberID) REFERENCES members(MemberID)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    UserID INTEGER PRIMARY KEY,
                    Username TEXT UNIQUE NOT NULL,
                    PasswordHash TEXT NOT NULL,
                    Role TEXT CHECK(Role IN ('Admin', 'Editor')) DEFAULT 'Editor'
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS family_members (
                    FamilyID INTEGER PRIMARY KEY,
                    MemberID1 TEXT NOT NULL,
                    MemberID2 TEXT NOT NULL,
                    Relationship TEXT NOT NULL,
                    FOREIGN KEY(MemberID1) REFERENCES members(MemberID),
                    FOREIGN KEY(MemberID2) REFERENCES members(MemberID)
                )
            """)

            conn.commit()  # Explicit commit for good measure
            print("Database initialized successfully!")

    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        messagebox.showerror("Database Error",
                           f"Failed to initialize database: {str(e)}\nPath: {db_path}")



class LoginWindow:
    def __init__(self, root, on_success):
        """
        Initialize the login window.
        :param root: The root Tkinter window.
        :param on_success: Callback function to launch the main app after successful login.
        """
        self.root = root
        self.on_success = on_success
        self.current_user = None
        self.root.title("Authorization")
        self.root.geometry("600x300")
        self.root.eval('tk::PlaceWindow . center')  # Center the window on the screen

        # Create a frame to hold the login widgets
        self.login_frame = ttk.Frame(self.root, padding=20)
        self.login_frame.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")

        # Configure the root window to allow the frame to expand
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # Username Label and Entry
        ttk.Label(self.login_frame, text="Username:").grid(row=0, column=0, pady=5, sticky="w")
        self.username_entry = ttk.Entry(self.login_frame, width=25)
        self.username_entry.grid(row=0, column=1, pady=5)

        # Password Label and Entry
        ttk.Label(self.login_frame, text="Password:").grid(row=1, column=0, pady=5, sticky="w")
        self.password_entry = ttk.Entry(self.login_frame, show="*", width=25)
        self.password_entry.grid(row=1, column=1, pady=5)

        # Login Button
        ttk.Button(self.login_frame, text="Login", command=self.validate_login).grid(row=2, column=0, columnspan=2,
                                                                                     pady=10)

    def validate_login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        if not username or not password:
            messagebox.showerror("Error", "Username and password are required!")
            return

        # Check credentials in the database
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
        cursor.execute("SELECT PasswordHash FROM users WHERE Username = ?", (username,))
        result = cursor.fetchone()
        conn.close()

        if result:
            if verify_password(password, result[0]):  # Verify hashed password
                self.current_user = username  # Set the current user
                self.root.withdraw()  # Hide the login window
                # Create a new window for the main application
                main_app_window = tk.Toplevel(self.root)
                self.on_success(main_app_window)  # Launch the main application
            else:
                messagebox.showerror("Error", "Invalid username or password!")
        else:
            messagebox.showerror("Error", "Invalid username or password!")


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt())


def verify_password(password, hashed_password):
    return bcrypt.checkpw(password.encode(), hashed_password)


# ==================== HELPER FUNCTIONS ====================

def get_current_user():
    """
    Returns the currently logged-in user.
    :return: The username of the currently logged-in user, or None if no user is logged in.
    """
    if hasattr(login_window, 'current_user'):
        return login_window.current_user


def is_valid_email(email):
    return re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email)


def is_valid_phone(phone):
    return re.match(r"^[0-9]{6,15}$", phone)


def is_valid_date(date_string):
    date_string = date_string.strip()
    print(f"Debug: Validating date string: '{date_string}'")  # Debug statement
    try:
        # Parse the date string into a datetime object
        date_obj = datetime.strptime(date_string, "%d-%m-%Y")
        # Additional validation for day, month, and year
        day, month, year = map(int, date_string.split('-'))
        if month < 1 or month > 12:
            messagebox.showerror("Error", "Invalid month! Month must be between 1 and 12.")
            return False
        if day < 1 or day > 31:
            messagebox.showerror("Error", "Invalid day! Day must be between 1 and 31.")
            return False
        if year < 1900 or year > datetime.now().year:
            messagebox.showerror("Error", "Invalid year! Year must be between 1900 and the current year.")
            return False
        return True
    except ValueError as e:
        print(f"Debug: Error parsing date: {e}")  # Debug statement
        if not date_string:
            messagebox.showerror("Error", "Date is required!")
        elif len(date_string) != 10 or date_string[2] != '-' or date_string[5] != '-':
            messagebox.showerror("Error", "Invalid date format! Use DD-MM-YYYY.")
        else:
            messagebox.showerror("Error", "Invalid date! Please enter a valid date.")
        return False


def generate_unique_id():
    """Generates a unique member ID in the format GHVI-SXX-XXXX."""
    prefix = "GHVI-S"
    # Generate 6 random digits for the suffix
    suffix = ''.join(random.choices(string.digits, k=6))
    # Format the suffix as XX-XXXX
    formatted_suffix = f"{suffix[:2]}-{suffix[2:]}"
    return f"{prefix}{formatted_suffix}"


def is_id_in_database(member_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
    cursor.execute("SELECT MemberID FROM members WHERE MemberID = ?", (member_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def log_audit(member_id, modified_by, changes_made):
    """
    Logs an audit entry in the database.
    :param member_id: The ID of the member being modified.
    :param modified_by: The username of the user who made the changes.
    :param changes_made: A description of the changes made.
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO audit_logs (MemberID, ModifiedBy, ModificationDate, ChangesMade)
        VALUES (?, ?, ?, ?)
    """, (member_id, modified_by, datetime.now().strftime("%d-%m-%Y %H:%M:%S"), changes_made))
    conn.commit()
    conn.close()


def get_expiring_members():
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()

    today = datetime.now()
    threshold = (today + timedelta(days=730)).strftime("%d-%m-%Y")
    cursor.execute("""
        SELECT * FROM members WHERE ExpirationDate <= ? AND Status = 'Active'
    """, (threshold,))
    members = cursor.fetchall()
    conn.close()
    return members  # Corrected this line to return 'members' instead of 'expiring_members()'



def export_to_excel():
    """Export all members to an Excel file named 'members.xlsx'."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
    cursor.execute("SELECT * FROM members")
    members = cursor.fetchall()
    conn.close()

    if not members:
        messagebox.showerror("Error", "No members to export!")
        return

    file_path = "GHVImembers.xlsx"
    workbook = xlsxwriter.Workbook(file_path)
    worksheet = workbook.add_worksheet()

    headers = ["MemberID", "FirstName", "LastName", "Sex", "Email", "Phone", "Address", "Postcode",
               "Town", "Province", "CodiceFiscale", "DateOfBirth", "PlaceOfBirth", "AdmissionDate", "ExpirationDate",
               "DismissalDate", "MembershipType", "Status", "PrefersEmail", "PrefersSMS", "PreferredLanguage",
               "EmergencyContactName", "EmergencyContactPhone"]
    for col_num, header in enumerate(headers):
        worksheet.write(0, col_num, header)

    for row_num, member in enumerate(members, start=1):
        for col_num, value in enumerate(member):
            worksheet.write(row_num, col_num, value if value is not None else "N/A")

    workbook.close()
    messagebox.showinfo("Success", f"Members exported to {file_path}")


def update_member(member_id, first_name, last_name, sex, email, phone, address, postcode, town, province,
                  codice_fiscale, date_of_birth, place_of_birth, join_date, dismissal_date, membership_type,
                  prefers_email, prefers_sms, preferred_language, emergency_name, emergency_phone):
    """Update an existing member in the database."""
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
        cursor.execute("""
            UPDATE members
            SET FirstName = ?, LastName = ?, Sex = ?, Email = ?, Phone = ?, Address = ?, Postcode = ?, Town = ?, Province = ?,
                CodiceFiscale = ?, DateOfBirth = ?, PlaceOfBirth = ?, JoinDate = ?, DismissalDate = ?, MembershipType = ?,
                PrefersEmail = ?, PrefersSMS = ?, PreferredLanguage = ?, EmergencyContactName = ?, EmergencyContactPhone = ?
            WHERE MemberID = ?
        """, (first_name, last_name, sex, email, phone, address, postcode, town, province, codice_fiscale,
              date_of_birth, place_of_birth, join_date, dismissal_date, membership_type, prefers_email,
              prefers_sms, preferred_language, emergency_name, emergency_phone, member_id))
        conn.commit()
        log_audit(member_id, logged_in_user, "Updated member info")
        messagebox.showinfo("Success", "Member info updated!")
    except sqlite3.Error as e:
        messagebox.showerror("Database Error", f"Update failed: {e}")
    finally:
        conn.close()


def record_payment(member_id, amount, method):
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO payments VALUES (?,?,?,?,?)
        """, (None, member_id, float(amount), datetime.now().strftime("%d-%m-%Y"), method))
        conn.commit()
        log_audit(member_id, logged_in_user, f"Recorded payment: {amount} via {method}")
        messagebox.showinfo("Success", "Payment recorded!")
    except ValueError:
        messagebox.showerror("Error", "Invalid amount!")
    except sqlite3.Error as e:
        messagebox.showerror("Database Error", f"Failed to record payment: {e}")
    finally:
        conn.close()


def add_family_member(member_id1, member_id2, relationship):
    """
    Add a family relationship between two members to the database.
    :param member_id1: The ID of the first member.
    :param member_id2: The ID of the second member.
    :param relationship: The relationship between the two members (e.g., "Spouse", "Child").
    """
    try:
        # Check if both member IDs exist in the database
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()

        # Check if member_id1 exists
        cursor.execute("SELECT MemberID FROM members WHERE MemberID = ?", (member_id1,))
        if not cursor.fetchone():
            messagebox.showerror("Error", f"Member ID {member_id1} does not exist!")
            return

        # Check if member_id2 exists
        cursor.execute("SELECT MemberID FROM members WHERE MemberID = ?", (member_id2,))
        if not cursor.fetchone():
            messagebox.showerror("Error", f"Member ID {member_id2} does not exist!")
            return

        # Insert the family relationship into the database
        cursor.execute("""
            INSERT INTO family_members (MemberID1, MemberID2, Relationship)
            VALUES (?, ?, ?)
        """, (member_id1, member_id2, relationship))

        conn.commit()
        conn.close()
        messagebox.showinfo("Success", "Family relationship added successfully!")
    except sqlite3.Error as e:
        messagebox.showerror("Database Error", f"Failed to add family relationship: {e}")


def add_family_member_ui():
    # Create a new Toplevel window
    family_window = tk.Toplevel()
    family_window.title("Add Family Member")
    family_window.geometry("400x300")

    # Create and place labels and entry fields
    ttk.Label(family_window, text="Member ID 1:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
    member_id1_entry = ttk.Entry(family_window, width=30)
    member_id1_entry.grid(row=0, column=1, padx=10, pady=10)

    ttk.Label(family_window, text="Member ID 2:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
    member_id2_entry = tk.Entry(family_window, width=30)
    member_id2_entry.grid(row=1, column=1, padx=10, pady=10)

    ttk.Label(family_window, text="Relationship:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
    relationship_entry = ttk.Entry(family_window, width=30)
    relationship_entry.grid(row=2, column=1, padx=10, pady=10)

    # Function to handle the "Add Family Member" button click
    def on_add_family_member():
        member_id1 = member_id1_entry.get().strip()
        member_id2 = member_id2_entry.get().strip()
        relationship = relationship_entry.get().strip()

        if not member_id1 or not member_id2 or not relationship:
            messagebox.showerror("Error", "All fields are required!")
            return

        if member_id1 == member_id2:
            messagebox.showerror("Error", "Member IDs must be different!")
            return

        try:
            # Call the add_family_member function
            add_family_member(member_id1, member_id2, relationship)
            messagebox.showinfo("Success", "Family relationship added successfully!")
            family_window.destroy()  # Close the window after adding
        except Exception as e:
            messagebox.showerror("Error", f"Failed to add family relationship: {e}")

    # Add the "Add Family Member" button
    ttk.Button(family_window, text="Add Family Member", command=on_add_family_member).grid(row=3, column=0,
                                                                                           columnspan=2)


def check_for_updates():
    """Check for updates silently and prompt the user if an update is available."""
    try:
        # Fetch the latest version number from the remote server
        response = requests.get(UPDATE_INFO_URL)
        response.raise_for_status()  # Raise an error for bad responses
        latest_version = response.text.strip()

        # Compare the current version with the latest version
        if latest_version != CURRENT_VERSION:
            confirm = messagebox.askyesno("Update Available",
                                          f"Version {latest_version} is available. Do you want to update now?")
            if confirm:
                update_app(latest_version)
    except requests.exceptions.RequestException as e:
        print(f"Failed to check for updates: {e}")  # Debug print
    except Exception as e:
        print(f"An error occurred: {e}")  # Debug print


def update_app(latest_version):
    """Update the application to the latest version."""
    try:
        # Download the latest version of the application as a ZIP file
        response = requests.get(UPDATE_FILE_URL)
        response.raise_for_status()  # Raise an error for bad responses

        # Save the downloaded ZIP file to a temporary location
        temp_zip_path = writable_path(f"GHVI_Membership_Management_{latest_version}.zip")
        with open(temp_zip_path, 'wb') as f:
            f.write(response.content)

        # Extract the ZIP file
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            zip_ref.extractall(writable_path(""))

        # Define the path to the extracted application file
        extracted_file_path = writable_path(f"GHVI_Membership_Management_{latest_version}.py")

        # Replace the current application file with the extracted one
        current_path = os.path.abspath(__file__)
        shutil.copyfile(extracted_file_path, current_path)

        # Remove the temporary ZIP file and the extracted file
        os.remove(temp_zip_path)
        os.remove(extracted_file_path)

        # Restart the application
        messagebox.showinfo("Update Success", f"Application updated to version {latest_version}. Restarting...")
        subprocess.Popen([sys.executable, current_path])
        sys.exit()

    except requests.exceptions.RequestException as e:
        messagebox.showerror("Update Error", f"Failed to download the update: {e}")
    except zipfile.BadZipFile as e:
        messagebox.showerror("Update Error", f"Failed to extract the update: {e}")
    except Exception as e:
        messagebox.showerror("Update Error", f"Failed to update the application: {e}")


# Global variable for the last modified time of the database
last_modified_time = None

def get_remote_file_mod_time(service):
    """Get the modification time of the remote file from Google Drive."""
    try:
        file_metadata = service.files().get(fileId=GOOGLE_DRIVE_FILE_ID, fields='modifiedTime').execute()
        remote_mod_time = file_metadata.get('modifiedTime')
        remote_mod_time = datetime.strptime(remote_mod_time[:-5], '%Y-%m-%dT%H:%M:%S')  # Convert to datetime object
        remote_mod_time = remote_mod_time.timestamp()  # Convert to timestamp
        return remote_mod_time
    except Exception as e:
        print(f"Error fetching remote file modification time: {e}")
        return None


def start_real_time_listener(root, refresh_callback):
    """
    Start a background thread to monitor the database for changes.
    :param root: The Tkinter root window.
    :param refresh_callback: The function to call when changes are detected.
    """
    def monitor_database():
        global last_modified_time
        while True:
            try:
                # Get the Google Drive service
                service = get_google_drive_service()

                # Fetch the remote file's modification time
                remote_mod_time = get_remote_file_mod_time(service)
                if remote_mod_time is None:
                    print("Failed to fetch remote file modification time. Skipping sync.")
                    time.sleep(15)  # Sleep for a short interval (e.g., 15 seconds)
                    continue

                # Check the last modified time of the local database file
                local_mod_time = os.path.getmtime(DATABASE_PATH)

                if last_modified_time is None:
                    last_modified_time = local_mod_time
                elif remote_mod_time > last_modified_time:
                    print("Remote file is more recent. Syncing...")
                    if sync_lock.acquire(blocking=False):  # Try to acquire the lock
                        try:
                            sync_database()
                            last_modified_time = remote_mod_time  # Update the last modified time
                            refresh_callback()  # Refresh the member table
                        except Exception as e:
                            print(f"Error during sync: {e}")
                            messagebox.showerror("Sync Error", f"Failed to sync database: {e}")
                        finally:
                            sync_lock.release()  # Release the lock
                    else:
                        print("Sync already in progress. Skipping this attempt.")
                elif local_mod_time > remote_mod_time:
                    print("Local file is more recent. Syncing...")
                    if sync_lock.acquire(blocking=False):  # Try to acquire the lock
                        try:
                            sync_database()
                            last_modified_time = local_mod_time  # Update the last modified time
                            refresh_callback()  # Refresh the member table
                        except Exception as e:
                            print(f"Error during sync: {e}")
                            messagebox.showerror("Sync Error", f"Failed to sync database: {e}")
                        finally:
                            sync_lock.release()  # Release the lock
                    else:
                        print("Sync already in progress. Skipping this attempt.")
                else:
                    print("Both files are up to date.")

            except Exception as e:
                print(f"Error monitoring database: {e}")
            finally:
                time.sleep(15)  # Sleep for a short interval (e.g., 15 seconds)

    # Start the monitoring thread
    listener_thread = threading.Thread(target=monitor_database, daemon=True)
    listener_thread.start()



def refresh_member_table():
    """Refresh the member table in the GUI."""
    global member_tree  # Declare member_tree as global
    for item in member_tree.get_children():
        member_tree.delete(item)
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM members")
        members = cursor.fetchall()
        for member in members:
            member_tree.insert("", tk.END, values=member)



def main():

    try:
        print("Main function started!")  # Debug statement

        # Initialize database and sync before starting the main application
        print("Initializing database...")  # Debug statement
        initialize_db()
        print("Database initialized successfully!")  # Debug statement

        # Sync database before starting the main application
        print("Syncing database with Google Drive...")  # Debug statement
        global root
        root = tk.Tk()
        root.title("GHANAVI-APS Enhanced Membership Management System")
        root.geometry("1450x1000")

        # Set the default ttk theme
        style = ttkthemes.ThemedStyle(root)
        style.set_theme('arc')  # Use 'arc' for a modern look

        # Ensure the main window is resizable
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1)

        # Setup connectivity light
        setup_connectivity_light(root)

        # Initialize last_modified_time with the current local file modification time
        global last_modified_time
        last_modified_time = os.path.getmtime(DATABASE_PATH)

        # Sync database before starting the main application
        sync_db_if_needed_with_conflict_resolution(last_sync_label)  # Pass last_sync_label here

        # Check for updates
        print("Checking for updates...")  # Debug statement
        check_for_updates()

        # Start the real-time sync thread
        start_real_time_sync()

        # Start the real-time listener
        start_real_time_listener(root, refresh_member_table)

        # Launch the main application
        global login_window
        login_window = LoginWindow(root, lambda window: launch_main_app(window))
        root.mainloop()
    except Exception as e:
        print(f"Error in main: {str(e)}")  # Debug print
        messagebox.showerror("Error", f"Application error: {str(e)}")





# ==================== GUI with Scrollable Frame ====================
def apply_filters(status_filter_combobox, membership_type_filter_combobox):
    """Apply filters to the member list based on status and membership type."""
    status = status_filter_combobox.get().strip()
    membership_type = membership_type_filter_combobox.get().strip()

    # Clear previous search results
    for item in member_tree.get_children():
        member_tree.delete(item)

    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()

    # Construct the query based on the provided criteria
    query = "SELECT * FROM members WHERE 1=1"
    params = []

    if status and status != "All":
        query += " AND Status = ?"
        params.append(status)
    if membership_type and membership_type != "All":
        query += " AND MembershipType = ?"
        params.append(membership_type)

    # Retrieve member details
    cursor.execute(query, params)
    members = cursor.fetchall()
    conn.close()

    # Insert members into the Treeview
    for member in members:
        member_tree.insert("", tk.END, values=member)

def get_available_themes():
    """Get a list of available ttkthemes."""
    style = ttkthemes.ThemedStyle()
    return style.theme_names()

def apply_theme(theme_name):
    """Apply the selected theme."""
    try:
        style = ttkthemes.ThemedStyle(root)
        style.set_theme(theme_name)
        print(f"Theme applied: {theme_name}")
    except Exception as e:
        print(f"Error applying theme: {e}")
        messagebox.showerror("Theme Error", f"Failed to apply theme: {e}")

# Global variable for the member tree
member_tree = None

def launch_main_app(root):
    """Launch the main application after successful login."""
    global member_tree  # Declare member_tree as global

    root.title("GHANAVI-APS Enhanced Membership Management System")
    root.geometry("1450x1000")

    # Configure root grid to allow resizing
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)

    # Create a canvas and scrollbar in root
    canvas = tk.Canvas(root)
    canvas.grid(row=0, column=0, sticky="nsew")

    v_scrollbar = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
    v_scrollbar.grid(row=0, column=1, sticky="ns")
    canvas.configure(yscrollcommand=v_scrollbar.set)

    # Horizontal Scrollbar
    h_scrollbar = ttk.Scrollbar(root, orient="horizontal", command=canvas.xview)
    h_scrollbar.grid(row=1, column=0, sticky="ew")
    canvas.configure(xscrollcommand=h_scrollbar.set)

    # Create a container frame inside the canvas
    container = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=container, anchor="nw")

    def on_frame_configure(event):
        """Update the scroll region of the canvas to encompass the container."""
        canvas.configure(scrollregion=canvas.bbox("all"))

    container.bind("<Configure>", on_frame_configure)

    # Configure container grid to allow resizing
    container.grid_rowconfigure(0, weight=1)
    container.grid_columnconfigure(0, weight=1)


    # ==================== Place GUI Components in 'container' ===================

    # Function to get the absolute path to the logo
    def get_logo_path():
        # Assuming the logo is in the same directory as the script
        base_path = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_path, "trilogo-rbg.png")
        return logo_path

    # Load the logo
    logo_path = get_logo_path()
    if os.path.exists(logo_path):
        try:
            logo_image = Image.open(logo_path).resize((700, 300), Image.LANCZOS)
            logo_photo = ImageTk.PhotoImage(logo_image)
            logo_label = tk.Label(container, image=logo_photo)
            logo_label.grid(row=0, column=0, columnspan=2, pady=5, sticky="w")  # Centered at the top
            logo_label.image = logo_photo  # Keep a reference to avoid garbage collection
        except Exception as e:
            messagebox.showwarning("Logo Error", f"Could not load logo: {e}")
    else:
        messagebox.showwarning("Logo Error", "Logo file does not exist.")

    print("Main application GUI created successfully!")  # Debug statement

    # Input Form (Row 1)
    input_frame = ttk.Frame(container, padding=10)
    input_frame.grid(row=1, column=0, sticky="w", padx=10)

    # Do not set column weights to keep fields fixed in size

    # Create input fields without expansion (use sticky="w" and fixed width)
    field_options = {"width": 30}  # fixed width for entries

    ttk.Label(input_frame, text="Member ID (leave blank to auto-generate)").grid(row=0, column=0, sticky="w", padx=5,
                                                                                 pady=5)
    member_id_entry = ttk.Entry(input_frame, **field_options)
    member_id_entry.grid(row=0, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="First Name").grid(row=1, column=0, sticky="w", padx=5, pady=5)
    first_name_entry = ttk.Entry(input_frame, **field_options)
    first_name_entry.grid(row=1, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="Last Name").grid(row=1, column=5, sticky="w", padx=5, pady=5)
    last_name_entry = ttk.Entry(input_frame, **field_options)
    last_name_entry.grid(row=1, column=6, padx=5, pady=5)

    ttk.Label(input_frame, text="Sex").grid(row=3, column=0, sticky="w", padx=5, pady=5)
    sex_combobox = ttk.Combobox(input_frame, values=["Male", "Female"], state="readonly", width=28)
    sex_combobox.grid(row=3, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="Email").grid(row=3, column=5, sticky="w", padx=5, pady=5)
    email_entry = ttk.Entry(input_frame, **field_options)
    email_entry.grid(row=3, column=6, padx=5, pady=5)

    ttk.Label(input_frame, text="Phone").grid(row=5, column=0, sticky="w", padx=5, pady=5)
    phone_entry = ttk.Entry(input_frame, **field_options)
    phone_entry.grid(row=5, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="Address").grid(row=5, column=5, sticky="w", padx=5, pady=5)
    address_entry = ttk.Entry(input_frame, **field_options)
    address_entry.grid(row=5, column=6, padx=5, pady=5)

    ttk.Label(input_frame, text="Postcode").grid(row=7, column=0, sticky="w", padx=5, pady=5)
    postcode_entry = ttk.Entry(input_frame, **field_options)
    postcode_entry.grid(row=7, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="Town").grid(row=7, column=5, sticky="w", padx=5, pady=5)
    town_entry = ttk.Entry(input_frame, **field_options)
    town_entry.grid(row=7, column=6, padx=5, pady=5)

    ttk.Label(input_frame, text="Province").grid(row=9, column=0, sticky="w", padx=5, pady=5)
    province_entry = ttk.Entry(input_frame, **field_options)
    province_entry.grid(row=9, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="Codice Fiscale").grid(row=9, column=5, sticky="w", padx=5, pady=5)
    cf_entry = ttk.Entry(input_frame, **field_options)
    cf_entry.grid(row=9, column=6, padx=5, pady=5)

    ttk.Label(input_frame, text="Date of Birth (DD-MM-YYYY)").grid(row=10, column=0, sticky="w", padx=5, pady=5)
    dob_entry = ttk.Entry(input_frame, **field_options)
    dob_entry.grid(row=10, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="Place of Birth").grid(row=10, column=5, sticky="w", padx=5, pady=5)
    pob_entry = ttk.Entry(input_frame, **field_options)
    pob_entry.grid(row=10, column=6, padx=5, pady=5)

    ttk.Label(input_frame, text="Admission Date (DD-MM-YYYY)").grid(row=11, column=0, sticky="w", padx=5, pady=5)
    join_date_entry = ttk.Entry(input_frame, **field_options)
    join_date_entry.grid(row=11, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="Dismissal Date (optional)").grid(row=11, column=5, sticky="w", padx=5, pady=5)
    dismissal_date_entry = ttk.Entry(input_frame, **field_options)
    dismissal_date_entry.grid(row=11, column=6, padx=5, pady=5)

    ttk.Label(input_frame, text="Membership Type").grid(row=15, column=0, sticky="w", padx=5, pady=5)
    membership_combobox = ttk.Combobox(input_frame, values=["Ordinary", "Premium", "Lifetime", "Honorary"],
                                       state="readonly", width=28)
    membership_combobox.grid(row=15, column=1, padx=5, pady=5)
    membership_combobox.set("Ordinary")

    ttk.Label(input_frame, text="Communication Preferences").grid(row=16, column=0, sticky="w", padx=5, pady=5)
    prefers_email_var = tk.BooleanVar(value=True)
    prefers_sms_var = tk.BooleanVar(value=False)
    email_check = ttk.Checkbutton(input_frame, text="Email Updates", variable=prefers_email_var)
    email_check.grid(row=16, column=1, sticky="w", padx=5, pady=2)
    sms_check = ttk.Checkbutton(input_frame, text="SMS Updates", variable=prefers_sms_var)
    sms_check.grid(row=17, column=1, sticky="w", padx=5, pady=2)

    ttk.Label(input_frame, text="Preferred Language").grid(row=16, column=5, sticky="w", padx=5, pady=5)
    language_combobox = ttk.Combobox(input_frame, values=["English", "Twi", "Ga", "Ewe", "Hausa"], state="readonly",
                                     width=28)
    language_combobox.grid(row=16, column=6, padx=5, pady=5)
    language_combobox.set("English")

    ttk.Label(input_frame, text="Emergency Contact Name").grid(row=19, column=0, sticky="w", padx=5, pady=5)
    emergency_name_entry = ttk.Entry(input_frame, **field_options)
    emergency_name_entry.grid(row=19, column=1, padx=5, pady=5)

    ttk.Label(input_frame, text="Emergency Contact Phone").grid(row=19, column=5, sticky="w", padx=5, pady=5)
    emergency_phone_entry = ttk.Entry(input_frame, **field_options)
    emergency_phone_entry.grid(row=19, column=6, padx=5, pady=5)

    # ==================== BUTTONS ====================
    button_frame = ttk.Frame(container, padding=10)
    button_frame.grid(row=2, column=0, sticky="w", padx=10)

    ttk.Button(button_frame, text="Add Member", command=lambda: add_member(
        member_id_entry, first_name_entry, last_name_entry, sex_combobox, email_entry, phone_entry, address_entry,
        postcode_entry, town_entry, province_entry, cf_entry, dob_entry, pob_entry, join_date_entry,
        dismissal_date_entry, membership_combobox, prefers_email_var, prefers_sms_var, language_combobox,
        emergency_name_entry, emergency_phone_entry
    )).grid(row=0, column=0, padx=5)

    ttk.Button(button_frame, text="Update Member Info", command=lambda: update_member_ui(root)).grid(row=0, column=1,
                                                                                                     padx=5)
    ttk.Button(button_frame, text="Search Member", command=search_member_ui).grid(row=0, column=2, padx=5)
    ttk.Button(button_frame, text="Export to Excel", command=export_to_excel).grid(row=0, column=3, padx=5)
    ttk.Button(button_frame, text="Clear Fields", command=lambda: clear_fields(
        member_id_entry, first_name_entry, last_name_entry, sex_combobox, email_entry, phone_entry,
        address_entry, postcode_entry, town_entry, province_entry, cf_entry, dob_entry, pob_entry,
        join_date_entry, dismissal_date_entry, membership_combobox, prefers_email_var, prefers_sms_var,
        language_combobox, emergency_name_entry, emergency_phone_entry
    )).grid(row=0, column=4, padx=5)

    ttk.Button(button_frame, text="Member Payments", command=record_payment_ui).grid(row=0, column=5, padx=5)
    ttk.Button(button_frame, text="Add Family Members", command=add_family_member_ui).grid(row=0, column=6, padx=5)
    # Add the "Manage Users" button only for adminX
    current_user = get_current_user()
    if current_user == "adminX":
        ttk.Button(button_frame, text="Manage Users", command=lambda: manage_users_ui(root)).grid(row=0, column=7,
                                                                                                  padx=5)

    # Add the "Delete Member" button
    current_user = get_current_user()
    if current_user == "adminX":
        ttk.Button(button_frame, text="Delete Member", command=lambda: delete_member_ui(root)).grid(row=1, column=0,
                                                                                                    padx=5)

    # ==================== FILTERS ====================
    filter_frame = ttk.Frame(container, padding=20)
    filter_frame.grid(row=0, column=0, sticky="ns", padx=20)

    ttk.Label(filter_frame, text="Status:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
    status_filter_combobox = ttk.Combobox(filter_frame, values=["Active", "Inactive"], state="readonly", width=28)
    status_filter_combobox.grid(row=0, column=1, padx=5, pady=5)
    status_filter_combobox.set("Active")

    ttk.Label(filter_frame, text="Membership Type:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
    membership_type_filter_combobox = ttk.Combobox(filter_frame,
                                                   values=["All", "Ordinary", "Premium", "Lifetime", "Honorary"],
                                                   state="readonly", width=28)
    membership_type_filter_combobox.grid(row=1, column=1, padx=5, pady=5)
    membership_type_filter_combobox.set("Ordinary")

    # Pass the combobox widgets to the apply_filters function
    ttk.Button(filter_frame, text="Apply Filters",
               command=lambda: apply_filters(status_filter_combobox, membership_type_filter_combobox)).grid(row=2,
                                                                                                            column=0,
                                                                                                            columnspan=2,
                                                                                                            pady=10)

    # Member Table (Row 3)
    table_frame = ttk.Frame(container, padding=10)
    table_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", padx=10)

    table_columns = ["MemberID", "FirstName", "LastName", "Sex", "Email", "Phone", "Address", "Postcode",
                     "Town", "Province", "CodiceFiscale", "DateOfBirth", "PlaceOfBirth", "JoinDate", "ExpirationDate",
                     "DismissalDate", "MembershipType", "Status", "PrefersEmail", "PrefersSMS", "PreferredLanguage",
                     "EmergencyContactName", "EmergencyContactPhone"]
    member_tree = ttk.Treeview(table_frame, columns=table_columns, show="headings")
    for col in table_columns:
        member_tree.heading(col, text=col)
        member_tree.column(col, width=100, stretch=True)  # Allow columns to stretch
    member_tree.grid(row=0, column=0, sticky="nsew")

    # Bind the Treeview selection event to a function
    member_tree.bind("<Double-1>", lambda event: show_member_details(member_tree))

    # ==================== Connectivity Light ====================
    setup_connectivity_light(root)  # Call the function to setup the connectivity light and buttons

    # Populate the member table when the app starts
    refresh_member_table()


def show_member_details(member_tree):
    # Get the selected item from the Treeview
    selected_item = member_tree.selection()
    if not selected_item:  # If no item is selected, do nothing
        return

    # Get the MemberID from the selected row
    member_id = member_tree.item(selected_item, "values")[0]

    # Fetch member details from the database
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
    cursor.execute("SELECT * FROM members WHERE MemberID = ?", (member_id,))
    member_data = cursor.fetchone()
    conn.close()

    if not member_data:
        messagebox.showerror("Error", "Member not found!")
        return

    # Create a Toplevel window to display member details
    details_window = tk.Toplevel()
    details_window.title(f"Member Details - {member_id}")
    details_window.geometry("600x800")

    # Create a frame to hold the details
    details_frame = ttk.Frame(details_window, padding=20)
    details_frame.pack(fill=tk.BOTH, expand=True)

    # Display member details
    fields = [
        ("Member ID", member_data[0]),
        ("First Name", member_data[1]),
        ("Last Name", member_data[2]),
        ("Sex", member_data[3]),
        ("Email", member_data[4]),
        ("Phone", member_data[5]),
        ("Address", member_data[6]),
        ("Postcode", member_data[7]),
        ("Town", member_data[8]),
        ("Province", member_data[9]),
        ("Codice Fiscale", member_data[10]),
        ("Date of Birth", member_data[11]),
        ("Place of Birth", member_data[12]),
        ("Join Date", member_data[13]),
        ("Expiration Date", member_data[14]),
        ("Dismissal Date", member_data[15] or "N/A"),
        ("Membership Type", member_data[16]),
        ("Status", member_data[17]),
        ("Prefers Email", "Yes" if member_data[18] else "No"),
        ("Prefers SMS", "Yes" if member_data[19] else "No"),
        ("Preferred Language", member_data[20]),
        ("Emergency Contact Name", member_data[21]),
        ("Emergency Contact Phone", member_data[22]),
    ]

    for row, (label, value) in enumerate(fields):
        ttk.Label(details_frame, text=f"{label}:", font=("Arial", 10, "bold")).grid(row=row, column=0, sticky="w",
                                                                                    padx=5, pady=5)
        ttk.Label(details_frame, text=value).grid(row=row, column=1, sticky="w", padx=5, pady=5)

    # Add a "Close" button
    ttk.Button(details_window, text="Close", command=details_window.destroy).pack(pady=10)


def refresh_member_table():
    """Refresh the member table in the GUI."""
    global member_tree
    if member_tree is None:  # Check if member_tree is initialized
        return
    for item in member_tree.get_children():
        member_tree.delete(item)
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM members")
        members = cursor.fetchall()
        for member in members:
            member_tree.insert("", tk.END, values=member)

def sync_button_click():
    sync_db_if_needed_with_conflict_resolution(last_sync_label)


def clear_fields(
        member_id_entry, first_name_entry, last_name_entry, sex_combobox, email_entry, phone_entry,
        address_entry, postcode_entry, town_entry, province_entry, cf_entry, dob_entry, pob_entry,
        join_date_entry, dismissal_date_entry, membership_combobox, prefers_email_var, prefers_sms_var,
        language_combobox, emergency_name_entry, emergency_phone_entry
):
    member_id_entry.delete(0, tk.END)
    first_name_entry.delete(0, tk.END)
    last_name_entry.delete(0, tk.END)
    sex_combobox.set("")
    email_entry.delete(0, tk.END)
    phone_entry.delete(0, tk.END)
    address_entry.delete(0, tk.END)
    postcode_entry.delete(0, tk.END)
    town_entry.delete(0, tk.END)
    province_entry.delete(0, tk.END)
    cf_entry.delete(0, tk.END)
    dob_entry.delete(0, tk.END)
    pob_entry.delete(0, tk.END)
    join_date_entry.delete(0, tk.END)
    dismissal_date_entry.delete(0, tk.END)
    membership_combobox.set("Ordinary")
    prefers_email_var.set(True)
    prefers_sms_var.set(False)
    language_combobox.set("English")
    emergency_name_entry.delete(0, tk.END)
    emergency_phone_entry.delete(0, tk.END)


SMTP_SERVER = "smtp.gmail.com"  # Replace with your SMTP server
SMTP_PORT = 587  # Replace with your SMTP port
SENDER_EMAIL = "info.ghanavi@gmail.com"  # Replace with your email
SENDER_PASSWORD = "ider uktg ixwm rrvs"  # Replace with your email password


def send_member_email(member_id, email, first_name, last_name):
    """
    Sends an email to the member with their member ID.
    """
    try:
        # Create the email
        subject = "Welcome to GHANAVI-APS"
        body = f"""
        Dear {first_name} {last_name},

        Welcome to GHANAVI-APS! Your membership has been successfully created.

        Your Member ID is: {member_id}

        Please keep this ID safe for future reference. 

        You will be contacted for collection or postage of your Membership Card.
        Please Note that the Card is Valid for 2 years, which gives you free access
        to selected events and services. Renewable after 2 years at a fee.

        For any information do not hesitate to contact us on WhatsApp: 
        https://wa.me/393513208866

        Best regards,
        GHANAVI-APS Team

        www.ghanaiansinvicenza.org

        """

        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # Connect to the SMTP server and send the email
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()  # Secure the connection
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)

        print(f"Email sent to {email}")  # Debug statement
    except Exception as e:
        print(f"Failed to send email: {e}")  # Debug statement


def is_member_exists(first_name, last_name, dob):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM members 
        WHERE FirstName = ? AND LastName = ? AND DateOfBirth = ?
    """, (first_name, last_name, dob))
    member_exists = cursor.fetchone()
    conn.close()
    return member_exists is not None


def delete_member_ui(parent_window):
    """Open a top-level window to delete a member by Member ID or full name and date of birth."""
    delete_window = tk.Toplevel(parent_window)
    delete_window.title("Delete Member")
    delete_window.geometry("600x400")

    # Configure grid weights for resizing
    delete_window.grid_rowconfigure(1, weight=1)
    delete_window.grid_columnconfigure(0, weight=1)

    # Variable to hold the selected search type
    search_type = tk.StringVar(value="member_id")

    # Radio buttons for search type
    ttk.Radiobutton(delete_window, text="Delete by Member ID", variable=search_type, value="member_id").grid(row=0,
                                                                                                             column=0,
                                                                                                             padx=10,
                                                                                                             pady=5,
                                                                                                             sticky="w")
    ttk.Radiobutton(delete_window, text="Delete by Name and Date of Birth", variable=search_type,
                    value="name_dob").grid(row=1, column=0, padx=10, pady=5, sticky="w")

    # Entry fields for Member ID search
    ttk.Label(delete_window, text="Member ID:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
    member_id_entry = ttk.Entry(delete_window, width=30)
    member_id_entry.grid(row=2, column=1, padx=10, pady=10)

    # Entry fields for First Name, Last Name, and Date of Birth search
    ttk.Label(delete_window, text="First Name:").grid(row=3, column=0, padx=10, pady=10, sticky="w")
    first_name_entry = ttk.Entry(delete_window, width=30)
    first_name_entry.grid(row=3, column=1, padx=10, pady=10)

    ttk.Label(delete_window, text="Last Name:").grid(row=4, column=0, padx=10, pady=10, sticky="w")
    last_name_entry = ttk.Entry(delete_window, width=30)
    last_name_entry.grid(row=4, column=1, padx=10, pady=10)

    ttk.Label(delete_window, text="Date of Birth (DD-MM-YYYY):").grid(row=5, column=0, padx=10, pady=10, sticky="w")
    dob_entry = ttk.Entry(delete_window, width=30)
    dob_entry.grid(row=5, column=1, padx=10, pady=10)

    # Function to perform the deletion
    def perform_deletion():
        selected_type = search_type.get()

        if selected_type == "member_id":
            member_id = member_id_entry.get().strip()
            if not member_id:
                messagebox.showerror("Error", "Member ID is required!")
                return

            # Fetch member details from the database
            with sqlite3.connect(DATABASE_PATH) as conn:
                cursor = conn.cursor()
            cursor.execute("SELECT FirstName, LastName FROM members WHERE MemberID = ?", (member_id,))
            member_data = cursor.fetchone()
            conn.close()

            if not member_data:
                messagebox.showerror("Error", "Member not found!")
                return

            first_name, last_name = member_data

            # Ask for confirmation
            confirm = messagebox.askyesno("Confirm Deletion",
                                          f"Are you sure you want to delete member '{first_name} {last_name}' (ID: {member_id})?")
            if confirm:
                delete_member(member_id)
                delete_window.destroy()  # Close the window after deletion

        elif selected_type == "name_dob":
            first_name = first_name_entry.get().strip()
            last_name = last_name_entry.get().strip()
            dob = dob_entry.get().strip()

            if not first_name or not last_name or not dob:
                messagebox.showerror("Error", "First Name, Last Name, and Date of Birth are required!")
                return

            if not is_valid_date(dob):
                return

            # Fetch member details from the database
            with sqlite3.connect(DATABASE_PATH) as conn:
                cursor = conn.cursor()
            cursor.execute("SELECT MemberID FROM members WHERE FirstName = ? AND LastName = ? AND DateOfBirth = ?",
                           (first_name, last_name, dob))
            member_data = cursor.fetchone()
            conn.close()

            if not member_data:
                messagebox.showerror("Error", "Member not found!")
                return

            member_id = member_data[0]

            # Ask for confirmation
            confirm = messagebox.askyesno("Confirm Deletion",
                                          f"Are you sure you want to delete member '{first_name} {last_name}' (ID: {member_id})?")
            if confirm:
                delete_member(member_id)
                delete_window.destroy()  # Close the window after deletion

    # Add the "Delete" button
    ttk.Button(delete_window, text="Delete", command=perform_deletion).grid(row=6, column=0, padx=10, pady=20)

    # Add the "Cancel" button
    ttk.Button(delete_window, text="Cancel", command=delete_window.destroy).grid(row=6, column=1, padx=10, pady=20)

    # Make window modal
    delete_window.grab_set()
    delete_window.transient(parent_window)
    delete_window.wait_window()


def delete_member(member_id):
    """Delete a member from the database."""
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
        cursor.execute("DELETE FROM members WHERE MemberID = ?", (member_id,))
        conn.commit()
        log_audit(member_id, get_current_user(), "Deleted member")
        messagebox.showinfo("Success", "Member deleted successfully!")
        refresh_member_table()
    except sqlite3.Error as e:
        messagebox.showerror("Database Error", f"Failed to delete member: {e}")
    finally:
        conn.close()


def add_member(
        member_id_entry, first_name_entry, last_name_entry, sex_combobox, email_entry, phone_entry, address_entry,
        postcode_entry, town_entry, province_entry, cf_entry, dob_entry, pob_entry, join_date_entry,
        dismissal_date_entry, membership_combobox, prefers_email_var, prefers_sms_var, language_combobox,
        emergency_name_entry, emergency_phone_entry
):
    try:
        # Validate required fields
        if not first_name_entry.get().strip() or not last_name_entry.get().strip():
            messagebox.showerror("Error", "First Name and Last Name are required!")
            return

        # Validate contact information
        email = email_entry.get()
        phone = phone_entry.get()
        if not is_valid_email(email):
            messagebox.showerror("Error", "Invalid email format!")
            return
        if not is_valid_phone(phone):
            messagebox.showerror("Error", "Invalid phone number! Use only digits.")
            return

        # Validate dates
        if not all([
            is_valid_date(dob_entry.get()),
            is_valid_date(join_date_entry.get()),
            is_valid_date(dismissal_date_entry.get()) if dismissal_date_entry.get() else True
        ]):
            return

        # Get values from entries
        first_name = first_name_entry.get().strip()
        last_name = last_name_entry.get().strip()
        dob = dob_entry.get().strip()

        # Check if member already exists
        if is_member_exists(first_name, last_name, dob):
            messagebox.showerror("Error",
                                 "A member with the same First Name, Last Name, and Date of Birth already exists in the database.")
            return

        # Generate unique member ID
        member_id = member_id_entry.get().strip() or generate_unique_id()
        while is_id_in_database(member_id):
            member_id = generate_unique_id()

        # Calculate expiration date
        join_date = datetime.strptime(join_date_entry.get(), "%d-%m-%Y")
        expiration_date = (join_date + timedelta(days=730)).strftime("%d-%m-%Y")

        # Database operation
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO members VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
        """, (
            member_id,
            first_name,
            last_name,
            sex_combobox.get(),
            email,
            phone,
            address_entry.get(),
            postcode_entry.get(),
            town_entry.get(),
            province_entry.get(),
            cf_entry.get(),
            dob,
            pob_entry.get(),
            join_date_entry.get(),
            expiration_date,
            dismissal_date_entry.get() or None,
            membership_combobox.get(),
            "Active",
            prefers_email_var.get(),
            prefers_sms_var.get(),
            language_combobox.get(),
            emergency_name_entry.get(),
            emergency_phone_entry.get()
        ))

        conn.commit()

        # Audit log
        logged_in_user = get_current_user()
        log_audit(member_id, logged_in_user, "Added new member")

        # Send welcome email if preferred
        if prefers_email_var.get():
            send_member_email(
                member_id,
                email,
                first_name,
                last_name
            )

        # UI cleanup
        messagebox.showinfo("Success",
                            f"""Member added successfully!
            Member ID: {member_id}
            {"Email sent" if prefers_email_var.get() else "No email sent"}""")

        clear_fields(
            member_id_entry, first_name_entry, last_name_entry, sex_combobox, email_entry, phone_entry,
            address_entry, postcode_entry, town_entry, province_entry, cf_entry, dob_entry, pob_entry,
            join_date_entry, dismissal_date_entry, membership_combobox, prefers_email_var, prefers_sms_var,
            language_combobox, emergency_name_entry, emergency_phone_entry
        )
        refresh_member_table()

    except sqlite3.Error as e:
        messagebox.showerror("Database Error", f"Failed to add member: {str(e)}")

    except ValueError as e:
        messagebox.showerror("Date Error", f"Invalid date format: {str(e)}")

    except Exception as e:
        messagebox.showerror("Error", f"Unexpected error: {str(e)}")

    finally:
        if 'conn' in locals():
            conn.close()

        sync_db_if_needed_with_conflict_resolution(last_sync_label)
        refresh_member_table()


def update_member_ui(parent_window):
    # Create Toplevel window
    update_window = tk.Toplevel(parent_window)
    update_window.title("Update Member Information")
    update_window.geometry("600x800")

    # Create a main frame to hold the canvas and buttons
    main_frame = ttk.Frame(update_window)
    main_frame.pack(fill=tk.BOTH, expand=True)

    # Create a canvas and vertical scrollbar
    canvas = tk.Canvas(main_frame)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    v_scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=canvas.yview)
    v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # Configure the canvas
    canvas.configure(yscrollcommand=v_scrollbar.set)
    canvas.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    # Create a frame inside the canvas to hold the form content
    form_frame = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=form_frame, anchor="nw")

    # Ask for Member ID
    member_id = simpledialog.askstring("Member ID", "Enter Member ID to update:", parent=update_window)
    if not member_id:
        update_window.destroy()
        return

    # Fetch current member data
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
    cursor.execute("SELECT * FROM members WHERE MemberID = ?", (member_id,))
    member_data = cursor.fetchone()
    conn.close()

    if not member_data:
        messagebox.showerror("Error", "Member not found!", parent=update_window)
        update_window.destroy()
        return

    # Create form entries with current values
    entries = {}
    fields = [
        ('First Name', 0, member_data[1]),
        ('Last Name', 1, member_data[2]),
        ('Sex', 2, member_data[3], ['Male', 'Female']),
        ('Email', 3, member_data[4]),
        ('Phone', 4, member_data[5]),
        ('Address', 5, member_data[6]),
        ('Postcode', 6, member_data[7]),
        ('Town', 7, member_data[8]),
        ('Province', 8, member_data[9]),
        ('Codice Fiscale', 9, member_data[10]),
        ('Date of Birth (DD-MM-YYYY)', 10, member_data[11]),
        ('Place of Birth', 11, member_data[12]),
        ('Join Date (DD-MM-YYYY)', 12, member_data[13]),
        ('Expiration Date (DD-MM-YYYY)', 13, member_data[14], True),  # Read-only field
        ('Dismissal Date (DD-MM-YYYY)', 14, member_data[15] or ''),
        ('Membership Type', 15, member_data[16], ['Ordinary', 'Premium', 'Lifetime', 'Honorary']),
        ('Preferred Language', 16, member_data[20], ['English', 'Twi', 'Ga', 'Ewe', 'Hausa']),
        ('Emergency Contact Name', 17, member_data[21]),
        ('Emergency Contact Phone', 18, member_data[22]),
    ]

    for row, (label, idx, default, *options) in enumerate(fields):
        ttk.Label(form_frame, text=label).grid(row=row, column=0, padx=5, pady=5, sticky="w")
        if options:
            if options[0] is True:  # Read-only field
                entries[idx] = ttk.Entry(form_frame, width=35, state="readonly")
                entries[idx].insert(0, default)
            else:  # Combobox
                entries[idx] = ttk.Combobox(form_frame, values=options[0], width=30)
                entries[idx].set(default)
        else:  # Entry field
            entries[idx] = ttk.Entry(form_frame, width=35)
            entries[idx].insert(0, default)
        entries[idx].grid(row=row, column=1, padx=5, pady=5)

    # Communication preferences
    comm_frame = ttk.Frame(form_frame)
    comm_frame.grid(row=19, column=0, columnspan=2, pady=10)
    prefers_email = tk.BooleanVar(value=member_data[18])
    prefers_sms = tk.BooleanVar(value=member_data[19])

    ttk.Checkbutton(comm_frame, text="Prefers Email Updates", variable=prefers_email).pack(side=tk.LEFT, padx=5)
    ttk.Checkbutton(comm_frame, text="Prefers SMS Updates", variable=prefers_sms).pack(side=tk.LEFT, padx=5)

    # Function to validate and update member information
    def validate_and_update():
        # Required fields validation
        required_fields = {
            0: "First Name",
            1: "Last Name",
            4: "Phone",
            10: "Date of Birth",
            12: "Join Date"
        }
        errors = []
        for idx, field in required_fields.items():
            if not entries[idx].get().strip():
                errors.append(f"{field} is required")

        # Validate email format
        email = entries[3].get()
        if email and not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            errors.append("Invalid email format")

        # Validate date formats
        date_fields = {
            10: "Date of Birth",
            12: "Join Date",
            14: "Dismissal Date"
        }
        for idx, field in date_fields.items():
            value = entries[idx].get()
            if value:
                try:
                    datetime.strptime(value, "%d-%m-%Y")
                except ValueError:
                    errors.append(f"Invalid {field} format (DD-MM-YYYY)")

        if errors:
            messagebox.showerror("Validation Errors", "\n".join(errors), parent=update_window)
            return

        # Prepare update data (exclude read-only expiration date)
        update_data = (
            entries[0].get(),  # FirstName
            entries[1].get(),  # LastName
            entries[2].get(),  # Sex
            entries[3].get(),  # Email
            entries[4].get(),  # Phone
            entries[5].get(),  # Address
            entries[6].get(),  # Postcode
            entries[7].get(),  # Town
            entries[8].get(),  # Province
            entries[9].get(),  # CodiceFiscale
            entries[10].get(),  # DateOfBirth
            entries[11].get(),  # PlaceOfBirth
            entries[12].get(),  # JoinDate
            entries[14].get() or None,  # DismissalDate
            entries[15].get(),  # MembershipType
            prefers_email.get(),  # PrefersEmail
            prefers_sms.get(),  # PrefersSMS
            entries[16].get(),  # PreferredLanguage
            entries[17].get(),  # EmergencyContactName
            entries[18].get(),  # EmergencyContactPhone
            member_id  # WHERE clause
        )

        # Execute update
        try:
            with sqlite3.connect(DATABASE_PATH) as conn:
                cursor = conn.cursor()
            cursor.execute("""UPDATE members SET
                FirstName=?, LastName=?, Sex=?, Email=?, Phone=?,
                Address=?, Postcode=?, Town=?, Province=?, CodiceFiscale=?,
                DateOfBirth=?, PlaceOfBirth=?, JoinDate=?, DismissalDate=?,
                MembershipType=?, PrefersEmail=?, PrefersSMS=?,
                PreferredLanguage=?, EmergencyContactName=?, EmergencyContactPhone=?
                WHERE MemberID=?""", update_data)
            conn.commit()
            messagebox.showinfo("Success", "Member updated successfully!", parent=update_window)
            update_window.destroy()
            # Refresh parent if available
            if hasattr(parent_window, 'refresh_member_table'):
                parent_window.refresh_member_table()
        except sqlite3.Error as e:
            messagebox.showerror("Database Error", f"Update failed: {str(e)}", parent=update_window)
        finally:
            conn.close()
        sync_db_if_needed_with_conflict_resolution(last_sync_label)

    # Function to renew membership
    def renew_membership():
        try:
            # Calculate new expiration date as 2 years from the current date
            current_date = datetime.now()
            new_expiration = current_date + timedelta(days=730)  # 2 years = 730 days
            new_expiration_str = new_expiration.strftime("%d-%m-%Y")  # Format as DD-MM-YYYY

            # Update UI and database
            entries[13].config(state="normal")
            entries[13].delete(0, tk.END)
            entries[13].insert(0, new_expiration_str)
            entries[13].config(state="readonly")

            # Update the database
            with sqlite3.connect(DATABASE_PATH) as conn:
                cursor = conn.cursor()
            cursor.execute("UPDATE members SET ExpirationDate=? WHERE MemberID=?",
                           (new_expiration_str, member_id))
            conn.commit()
            messagebox.showinfo("Success", "Membership renewed for 2 years!", parent=update_window)

        except Exception as e:
            messagebox.showerror("Error", f"Renewal failed: {str(e)}", parent=update_window)
        finally:
            conn.close() if 'conn' in locals() else None

    # Action buttons
    button_frame = ttk.Frame(update_window)
    button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=10)

    ttk.Button(
        button_frame,
        text="Confirm Changes",
        command=validate_and_update
    ).pack(side=tk.LEFT, padx=10)

    ttk.Button(
        button_frame,
        text="Renew Membership",
        command=renew_membership
    ).pack(side=tk.LEFT, padx=10)

    ttk.Button(
        button_frame,
        text="Cancel",
        command=update_window.destroy
    ).pack(side=tk.RIGHT, padx=10)

    # Make window modal
    update_window.grab_set()
    update_window.transient(parent_window)
    update_window.wait_window()


def search_member_ui():
    # Create a new Toplevel window
    search_window = tk.Toplevel()
    search_window.title("Search Member")
    search_window.geometry("1000x600")

    # Configure grid weights for resizing
    search_window.grid_rowconfigure(1, weight=1)
    search_window.grid_columnconfigure(0, weight=1)

    # Variable to hold the selected search type
    search_type = tk.StringVar(value="member_id")

    # Radio buttons for search type
    ttk.Radiobutton(search_window, text="Search by Member ID", variable=search_type, value="member_id").grid(row=1,
                                                                                                             column=0,
                                                                                                             padx=10,
                                                                                                             pady=5,
                                                                                                             sticky="w")
    ttk.Radiobutton(search_window, text="Search by Name And Date of Birth", variable=search_type,
                    value="name_dob").grid(row=1, column=1, padx=10, pady=5, sticky="w")
    ttk.Radiobutton(search_window, text="Search by Email", variable=search_type, value="email").grid(row=1, column=2,
                                                                                                     padx=10, pady=5,
                                                                                                     sticky="w")

    # Entry fields for Member ID search
    ttk.Label(search_window, text="Member ID:").grid(row=3, column=0, padx=10, pady=10, sticky="w")
    search_entry = ttk.Entry(search_window, width=30)
    search_entry.grid(row=3, column=1, padx=10, pady=10)

    # Entry fields for First Name, Last Name, and Date of Birth search
    ttk.Label(search_window, text="First Name:").grid(row=4, column=0, padx=10, pady=10, sticky="w")
    first_name_search_entry = ttk.Entry(search_window, width=30)
    first_name_search_entry.grid(row=4, column=1, padx=10, pady=10)

    ttk.Label(search_window, text="Last Name:").grid(row=4, column=2, padx=10, pady=10, sticky="w")
    last_name_search_entry = ttk.Entry(search_window, width=30)
    last_name_search_entry.grid(row=4, column=3, padx=10, pady=10)

    ttk.Label(search_window, text="Date of Birth (DD-MM-YYYY):").grid(row=5, column=0, padx=10, pady=10, sticky="w")
    dob_search_entry = ttk.Entry(search_window, width=30)
    dob_search_entry.grid(row=5, column=1, padx=10, pady=10)

    # Entry field for Email search
    ttk.Label(search_window, text="Email:").grid(row=6, column=0, padx=10, pady=10, sticky="w")
    email_search_entry = ttk.Entry(search_window, width=30)
    email_search_entry.grid(row=6, column=1, padx=10, pady=10)

    # Function to perform the search
    def perform_search():
        selected_type = search_type.get()

        if selected_type == "member_id":
            member_id = search_entry.get().strip()
            if not member_id:
                messagebox.showerror("Error", "Member ID is required!")
                return

            query = "SELECT * FROM members WHERE MemberID = ?"
            params = (member_id,)
        elif selected_type == "name_dob":
            first_name = first_name_search_entry.get().strip()
            last_name = last_name_search_entry.get().strip()
            dob = dob_search_entry.get().strip()

            if not first_name or not last_name or not dob:
                messagebox.showerror("Error", "First Name, Last Name, and Date of Birth are required!")
                return

            if not is_valid_date(dob):
                return

            query = "SELECT * FROM members WHERE FirstName = ? AND LastName = ? AND DateOfBirth = ?"
            params = (first_name, last_name, dob)
        elif selected_type == "email":
            email = email_search_entry.get().strip()
            if not email:
                messagebox.showerror("Error", "Email is required!")
                return

            if not is_valid_email(email):
                messagebox.showerror("Error", "Invalid email format!")
                return

            query = "SELECT * FROM members WHERE Email = ?"
            params = (email,)
        else:
            messagebox.showerror("Error", "Invalid search type!")
            return

        # Clear previous search results
        for widget in results_frame.winfo_children():
            widget.destroy()

        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()

        # Retrieve member details
        cursor.execute(query, params)
        members = cursor.fetchall()
        conn.close()

        if not members:
            messagebox.showerror("Error", "No members found!")
            return

        # Display member details
        for idx, member in enumerate(members):
            member_details = "\n".join([f"{col}: {val}" for col, val in zip(
                ["MemberID", "FirstName", "LastName", "Sex", "Email", "Phone", "Address", "Postcode",
                 "Town", "Province", "CodiceFiscale", "DateOfBirth", "PlaceOfBirth", "JoinDate",
                 "ExpirationDate", "DismissalDate", "MembershipType", "Status", "PrefersEmail", "PrefersSMS",
                 "PreferredLanguage",
                 "EmergencyContactName", "EmergencyContactPhone"], member)])
            ttk.Label(results_frame, text=member_details).grid(row=idx, column=0, columnspan=2, pady=10)

        # Retrieve family relations for the first member found
        if members:
            member_id = members[0][0]
            with sqlite3.connect(DATABASE_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 
                        m1.MemberID AS MemberID1, 
                        m1.FirstName AS FirstName1, 
                        m1.LastName AS LastName1, 
                        m2.MemberID AS MemberID2, 
                        m2.FirstName AS FirstName2, 
                        m2.LastName AS LastName2, 
                        fm.Relationship
                    FROM family_members fm
                    JOIN members m1 ON fm.MemberID1 = m1.MemberID
                    JOIN members m2 ON fm.MemberID2 = m2.MemberID
                    WHERE fm.MemberID1 = ? OR fm.MemberID2 = ?
                """, (member_id, member_id))
                family_relations = cursor.fetchall()

                # Retrieve payments for the first member found
                cursor.execute("SELECT Amount, PaymentDate, PaymentMethod FROM payments WHERE MemberID = ?",
                               (member_id,))
                payments = cursor.fetchall()

            # Display family relations in a table
            if family_relations:
                ttk.Label(results_frame, text="Family Relations:").grid(row=len(members), column=0, columnspan=2,
                                                                        pady=10)
                family_tree = ttk.Treeview(results_frame,
                                           columns=("MemberID1", "Name1", "MemberID2", "Name2", "Relationship"),
                                           show="headings")
                family_tree.heading("MemberID1", text="Member 1 ID")
                family_tree.heading("Name1", text="Member 1 Name")
                family_tree.heading("MemberID2", text="Member 2 ID")
                family_tree.heading("Name2", text="Member 2 Name")
                family_tree.heading("Relationship", text="Relationship")
                family_tree.grid(row=len(members) + 1, column=0, columnspan=2, padx=10, pady=10)

                for relation in family_relations:
                    # Format the values from the SQL query results
                    member1_name = f"{relation[1]} {relation[2]}"
                    member2_name = f"{relation[4]} {relation[5]}"
                    values = (relation[0], member1_name, relation[3], member2_name, relation[6])
                    family_tree.insert("", tk.END, values=values)

            # Display payments in a table
            if payments:
                ttk.Label(results_frame, text="Payments:").grid(row=len(members) + 2 + (1 if family_relations else 0),
                                                                column=0, columnspan=2, pady=10)
                payments_tree = ttk.Treeview(results_frame, columns=("Amount", "Payment Date", "Payment Method"),
                                             show="headings")
                payments_tree.heading("Amount", text="Amount")
                payments_tree.heading("Payment Date", text="Payment Date")
                payments_tree.heading("Payment Method", text="Payment Method")
                payments_tree.grid(row=len(members) + 3 + (1 if family_relations else 0), column=0, columnspan=2,
                                   padx=10, pady=10)

                for payment in payments:
                    payments_tree.insert("", tk.END, values=payment)

    # Function to print the search results
    def print_results():
        try:
            # Capture the content of the results_frame
            content = ""
            for widget in results_frame.winfo_children():
                if isinstance(widget, ttk.Label):
                    content += widget.cget("text") + "\n"
                elif isinstance(widget, ttk.Treeview):
                    content += "\n".join(
                        ["\t".join(map(str, widget.item(item)["values"])) for item in widget.get_children()]) + "\n"

            # Save the content to a temporary file
            with open("search_results.txt", "w") as file:
                file.write(content)

            # Open the file with the default application
            if os.name == 'nt':  # Windows
                os.startfile("search_results.txt", "print")
            else:  # macOS or Linux
                try:
                    subprocess.run(["open", "search_results.txt"])  # macOS
                except FileNotFoundError:
                    subprocess.run(["xdg-open", "search_results.txt"])  # Linux

            messagebox.showinfo("Success", "Search results sent to printer!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to print results: {e}")

    # Create a canvas and scrollbar for the results
    canvas = tk.Canvas(search_window)
    canvas.grid(row=13, column=0, sticky="nsew")

    # Vertical scrollbar
    v_scrollbar = ttk.Scrollbar(search_window, orient="vertical", command=canvas.yview)
    v_scrollbar.grid(row=13, column=1, sticky="ns")

    # Horizontal scrollbar
    h_scrollbar = ttk.Scrollbar(search_window, orient="horizontal", command=canvas.xview)
    h_scrollbar.grid(row=14, column=0, sticky="ew")

    # Configure the canvas
    canvas.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)

    # Create a frame inside the canvas to hold the search results
    results_frame = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=results_frame, anchor="nw")

    # Function to update the canvas scroll region
    def update_scroll_region(event):
        canvas.configure(scrollregion=canvas.bbox("all"))

    # Bind the update_scroll_region function to the results_frame
    results_frame.bind("<Configure>", update_scroll_region)

    # Add the "Search" button
    ttk.Button(search_window, text="Search", command=perform_search).grid(row=15, column=0, padx=10, pady=10)

    # Add the "Print Results" button
    ttk.Button(search_window, text="Print Results", command=print_results).grid(row=15, column=1, padx=10, pady=10)


def record_payment_ui():
    # Create a new Toplevel window
    payment_window = tk.Toplevel()
    payment_window.title("Record Payment")
    payment_window.geometry("400x350")

    # Create and place labels and entry fields
    ttk.Label(payment_window, text="Member ID:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
    member_id_entry = ttk.Entry(payment_window, width=30)
    member_id_entry.grid(row=0, column=1, padx=10, pady=10)

    ttk.Label(payment_window, text="Amount:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
    amount_entry = ttk.Entry(payment_window, width=30)
    amount_entry.grid(row=1, column=1, padx=10, pady=10)

    ttk.Label(payment_window, text="Payment Method:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
    payment_method_combobox = ttk.Combobox(payment_window,
                                           values=["Cash", "Credit Card", "Bank Transfer", "Mobile Money"],
                                           state="readonly", width=27)
    payment_method_combobox.grid(row=2, column=1, padx=10, pady=10)
    payment_method_combobox.set("Cash")  # Default value

    def on_record_payment():
        member_id = member_id_entry.get().strip()
        amount = amount_entry.get().strip()
        method = payment_method_combobox.get().strip()

        if not member_id or not amount or not method:
            messagebox.showerror("Error", "All fields are required!")
            return

        try:
            # Call the record_payment function
            record_payment(member_id, amount, method)
            messagebox.showinfo("Success", "Payment recorded successfully!")
            payment_window.destroy()  # Close the window after recording
        except Exception as e:
            messagebox.showerror("Error", f"Failed to record payment: {e}")

    # Add the "Record Payment" button
    ttk.Button(payment_window, text="Record Payment", command=on_record_payment).grid(row=3, column=0, columnspan=2,
                                                                                      pady=20)


def manage_users_ui(parent_window):
    # Create a new Toplevel window using the parent_window as its parent
    user_window = tk.Toplevel(parent_window)
    user_window.title("User Management")
    user_window.geometry("600x400")

    # Create a frame for the user list
    user_list_frame = ttk.Frame(user_window, padding=10)
    user_list_frame.grid(row=0, column=0, sticky="nsew")

    # Create a Treeview to display users
    user_tree = ttk.Treeview(user_list_frame, columns=("Username", "Role"), show="headings")
    user_tree.heading("Username", text="Username")
    user_tree.heading("Role", text="Role")
    user_tree.grid(row=0, column=0, sticky="nsew")

    # Add a scrollbar for the Treeview
    scrollbar = ttk.Scrollbar(user_list_frame, orient="vertical", command=user_tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    user_tree.configure(yscrollcommand=scrollbar.set)

    # Function to populate the user list
    def populate_user_list():
        # Clear existing entries
        for item in user_tree.get_children():
            user_tree.delete(item)

        # Fetch users from the database
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
        cursor.execute("SELECT Username, Role FROM users")
        users = cursor.fetchall()
        conn.close()

        # Insert users into the Treeview
        for user in users:
            user_tree.insert("", tk.END, values=user)

    # Populate the user list when the window opens
    populate_user_list()

    # Create a frame for user management buttons
    button_frame = ttk.Frame(user_window, padding=10)
    button_frame.grid(row=1, column=0, sticky="ew")

    # Function to add a new user
    def on_add_user():
        # Create a new Toplevel window for adding a user
        add_user_window = tk.Toplevel(user_window)
        add_user_window.title("Add User")
        add_user_window.geometry("400x400")

        # Create and place labels and entry fields
        ttk.Label(add_user_window, text="Username:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        username_entry = ttk.Entry(add_user_window, width=25)
        username_entry.grid(row=0, column=1, padx=10, pady=10)

        ttk.Label(add_user_window, text="Password:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        password_entry = ttk.Entry(add_user_window, show="*", width=25)
        password_entry.grid(row=1, column=1, padx=10, pady=10)

        ttk.Label(add_user_window, text="Role:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
        role_combobox = ttk.Combobox(add_user_window, values=["Admin", "Editor"], state="readonly", width=22)
        role_combobox.grid(row=2, column=1, padx=10, pady=10)
        role_combobox.set("Editor")  # Default value

        # Function to handle the "Add User" button click
        def add_user():
            username = username_entry.get().strip()
            password = password_entry.get().strip()
            role = role_combobox.get().strip()

            if not username or not password or not role:
                messagebox.showerror("Error", "All fields are required!")
                return

            try:
                # Hash the password before storing it
                hashed_password = hash_password(password)

                with sqlite3.connect(DATABASE_PATH) as conn:
                    cursor = conn.cursor()
                cursor.execute("INSERT INTO users (Username, PasswordHash, Role) VALUES (?, ?, ?)",
                               (username, hashed_password, role))
                conn.commit()
                conn.close()
                messagebox.showinfo("Success", "User added successfully!")
                add_user_window.destroy()  # Close the window after adding
                populate_user_list()  # Refresh the user list
            except sqlite3.IntegrityError:
                messagebox.showerror("Error", "Username already exists!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to add user: {e}")

        # Add the "Add User" button
        ttk.Button(add_user_window, text="Add User", command=add_user).grid(row=3, column=0, columnspan=2, pady=20)

    # Function to delete a user
    def on_delete_user():
        selected_item = user_tree.selection()
        if not selected_item:
            messagebox.showerror("Error", "Please select a user to delete!")
            return

        # Get the username of the selected user
        username = user_tree.item(selected_item, "values")[0]

        # Confirm deletion
        confirm = messagebox.askyesno("Confirm Deletion", f"Are you sure you want to delete user '{username}'?")
        if not confirm:
            return

        try:
            with sqlite3.connect(DATABASE_PATH) as conn:
                cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE Username = ?", (username,))
            conn.commit()
            conn.close()
            messagebox.showinfo("Success", f"User '{username}' deleted successfully!")
            populate_user_list()  # Refresh the user list
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete user: {e}")

    # Function to modify a user
    def on_modify_user():
        selected_item = user_tree.selection()
        if not selected_item:
            messagebox.showerror("Error", "Please select a user to modify!")
            return

        # Get the username and role of the selected user
        username, role = user_tree.item(selected_item, "values")

        # Create a new Toplevel window for modifying the user
        modify_user_window = tk.Toplevel(user_window)
        modify_user_window.title("Modify User")
        modify_user_window.geometry("400x400")

        # Create and place labels and entry fields
        ttk.Label(modify_user_window, text="Username:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        username_entry = ttk.Entry(modify_user_window, width=25)
        username_entry.insert(0, username)
        username_entry.config(state="readonly")  # Username cannot be changed
        username_entry.grid(row=0, column=1, padx=10, pady=10)

        ttk.Label(modify_user_window, text="New Password:").grid(row=1, column=0, padx=10, pady=10, sticky="w")
        password_entry = ttk.Entry(modify_user_window, show="*", width=25)
        password_entry.grid(row=1, column=1, padx=10, pady=10)

        ttk.Label(modify_user_window, text="Role:").grid(row=2, column=0, padx=10, pady=10, sticky="w")
        role_combobox = ttk.Combobox(modify_user_window, values=["Admin", "Editor"], state="readonly", width=22)
        role_combobox.set(role)  # Set current role
        role_combobox.grid(row=2, column=1, padx=10, pady=10)

        # Function to handle the "Modify User" button click
        def modify_user():
            new_password = password_entry.get().strip()
            new_role = role_combobox.get().strip()

            if not new_password and not new_role:
                messagebox.showerror("Error", "Please enter a new password or select a new role!")
                return

            try:
                with sqlite3.connect(DATABASE_PATH) as conn:
                    cursor = conn.cursor()

                # Update password if provided
                if new_password:
                    hashed_password = hash_password(new_password)
                    cursor.execute("UPDATE users SET PasswordHash = ? WHERE Username = ?",
                                   (hashed_password, username))

                # Update role if changed
                if new_role != role:
                    cursor.execute("UPDATE users SET Role = ? WHERE Username = ?", (new_role, username))

                conn.commit()
                conn.close()
                messagebox.showinfo("Success", "User updated successfully!")
                modify_user_window.destroy()  # Close the window after updating
                populate_user_list()  # Refresh the user list
            except Exception as e:
                messagebox.showerror("Error", f"Failed to update user: {e}")

        # Add the "Modify User" button
        ttk.Button(modify_user_window, text="Modify User", command=modify_user).grid(row=3, column=0, columnspan=2,
                                                                                     pady=20)

    # Add buttons for user management
    ttk.Button(button_frame, text="Add User", command=on_add_user).grid(row=0, column=0, padx=5, pady=5)
    ttk.Button(button_frame, text="Delete User", command=on_delete_user).grid(row=0, column=1, padx=5, pady=5)
    ttk.Button(button_frame, text="Modify User", command=on_modify_user).grid(row=0, column=2, padx=5, pady=5)

    # Make window modal
    user_window.grab_set()
    # Correct the transient setting
    user_window.transient(parent_window)  # Ensure the transient master is the parent window
    user_window.wait_window()


###


if __name__ == "__main__":
    # initialize_db()
    # sync_db_if_needed_with_conflict_resolution()
    main()
