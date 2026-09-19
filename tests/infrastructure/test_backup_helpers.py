import pytest
import os
import subprocess
from unittest.mock import patch, MagicMock, mock_open
from src.infrastructure.backup_helpers import DatabaseBackupHelper


class TestDatabaseBackupHelper:
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_init(self, mock_makedirs):
        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url, "/custom/backups")

        assert helper.database_url == db_url
        assert helper.backup_dir == "/custom/backups"
        mock_makedirs.assert_called_once_with("/custom/backups", exist_ok=True)

    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_init_default_dir(self, mock_makedirs):
        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)

        assert helper.database_url == db_url
        assert helper.backup_dir == "/tmp/backups"
        mock_makedirs.assert_called_once_with("/tmp/backups", exist_ok=True)

    @patch('src.infrastructure.backup_helpers.datetime')
    @patch('src.infrastructure.backup_helpers.subprocess.Popen')
    @patch('builtins.open', new_callable=mock_open)
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_backup_compressed(self, mock_makedirs, mock_file, mock_popen, mock_datetime):
        # Setup mocks
        mock_datetime.datetime.now.return_value.strftime.return_value = "20231026_120000"

        mock_p1 = MagicMock()
        mock_p2 = MagicMock()
        mock_p2.returncode = 0
        mock_popen.side_effect = [mock_p1, mock_p2]

        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)
        filepath = helper.backup(compress=True)

        expected_filepath = os.path.join("/tmp/backups", "db_backup_20231026_120000.sql.gz")
        assert filepath == expected_filepath

        mock_file.assert_called_once_with(expected_filepath, "wb")

        # Verify Popen calls
        assert mock_popen.call_count == 2
        mock_popen.assert_any_call(["pg_dump", "-d", db_url, "-F", "p"], stdout=subprocess.PIPE)
        mock_popen.assert_any_call(["gzip"], stdin=mock_p1.stdout, stdout=mock_file())

        mock_p1.stdout.close.assert_called_once()
        mock_p2.communicate.assert_called_once()

    @patch('src.infrastructure.backup_helpers.datetime')
    @patch('src.infrastructure.backup_helpers.subprocess.run')
    @patch('builtins.open', new_callable=mock_open)
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_backup_uncompressed(self, mock_makedirs, mock_file, mock_run, mock_datetime):
        # Setup mocks
        mock_datetime.datetime.now.return_value.strftime.return_value = "20231026_120000"

        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)
        filepath = helper.backup(compress=False)

        expected_filepath = os.path.join("/tmp/backups", "db_backup_20231026_120000.sql")
        assert filepath == expected_filepath

        mock_file.assert_called_once_with(expected_filepath, "w")

        mock_run.assert_called_once_with(
            ["pg_dump", "-d", db_url, "-F", "p"],
            stdout=mock_file(),
            check=True
        )

    @patch('src.infrastructure.backup_helpers.datetime')
    @patch('src.infrastructure.backup_helpers.subprocess.Popen')
    @patch('builtins.open', new_callable=mock_open)
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_backup_compressed_failure(self, mock_makedirs, mock_file, mock_popen, mock_datetime):
        mock_datetime.datetime.now.return_value.strftime.return_value = "20231026_120000"

        mock_p1 = MagicMock()
        mock_p2 = MagicMock()
        mock_p2.returncode = 1  # Simulate failure
        mock_popen.side_effect = [mock_p1, mock_p2]

        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)

        with pytest.raises(RuntimeError, match="Backup failed with code 1"):
            helper.backup(compress=True)

    @patch('src.infrastructure.backup_helpers.datetime')
    @patch('src.infrastructure.backup_helpers.subprocess.run')
    @patch('builtins.open', new_callable=mock_open)
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_backup_uncompressed_failure(self, mock_makedirs, mock_file, mock_run, mock_datetime):
        mock_datetime.datetime.now.return_value.strftime.return_value = "20231026_120000"
        mock_run.side_effect = subprocess.CalledProcessError(1, "pg_dump")

        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)

        with pytest.raises(subprocess.CalledProcessError):
            helper.backup(compress=False)

    @patch('src.infrastructure.backup_helpers.subprocess.Popen')
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_restore_compressed(self, mock_makedirs, mock_popen):
        mock_p1 = MagicMock()
        mock_p2 = MagicMock()
        mock_p2.returncode = 0
        mock_popen.side_effect = [mock_p1, mock_p2]

        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)
        filepath = "/tmp/backups/db_backup_20231026_120000.sql.gz"

        helper.restore(filepath)

        assert mock_popen.call_count == 2
        mock_popen.assert_any_call(["gunzip", "-c", filepath], stdout=subprocess.PIPE)
        mock_popen.assert_any_call(["psql", "-d", db_url], stdin=mock_p1.stdout)

        mock_p1.stdout.close.assert_called_once()
        mock_p2.communicate.assert_called_once()

    @patch('src.infrastructure.backup_helpers.subprocess.run')
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_restore_uncompressed(self, mock_makedirs, mock_run):
        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)
        filepath = "/tmp/backups/db_backup_20231026_120000.sql"

        helper.restore(filepath)

        mock_run.assert_called_once_with(["psql", "-d", db_url, "-f", filepath], check=True)

    @patch('src.infrastructure.backup_helpers.subprocess.Popen')
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_restore_compressed_failure(self, mock_makedirs, mock_popen):
        mock_p1 = MagicMock()
        mock_p2 = MagicMock()
        mock_p2.returncode = 1  # Simulate failure
        mock_popen.side_effect = [mock_p1, mock_p2]

        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)
        filepath = "/tmp/backups/db_backup_20231026_120000.sql.gz"

        with pytest.raises(RuntimeError, match="Restore failed with code 1"):
            helper.restore(filepath)

    @patch('src.infrastructure.backup_helpers.subprocess.run')
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_restore_uncompressed_failure(self, mock_makedirs, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "psql")

        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)
        filepath = "/tmp/backups/db_backup_20231026_120000.sql"

        with pytest.raises(subprocess.CalledProcessError):
            helper.restore(filepath)

    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_init_makedirs_failure(self, mock_makedirs):
        mock_makedirs.side_effect = PermissionError("Permission denied")
        db_url = "postgresql://user:pass@localhost/db"

        with pytest.raises(PermissionError):
            DatabaseBackupHelper(db_url)

    @patch('src.infrastructure.backup_helpers.datetime')
    @patch('src.infrastructure.backup_helpers.subprocess.Popen')
    @patch('builtins.open')
    @patch('src.infrastructure.backup_helpers.os.makedirs')
    def test_backup_file_open_failure(self, mock_makedirs, mock_file, mock_popen, mock_datetime):
        mock_datetime.datetime.now.return_value.strftime.return_value = "20231026_120000"
        mock_file.side_effect = IOError("Cannot open file")

        db_url = "postgresql://user:pass@localhost/db"
        helper = DatabaseBackupHelper(db_url)

        with pytest.raises(IOError, match="Cannot open file"):
            helper.backup(compress=True)

        # Verify subprocess wasn't called since file open failed
        mock_popen.assert_not_called()
