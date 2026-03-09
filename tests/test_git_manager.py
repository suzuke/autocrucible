import subprocess
import pytest
from pathlib import Path
from crucible.git_manager import GitManager


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "file.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_create_branch(git_repo):
    gm = GitManager(git_repo, branch_prefix="crucible", tag_failed=True)
    gm.create_branch("test1")
    result = subprocess.run(["git", "branch", "--show-current"], cwd=git_repo, capture_output=True, text=True)
    assert result.stdout.strip() == "crucible/test1"


def test_commit(git_repo):
    gm = GitManager(git_repo, branch_prefix="crucible", tag_failed=True)
    gm.create_branch("test2")
    (git_repo / "file.txt").write_text("changed")
    gm.commit("test change")
    log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=git_repo, capture_output=True, text=True)
    assert "test change" in log.stdout


def test_head(git_repo):
    gm = GitManager(git_repo, branch_prefix="crucible", tag_failed=True)
    head = gm.head()
    assert len(head) == 7


def test_tag_failed_and_reset(git_repo):
    gm = GitManager(git_repo, branch_prefix="crucible", tag_failed=True)
    gm.create_branch("test3")
    original_head = gm.head()
    (git_repo / "file.txt").write_text("experiment")
    gm.commit("failed experiment")
    gm.tag_failed_and_reset("test3", 1)
    assert gm.head() == original_head
    tags = subprocess.run(["git", "tag", "-l", "failed/test3/*"], cwd=git_repo, capture_output=True, text=True)
    assert "failed/test3/1" in tags.stdout


def test_modified_files(git_repo):
    gm = GitManager(git_repo, branch_prefix="crucible", tag_failed=True)
    (git_repo / "file.txt").write_text("modified")
    modified = gm.modified_files()
    assert "file.txt" in modified


def test_revert_changes(git_repo):
    gm = GitManager(git_repo, branch_prefix="crucible", tag_failed=True)
    (git_repo / "file.txt").write_text("dirty")
    gm.revert_changes()
    assert (git_repo / "file.txt").read_text() == "initial"


def test_branch_exists(git_repo):
    gm = GitManager(workspace=git_repo)
    gm.create_branch("run1")
    assert gm.branch_exists("run1") is True
    assert gm.branch_exists("nonexistent") is False


def test_show_file(git_repo):
    gm = GitManager(workspace=git_repo)
    gm.create_branch("run1")
    (git_repo / "data.txt").write_text("hello world")
    gm.commit("add data")
    content = gm.show_file("run1", "data.txt")
    assert content == "hello world"


def test_checkout_branch(git_repo):
    gm = GitManager(workspace=git_repo)
    gm.create_branch("run1")
    subprocess.run(["git", "checkout", "main"], cwd=git_repo, check=True, capture_output=True)
    gm.checkout_branch("run1")
    result = subprocess.run(["git", "branch", "--show-current"], cwd=git_repo, capture_output=True, text=True)
    assert result.stdout.strip() == "crucible/run1"
