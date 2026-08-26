from __future__ import annotations

import hashlib

import pytest

from boomearth.providers.github_skill import (
    GitHubSkillProviderError,
    discover_markdown_references,
    parse_github_skill_url,
    parse_skill_document,
    parse_tree_response,
    render_repository_markdown,
)


@pytest.mark.parametrize(
    ("url", "scope", "requested_ref", "requested_path"),
    [
        ("https://github.com/acme/skills", "repository", None, ""),
        ("https://github.com/acme/skills.git", "repository", None, ""),
        (
            "https://github.com/acme/skills/tree/main/comic",
            "skill-directory",
            "main",
            "comic",
        ),
        (
            "https://github.com/acme/skills/blob/v1/comic/SKILL.md",
            "skill-file",
            "v1",
            "comic/SKILL.md",
        ),
    ],
)
def test_parse_github_skill_url_returns_one_unambiguous_target(
    url: str,
    scope: str,
    requested_ref: str | None,
    requested_path: str,
) -> None:
    target = parse_github_skill_url(url)

    assert (target.owner, target.repo, target.scope) == ("acme", "skills", scope)
    assert (target.requested_ref, target.requested_path) == (
        requested_ref,
        requested_path,
    )
    assert repr(target) == "GitHubSkillTarget(<redacted>)"


@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/acme/skills",
        "https://www.github.com/acme/skills",
        "https://user:pass@github.com/acme/skills",
        "https://github.com:443/acme/skills",
        "https://github.com/acme/skills/",
        "https://github.com/acme/skills?token=secret",
        "https://github.com/acme/skills#readme",
        "https://github.com/acme/../skills",
        "https://github.com/acme/skills/tree/main",
        "https://github.com/acme/skills/blob/main/comic/README.md",
        "https://github.com/acme/skills/blob/main/comic/../SKILL.md",
        "https://github.com/acme/skills/releases/download/v1/file.zip",
        "https://github.com/acme/skills.wiki",
        " https://github.com/acme/skills",
    ],
)
def test_parse_github_skill_url_rejects_noncanonical_or_ambiguous_input(
    value: str,
) -> None:
    with pytest.raises(
        GitHubSkillProviderError, match="^github-skill-input-invalid$"
    ):
        parse_github_skill_url(value)


def test_parse_tree_response_returns_only_safe_markdown_blobs() -> None:
    tree = parse_tree_response(
        {
            "sha": "a" * 40,
            "url": "https://api.github.com/repos/acme/skills/git/trees/" + "a" * 40,
            "tree": [
                {
                    "path": "comic",
                    "mode": "040000",
                    "type": "tree",
                    "sha": "b" * 40,
                    "url": "https://api.github.com/tree/comic",
                },
                {
                    "path": "README.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "c" * 40,
                    "size": 12,
                    "url": "https://api.github.com/blob/readme",
                },
                {
                    "path": "comic/SKILL.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "d" * 40,
                    "size": 42,
                    "url": "https://api.github.com/blob/skill",
                },
                {
                    "path": "script.py",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "e" * 40,
                    "size": 9,
                    "url": "https://api.github.com/blob/script",
                },
            ],
            "truncated": False,
        },
        max_entries=10,
    )

    assert [(item.path, item.blob_sha, item.size_bytes) for item in tree] == [
        ("README.md", "c" * 40, 12),
        ("comic/SKILL.md", "d" * 40, 42),
    ]


def test_parse_tree_response_rejects_truncation_before_returning_partial_data() -> None:
    with pytest.raises(
        GitHubSkillProviderError, match="^github-skill-tree-truncated$"
    ):
        parse_tree_response(
            {"sha": "a" * 40, "url": "https://api.github.com/tree", "tree": [], "truncated": True},
            max_entries=10,
        )


def test_parse_tree_response_ignores_valid_noncandidate_entries() -> None:
    entries = parse_tree_response(
        {
            "sha": "b" * 40,
            "url": "https://api.github.com/tree",
            "tree": [
                {
                    "path": "assets/演示 图.png",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "a" * 40,
                    "size": 8,
                    "url": "https://api.github.com/blob/asset",
                },
                {
                    "path": "docs/latest.md",
                    "mode": "120000",
                    "type": "blob",
                    "sha": "c" * 40,
                    "size": 8,
                    "url": "https://api.github.com/blob/link",
                },
                {
                    "path": "vendor",
                    "mode": "160000",
                    "type": "commit",
                    "sha": "d" * 40,
                    "url": "https://api.github.com/commit/vendor",
                },
            ],
            "truncated": False,
        },
        max_entries=10,
    )

    assert entries == ()


def test_parse_tree_response_rejects_non_utf8_noncandidate_path() -> None:
    with pytest.raises(
        GitHubSkillProviderError, match="^github-skill-response-invalid$"
    ):
        parse_tree_response(
            {
                "sha": "b" * 40,
                "url": "https://api.github.com/tree",
                "tree": [
                    {
                        "path": "assets/\ud800.png",
                        "mode": "100644",
                        "type": "blob",
                        "sha": "a" * 40,
                        "size": 8,
                        "url": "https://api.github.com/blob/asset",
                    }
                ],
                "truncated": False,
            },
            max_entries=10,
        )


def test_parse_tree_response_rejects_unsafe_candidate_path() -> None:
    with pytest.raises(
        GitHubSkillProviderError, match="^github-skill-response-invalid$"
    ):
        parse_tree_response(
            {
                "sha": "b" * 40,
                "url": "https://api.github.com/tree",
                "tree": [
                    {
                        "path": "comic/../SKILL.md",
                        "mode": "100644",
                        "type": "blob",
                        "sha": "a" * 40,
                        "size": 8,
                        "url": "https://api.github.com/blob/escape",
                    }
                ],
                "truncated": False,
            },
            max_entries=10,
        )


def test_parse_tree_response_enforces_entry_budget() -> None:
    entries = [
        {
            "path": f"skill-{index}/SKILL.md",
            "mode": "100644",
            "type": "blob",
            "sha": f"{index:040x}",
            "size": 8,
            "url": f"https://api.github.com/blob/{index}",
        }
        for index in range(3)
    ]
    with pytest.raises(
        GitHubSkillProviderError,
        match="^github-skill-request-budget-exceeded$",
    ):
        parse_tree_response(
            {
                "sha": "b" * 40,
                "url": "https://api.github.com/tree",
                "tree": entries,
                "truncated": False,
            },
            max_entries=2,
        )


def test_skill_frontmatter_is_exact_and_source_safe() -> None:
    descriptor = parse_skill_document(
        "comic/SKILL.md",
        "a" * 40,
        "---\nname: comic\ndescription: Explain ideas\n---\n# Body\n",
    )

    assert (descriptor.path, descriptor.name, descriptor.description) == (
        "comic/SKILL.md",
        "comic",
        "Explain ideas",
    )
    assert descriptor.blob_sha == "a" * 40
    assert "# Body" not in repr(descriptor)


@pytest.mark.parametrize(
    "text",
    [
        "# no frontmatter\n",
        "---\nname: comic\nname: duplicate\ndescription: x\n---\n",
        "---\nname: Comic Space\ndescription: x\n---\n",
        "---\nname: comic\ndescription:\n---\n",
        "---\nname: comic\nlicense: private\n---\n",
        "---\nname: comic\ndescription: contains\\x00nul\n---\n",
    ],
)
def test_skill_frontmatter_rejects_missing_duplicate_or_unsafe_fields(
    text: str,
) -> None:
    text = text.replace("\\x00", "\x00")
    with pytest.raises(
        GitHubSkillProviderError, match="^github-skill-response-invalid$"
    ):
        parse_skill_document("comic/SKILL.md", "a" * 40, text)


def test_discover_markdown_references_resolves_only_direct_safe_links() -> None:
    references = discover_markdown_references(
        "comic/SKILL.md",
        "Read [palette](references/palette.md), [layout](references/layout.MD), "
        "and [web](https://example.com/reference.md).\n"
        "Duplicate [palette](references/palette.md).\n",
    )

    assert references == (
        "comic/references/layout.MD",
        "comic/references/palette.md",
    )


@pytest.mark.parametrize(
    "target",
    ["../shared.md", "references/../../escape.md", "/absolute.md", "references/%2e%2e/escape.md"],
)
def test_discover_markdown_references_rejects_scope_escape(target: str) -> None:
    with pytest.raises(
        GitHubSkillProviderError, match="^github-skill-path-invalid$"
    ):
        discover_markdown_references(
            "comic/SKILL.md", f"Read [unsafe]({target}).\n"
        )


def test_render_repository_markdown_is_ordered_hash_bound_and_deterministic() -> None:
    readme = b"# Skills\n"
    skill = b"---\nname: comic\ndescription: Explain ideas\n---\n"
    payload = render_repository_markdown(
        repository="acme/skills",
        commit_sha="b" * 40,
        license_status="UNDECLARED",
        documents=(("comic/SKILL.md", skill), ("README.md", readme)),
    )

    assert payload.startswith(b"# Private GitHub Skill repository digest\n")
    assert payload.index(b"README.md") < payload.index(b"comic/SKILL.md")
    assert hashlib.sha256(readme).hexdigest().encode("ascii") in payload
    assert hashlib.sha256(skill).hexdigest().encode("ascii") in payload
    assert payload == render_repository_markdown(
        repository="acme/skills",
        commit_sha="b" * 40,
        license_status="UNDECLARED",
        documents=(("README.md", readme), ("comic/SKILL.md", skill)),
    )


def test_render_repository_markdown_rejects_duplicate_binary_or_invalid_utf8() -> None:
    for documents in (
        (("README.md", b"one"), ("README.md", b"two")),
        (("README.md", b"a\x00b"),),
        (("README.md", b"\xff"),),
    ):
        with pytest.raises(
            GitHubSkillProviderError, match="^github-skill-response-invalid$"
        ):
            render_repository_markdown(
                repository="acme/skills",
                commit_sha="b" * 40,
                license_status="UNKNOWN",
                documents=documents,
            )
