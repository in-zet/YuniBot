import ast
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter
from chuni_penguin.adapters.chunithm_net.parser import parse_player_data

FIXTURE = Path("docs/reference/chunithm-net/player_data.html")
ADAPTER_SOURCE = Path("third_party/chuni_penguin/adapters/chunithm_net/adapter.py")


def test_parses_saved_player_data_page():
    html = FIXTURE.read_text(encoding="utf-8")
    profile = parse_player_data(LexborHTMLParser(html))

    assert profile.username
    assert profile.level is not None
    assert profile.level > 0


def test_adapter_constructs_without_a_database():
    adapter = ChunithmNetAdapter(
        database=None,
        discord_id=0,
        lwp_cookie_jar="#LWP-Cookies-2.0\n",
    )
    assert adapter is not None


def test_patched_adapter_never_reads_self_database_outside_init():
    """Regression check for the sync_upstream.py patch: if a future upstream
    sync reintroduces a `self.database` read outside the constructor, this
    fails loudly instead of only blowing up later at runtime with database=None.
    """
    tree = ast.parse(ADAPTER_SOURCE.read_text(encoding="utf-8"))
    (cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef)]

    offending: list[str] = []

    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "database"
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            offending.append(f"line {node.lineno}")

    assert offending == [], f"self.database read outside __init__: {offending}"
