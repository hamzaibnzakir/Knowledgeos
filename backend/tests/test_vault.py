import pytest

from knowledge_os import indexer, vault
from knowledge_os.markdown import parse_note, rewrite_wiki_links
from knowledge_os.util import Conflict, KOSError, NotFound


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/../../x", ".brainbox/knowledge.db", "Notes/.hidden",
                                 "", "/", "Notes/", "a\x00b", "con<>.md"])
def test_unsafe_paths_rejected(env, bad):
    with pytest.raises(KOSError):
        vault.resolve(bad)


def test_paths_normalised(env):
    assert vault.normalize_rel("/Notes//idea") == "Notes/idea.md"
    assert vault.normalize_rel("Notes\\x.md") == "Notes/x.md"


def test_symlink_escape_blocked(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    (env.vault_dir / "link").symlink_to(outside)
    with pytest.raises(KOSError):
        vault.read("link/secret.md")


def test_crud_versions_trash(env):
    r = vault.create("Notes/A.md", "one", "t")
    with pytest.raises(Conflict):
        vault.create("Notes/A.md", "again", "t")
    h = r["hash"]
    vault.write("Notes/A.md", "two", "t", expected_hash=h)
    with pytest.raises(Conflict):
        vault.write("Notes/A.md", "three", "t", expected_hash=h)  # stale hash
    versions = vault.list_versions("Notes/A.md")
    assert len(versions) == 1
    assert vault.get_version(versions[0]["id"])["content"] == "one"
    vault.restore_version(versions[0]["id"], "t")
    assert vault.read("Notes/A.md")["content"] == "one"

    d = vault.delete("Notes/A.md", "t")
    assert not vault.exists("Notes/A.md")
    assert indexer.stats()["notes"] == 0
    vault.restore_trash(d["trash_id"], "t")
    assert vault.read("Notes/A.md")["content"] == "one"
    assert indexer.stats()["notes"] == 1


def test_append_to_section(env):
    vault.create("N.md", "# T\n\n## Log\n\n- a\n\n## Other\n\ntext\n", "t")
    vault.append("N.md", "- b", "t", heading="Log")
    c = vault.read("N.md")["content"]
    assert c.index("- b") < c.index("## Other")
    vault.append("N.md", "x", "t", heading="New section")
    assert "## New section\n\nx" in vault.read("N.md")["content"]


def test_replace_text_requires_unique(env):
    vault.create("R.md", "foo foo bar", "t")
    with pytest.raises(KOSError):
        vault.replace_text("R.md", "foo", "x", "t")
    vault.replace_text("R.md", "bar", "baz", "t")
    assert vault.read("R.md")["content"] == "foo foo baz"
    with pytest.raises(KOSError):
        vault.replace_text("R.md", "missing", "x", "t")


def test_links_backlinks_aliases_and_rename(env):
    vault.create("Projects/Meta Ads.md", "---\naliases: [FB Ads]\ntags: [ads]\n---\n# Meta Ads\nSee [[Scaling]].", "t")
    vault.create("Notes/Testing.md", "Links to [[FB Ads]] and [[Projects/Meta Ads|ads]].\n```\n[[not a link]]\n```", "t")
    assert indexer.backlinks("Projects/Meta Ads.md") == ["Notes/Testing.md"]
    assert [u["target"] for u in indexer.unresolved_links()] == ["Scaling"]
    # creating the missing note resolves the dangling link
    vault.create("Notes/Scaling.md", "scale", "t")
    assert indexer.unresolved_links() == []
    assert "Projects/Meta Ads.md" in indexer.backlinks("Notes/Scaling.md")
    # rename rewrites links in other notes
    res = vault.move("Notes/Scaling.md", "Notes/Growth/Scaling Up.md", "t")
    assert res["updated_links"] == 1
    assert "[[Scaling Up]]" in vault.read("Projects/Meta Ads.md")["content"]
    assert indexer.backlinks("Notes/Growth/Scaling Up.md") == ["Projects/Meta Ads.md"]


def test_external_edit_picked_up(env):
    vault.create("X.md", "alpha", "t")
    (env.vault_dir / "X.md").write_text("beta [[Y]]")
    (env.vault_dir / "Y.md").write_text("why")
    stats = indexer.reconcile()
    assert stats["indexed"] == 2
    assert indexer.backlinks("Y.md") == ["X.md"]
    (env.vault_dir / "Y.md").unlink()
    assert indexer.reconcile()["removed"] == 1


def test_parse_obsidian_features():
    content = ("---\ntitle: Real Title\ntags:\n  - one\n  - Two/Sub\naliases: Alt\ncreated: 2024-01-02\n---\n"
               "# H1\nBody #inline and #123 not and url http://x.com/#frag and `#code`.\n"
               "![[image.png]] [[Target#Section|label]] [md](../Other%20Note.md)\n## H2\nmore")
    p = parse_note("Folder/file.md", content)
    assert p.title == "Real Title"
    assert p.tags == ["inline", "one", "two/sub"]
    assert p.aliases == ["Alt"]
    assert p.properties["created"] == "2024-01-02"
    kinds = {(l.key, l.kind) for l in p.links}
    assert ("target", "wiki") in kinds and ("image.png", "embed") in kinds and ("other note", "md") in kinds
    assert [h["text"] for h in p.headings] == ["H1", "H2"]
    assert any(c.heading == "H1 › H2" for c in p.chunks)


def test_rewrite_links_keeps_heading_and_alias():
    out, n = rewrite_wiki_links("[[Old#Part|shown]] and ![[old]] `[[Old]]`", {"old"}, "New")
    assert n == 2 and out.startswith("[[New#Part|shown]] and ![[New]]")


def test_read_missing(env):
    with pytest.raises(NotFound):
        vault.read("nope.md")
