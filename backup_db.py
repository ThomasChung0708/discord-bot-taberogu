from __future__ import annotations

"""Create one timestamped SQLite backup.

This script is intended for systemd timers on the VM. It reuses the same
backup logic as the admin web app, including backup rotation.
"""

from admin_app import create_database_backup


if __name__ == "__main__":
    backup_path = create_database_backup()
    print(f"Created backup: {backup_path}")
