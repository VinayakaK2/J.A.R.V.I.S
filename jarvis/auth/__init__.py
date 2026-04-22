# Identity subpackage — exposes role resolution helpers and auth utilities
from auth.identity import get_user_role, is_owner, OWNER

# Re-export auth utilities from the sibling auth.py module
# Python resolves "auth" to this package, so main.py's
# `from auth import get_password_hash, ...` lands here.
import importlib.util
import os as _os

_auth_file = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "auth.py")
_spec = importlib.util.spec_from_file_location("_auth_module", _auth_file)
_auth_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_auth_mod)

get_password_hash = _auth_mod.get_password_hash
verify_password = _auth_mod.verify_password
create_access_token = _auth_mod.create_access_token
create_agent_token = _auth_mod.create_agent_token
decode_access_token = _auth_mod.decode_access_token
get_current_user = _auth_mod.get_current_user

__all__ = [
    "get_user_role", "is_owner", "OWNER",
    "get_password_hash", "verify_password", "create_access_token",
    "create_agent_token", "decode_access_token", "get_current_user",
]
