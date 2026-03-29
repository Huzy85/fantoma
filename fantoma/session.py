"""Session persistence — saves browser state (cookies + localStorage) to encrypted files."""
import json
import logging
import os
import tempfile
import time

log = logging.getLogger("fantoma.session")

try:
    from cryptography.fernet import Fernet
    _has_cryptography = True
except ImportError:
    _has_cryptography = False

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "fantoma", "sessions")


class SessionManager:
    """Persist browser sessions per domain + account. Encrypted at rest."""

    def __init__(self, base_dir: str = DEFAULT_DIR):
        self._dir = base_dir
        self._fernet = None

    def _ensure_dir(self):
        os.makedirs(self._dir, exist_ok=True)

    def _key_path(self) -> str:
        return os.path.join(self._dir, ".key")

    def _get_fernet(self):
        """Load or create encryption key. Returns Fernet instance or None."""
        if not _has_cryptography:
            return None
        if self._fernet:
            return self._fernet
        self._ensure_dir()
        key_path = self._key_path()
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
            # Ensure permissions even if file already existed
            os.chmod(key_path, 0o600)
        self._fernet = Fernet(key)
        return self._fernet

    def _filename(self, domain: str, account: str) -> str:
        safe_domain = domain.replace("/", "_").replace(":", "_")
        safe_account = account.replace("/", "_").replace(":", "_")
        ext = ".enc" if _has_cryptography else ".json"
        return f"{safe_domain}--{safe_account}{ext}"

    def _filepath(self, domain: str, account: str) -> str:
        return os.path.join(self._dir, self._filename(domain, account))

    def save(self, domain: str, account: str, storage_state: dict, login_url: str):
        """Save browser state to disk. Atomic write (temp file → rename)."""
        self._ensure_dir()
        data = {
            "domain": domain,
            "account": account,
            "storage_state": storage_state,
            "login_url": login_url,
            "saved_at": time.time(),
        }
        payload = json.dumps(data).encode()

        fernet = self._get_fernet()
        if fernet:
            payload = fernet.encrypt(payload)

        filepath = self._filepath(domain, account)
        tmp_path = filepath + ".tmp"
        try:
            with open(tmp_path, "wb" if fernet else "w") as f:
                if fernet:
                    f.write(payload)
                else:
                    f.write(payload.decode())
            os.replace(tmp_path, filepath)
            log.info("Session saved: %s (%s)", domain, account[:20])
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def load(self, domain: str, account: str) -> dict | None:
        """Load saved session. Returns None if missing, corrupted, or decryption fails."""
        filepath = self._filepath(domain, account)
        if not os.path.exists(filepath):
            return None
        try:
            fernet = self._get_fernet()
            if fernet:
                with open(filepath, "rb") as f:
                    encrypted = f.read()
                decrypted = fernet.decrypt(encrypted)
                return json.loads(decrypted)
            else:
                with open(filepath, "r") as f:
                    return json.loads(f.read())
        except Exception as e:
            log.warning("Failed to load session %s/%s: %s", domain, account, e)
            return None

    def delete(self, domain: str, account: str):
        """Remove a saved session."""
        filepath = self._filepath(domain, account)
        if os.path.exists(filepath):
            os.unlink(filepath)
            log.info("Session deleted: %s (%s)", domain, account[:20])

    def list(self, domain: str = None) -> list[dict]:
        """List saved sessions. Filter by domain if provided."""
        if not os.path.exists(self._dir):
            return []
        results = []
        for fname in os.listdir(self._dir):
            if fname.startswith(".") or fname.endswith(".tmp"):
                continue
            if not (fname.endswith(".enc") or fname.endswith(".json")):
                continue
            parts = fname.rsplit(".", 1)[0].split("--", 1)
            if len(parts) != 2:
                continue
            d, a = parts
            if domain and d != domain.replace("/", "_").replace(":", "_"):
                continue
            results.append({"domain": d, "account": a, "file": fname})
        return results
