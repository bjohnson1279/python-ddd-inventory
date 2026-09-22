import os
import subprocess
import datetime
import logging

logger = logging.getLogger(__name__)

class DatabaseBackupHelper:
    """Helper to perform automated PostgreSQL backups and restores."""

    def __init__(self, database_url: str, backup_dir: str = "/tmp/backups"):
        self.database_url = database_url
        self.backup_dir = backup_dir
        os.makedirs(self.backup_dir, exist_ok=True)

    def backup(self, compress: bool = True) -> str:
        """Create a full database snapshot."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"db_backup_{timestamp}.sql"
        if compress:
            filename += ".gz"
            
        filepath = os.path.join(self.backup_dir, filename)
        
        logger.info(f"Starting database backup to {filepath}...")
        
        # In a real system, we might parse the database_url to pass to pg_dump
        # For simplicity, we assume pg_dump is configured with environment variables
        # or we pass the connection string directly.
        cmd = ["pg_dump", "-d", self.database_url, "-F", "p"]
        
        try:
            with open(filepath, "wb" if compress else "w") as out:
                if compress:
                    import gzip
                    # pipe pg_dump to gzip
                    p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE)
                    p2 = subprocess.Popen(["gzip"], stdin=p1.stdout, stdout=out)
                    p1.stdout.close()  # Allow p1 to receive a SIGPIPE if p2 exits
                    p2.communicate()
                    if p2.returncode != 0:
                        raise RuntimeError(f"Backup failed with code {p2.returncode}")
                else:
                    subprocess.run(cmd, stdout=out, check=True)
            logger.info("Database backup completed successfully.")
            return filepath
        except Exception as e:
            logger.error(f"Failed to create database backup: {e}")
            raise

    def restore(self, filepath: str):
        """Restore database from a snapshot."""
        # Secure the filepath against path traversal attacks
        abs_backup_dir = os.path.abspath(self.backup_dir)
        target_path = os.path.join(self.backup_dir, filepath) if not os.path.isabs(filepath) else filepath
        abs_target_path = os.path.abspath(target_path)
        try:
            if os.path.commonpath([abs_backup_dir, abs_target_path]) != abs_backup_dir:
                raise ValueError("Path traversal detected")
        except ValueError:
            raise ValueError("Path traversal detected")

        logger.info(f"Starting database restore from {abs_target_path}...")
        cmd = ["psql", "-d", self.database_url, "-f", abs_target_path]
        
        try:
            if filepath.endswith(".gz"):
                # pipe gunzip to psql
                p1 = subprocess.Popen(["gunzip", "-c", abs_target_path], stdout=subprocess.PIPE)
                p2 = subprocess.Popen(["psql", "-d", self.database_url], stdin=p1.stdout)
                p1.stdout.close()
                p2.communicate()
                if p2.returncode != 0:
                    raise RuntimeError(f"Restore failed with code {p2.returncode}")
            else:
                subprocess.run(cmd, check=True)
            logger.info("Database restore completed successfully.")
        except Exception as e:
            logger.error(f"Failed to restore database: {e}")
            raise
