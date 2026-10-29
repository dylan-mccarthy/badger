"""Random pseudonymous badge identity, independent of hardware identifiers."""
import errno
import json
import os

IDENTITY_FILE = "/state/badgecluster_identity.json"


def _read_identity(path):
    with open(path) as source:
        content = source.read(256)
    record = json.loads(content)
    node_id = record.get("nodeId") if isinstance(record, dict) else None
    if (not isinstance(record, dict)
            or type(record.get("version")) is not int or record["version"] != 1
            or not isinstance(node_id, str) or len(node_id) != 37
            or not node_id.startswith("node-")
            or any(character not in "0123456789abcdef" for character in node_id[5:])):
        raise ValueError("Invalid BadgeCluster identity; restore the saved identity file")
    return node_id


def load_node_id(random_bytes, path=IDENTITY_FILE):
    try:
        return _read_identity(path)
    except OSError as error:
        if error.args[0] != errno.ENOENT:
            raise

    temporary = path + ".tmp"
    try:
        node_id = _read_identity(temporary)
    except OSError as error:
        if error.args[0] != errno.ENOENT:
            raise
        entropy = random_bytes(16)
        if len(entropy) != 16:
            raise ValueError("Badge identity requires 16 random bytes")
        node_id = "node-" + entropy.hex()
        with open(temporary, "w") as destination:
            json.dump({"version": 1, "nodeId": node_id}, destination)
        if hasattr(os, "sync"):
            os.sync()
    os.rename(temporary, path)
    if hasattr(os, "sync"):
        os.sync()
    return node_id
