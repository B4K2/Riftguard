import json
import threading

from agentgit import store
from agentgit.paths import store_path


def _init(repo):
    store.init_store(store_path(repo))


def test_worktrees_share_one_store(repo, tmp_path):
    assert store_path(tmp_path / "wt-b") == store_path(repo)


def test_steps_form_a_parent_chain(repo, replay):
    _init(repo)
    replay("read", repo)
    replay("edit", repo)
    db = store.connect(store_path(repo))
    (head,) = db.execute("SELECT head FROM agents").fetchone()
    chain = list(store.walk(db, head))
    assert [s.tool for s in chain] == ["Edit", "Read"]
    assert chain[0].parent == chain[1].hash
    assert chain[1].parent is None
    assert [s.effect for s in chain] == ["reversible", "pure"]
    assert store.get_object(db, chain[1].input_hash) == {"file_path": "README.md"}


def test_agent_in_linked_worktree_writes_to_shared_store(repo, tmp_path, replay):
    _init(repo)
    replay("read", repo)
    replay("bash", tmp_path / "wt-b")
    db = store.connect(store_path(repo))
    agents = dict(db.execute("SELECT session_id, worktree FROM agents").fetchall())
    assert agents == {"sess-aaaa1111": str(repo), "sess-bbbb2222": str(tmp_path / "wt-b")}
    (effect,) = db.execute("SELECT effect FROM steps WHERE tool='Bash'").fetchone()
    assert effect == "irreversible"


def test_identical_content_stored_once(repo, replay):
    _init(repo)
    replay("read", repo)
    replay("read", repo)
    db = store.connect(store_path(repo))
    assert db.execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 2
    # one input blob + one output blob, shared by both steps
    assert db.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 2


def test_hook_is_silent_when_not_initialised(repo, replay, capsys):
    replay("read", repo)  # no store: must not raise or create one
    assert not store_path(repo).exists()
    assert capsys.readouterr().err == ""


def test_hook_never_raises_on_bad_input(capsys):
    import io
    from agentgit import hooks
    hooks.post_tool_use(io.StringIO("not json"))
    assert "agentgit:" in capsys.readouterr().err


def test_concurrent_writers_keep_chain_linear(repo):
    _init(repo)
    path = store_path(repo)

    def worker(i):
        db = store.connect(path)
        for j in range(20):
            store.record_step(db, "sess-x", str(repo), "Read", {"i": i, "j": j}, None)
        db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    db = store.connect(path)
    (head,) = db.execute("SELECT head FROM agents").fetchone()
    assert len(list(store.walk(db, head))) == 80  # no step lost to a head race
