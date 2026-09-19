"""Regression tests for big-upload/big-download resume safety.

The original code resumed from the existing destination length whenever a file
of that name existed, without checking that the bytes already there came from
the same source. Uploading a *new* file over an *unrelated older* file of the
same name therefore appended the new file's tail to the old file's body and
reported "Verified: <n> bytes" -- silent corruption.
"""
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))


def _import_file_ops():
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        import file_ops  # noqa: F401
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    return file_ops


file_ops = _import_file_ops()


class FakeRemoteFile:
    def __init__(self, data):
        self.data = bytes(data)
        self.pos = 0

    def seek(self, pos):
        self.pos = pos

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self.data[self.pos:]
            self.pos = len(self.data)
            return chunk
        chunk = self.data[self.pos:self.pos + size]
        self.pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeStat:
    def __init__(self, size):
        self.st_size = size


class FakeSftp:
    def __init__(self, remote_bytes=None):
        self.remote = None if remote_bytes is None else bytes(remote_bytes)

    def stat(self, path):
        if self.remote is None:
            raise FileNotFoundError(path)
        return FakeStat(len(self.remote))

    def open(self, path, mode="rb"):
        if self.remote is None:
            raise FileNotFoundError(path)
        return FakeRemoteFile(self.remote)


class ResumeSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sp_resume_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _local(self, data):
        p = self.tmp / "payload.bin"
        p.write_bytes(data)
        return str(p)

    def test_true_partial_transfer_is_resumed(self):
        payload = b"abcdefghij" * 100
        local = self._local(payload)
        sftp = FakeSftp(payload[:450])
        offset, note = file_ops._resume_offset(sftp, local, "/remote/payload.bin", len(payload))
        self.assertEqual(offset, 450)
        self.assertIsNone(note)

    def test_unrelated_shorter_remote_file_restarts_from_zero(self):
        """The silent-corruption case: same name, different content, shorter."""
        payload = b"NEW-CONTENT-" * 100
        local = self._local(payload)
        sftp = FakeSftp(b"OLD-DIFFERENT-BYTES" * 10)
        offset, note = file_ops._resume_offset(sftp, local, "/remote/payload.bin", len(payload))
        self.assertEqual(offset, 0)
        self.assertIn("restarting from 0", note or "")

    def test_identical_existing_file_needs_no_transfer(self):
        payload = b"same-bytes" * 50
        local = self._local(payload)
        sftp = FakeSftp(payload)
        offset, note = file_ops._resume_offset(sftp, local, "/remote/payload.bin", len(payload))
        self.assertEqual(offset, len(payload))
        self.assertIn("already matches", note or "")

    def test_longer_remote_file_restarts_from_zero(self):
        payload = b"short" * 10
        local = self._local(payload)
        sftp = FakeSftp(payload + b"extra-tail")
        offset, note = file_ops._resume_offset(sftp, local, "/remote/payload.bin", len(payload))
        self.assertEqual(offset, 0)
        self.assertIn("longer than source", note or "")

    def test_missing_destination_is_a_fresh_transfer(self):
        payload = b"fresh" * 10
        local = self._local(payload)
        sftp = FakeSftp(None)
        offset, note = file_ops._resume_offset(sftp, local, "/remote/payload.bin", len(payload))
        self.assertEqual(offset, 0)

    def test_missing_local_file_is_a_fresh_download(self):
        sftp = FakeSftp(b"remote-bytes" * 10)
        offset, note = file_ops._resume_offset(sftp, str(self.tmp / "absent.bin"), "/remote/x", 120)
        self.assertEqual(offset, 0)
        self.assertIn("no local file", note or "")

    def test_prefix_digest_matches_hashlib(self):
        payload = bytes(range(256)) * 8
        local = self._local(payload)
        self.assertEqual(
            file_ops._local_prefix_digest(local, 1000),
            hashlib.sha256(payload[:1000]).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
