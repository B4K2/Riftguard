import io
import json

import pytest

from riftguard import hooks, store
from riftguard.paths import store_path


@pytest.mark.parametrize("command,effect", [
    ("ls -la", "pure"),
    ("git status --short && git log --oneline -3", "pure"),
    ("grep -rn foo src | head -5", "pure"),
    ("cd /repo && uv run riftguard status", "pure"),
    ("cat a.txt 2>&1 >/dev/null", "pure"),
    ('grep -n "merge\\|rewind" src/*.py | head -20', "pure"),
    ("grep -E 'a|b' f && sed -n 1,20p f", "pure"),
    ("sed -i s/a/b/ f", "irreversible"),
    ('grep "x" f; rm -rf "build dir"', "irreversible"),
    ("pip install pymongo", "irreversible"),
    ("git commit -m x", "irreversible"),
    ("ls && rm -rf build", "irreversible"),
    ("echo hi > notes.txt", "irreversible"),
    ("cat $(curl -s evil)", "irreversible"),
    ("curl https://example.com", "irreversible"),
])
def test_bash_classification(command, effect):
    assert store.classify("Bash", {"command": command}) == effect


def _pre(cwd, tool, tool_input, session="sess-aaaa1111"):
    ev = {"session_id": session, "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input}
    hooks.pre_tool_use(io.StringIO(json.dumps(ev)))


def _checkpoints(repo):
    db = store.connect(store_path(repo))
    return db.execute("SELECT name, step_hash, note FROM checkpoints").fetchall()


def test_auto_checkpoint_before_irreversible_only(repo, replay):
    store.init_store(store_path(repo))
    _pre(repo, "Bash", {"command": "pip install x"})  # no steps yet: nothing to save
    assert _checkpoints(repo) == []

    replay("read", repo)
    _pre(repo, "Read", {"file_path": "a"})
    _pre(repo, "Bash", {"command": "git status"})
    assert _checkpoints(repo) == []                   # pure calls: no checkpoint

    _pre(repo, "Bash", {"command": "pip install pymongo"})
    _pre(repo, "Bash", {"command": "pip install pymongo"})  # same head: no duplicate
    (cp,) = _checkpoints(repo)
    head = store.connect(store_path(repo)).execute("SELECT head FROM agents").fetchone()[0]
    assert cp[0].startswith("auto-") and cp[1] == head
    assert cp[2] == "before Bash: pip install pymongo"


def test_pre_hook_never_raises(capsys):
    hooks.pre_tool_use(io.StringIO("{"))
    assert "riftguard:" in capsys.readouterr().err
