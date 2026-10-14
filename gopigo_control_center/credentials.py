"""Remembering the robot's password -- in the system's password store (GNOME Keyring or KWallet on Linux,
Windows Credential Manager, the macOS Keychain) through the keyring library, never in a file.

Every function here can be slow (the store may ask to be unlocked), so call them off the Tk thread. None of
them raise: a store that's missing or locked just means nothing is remembered.
"""

SERVICE = "GoPiGo Control Center"

try:
    import keyring
    from keyring.backends import fail as _fail_backend
except ImportError:
    keyring = None


def available():
    """Whether there's a password store to use."""
    if keyring is None:
        return False
    try:
        return not isinstance(keyring.get_keyring(), _fail_backend.Keyring)
    except Exception:
        return False


def _account(host, user):
    return f"{user}@{host}"


def load(host, user):
    """The remembered password for user@host, or None."""
    if keyring is None:
        return None
    try:
        return keyring.get_password(SERVICE, _account(host, user))
    except Exception:
        return None


def save(host, user, password):
    """Remember the password. Returns an error message, or None if it worked."""
    if keyring is None:
        return "the keyring library isn't installed"
    try:
        keyring.set_password(SERVICE, _account(host, user), password)
        return None
    except Exception as e:
        return str(e) or type(e).__name__


def forget(host, user):
    if keyring is None:
        return
    try:
        keyring.delete_password(SERVICE, _account(host, user))
    except Exception:
        pass  # nothing was stored, or the store is unavailable
