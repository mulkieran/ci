#!/usr/bin/python3
#
# Copyright 2022 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Calculates values useful for making a release.
"""

import os
import subprocess
import tomllib
from collections.abc import Callable
from datetime import datetime
from functools import partial
from getpass import getpass
from typing import Any
from urllib.parse import ParseResult, urlparse

import requests
from build.util import project_wheel_metadata
from github import Github
from github.GitRelease import GitRelease
from semantic_version import Version
from specfile import specfile

MANIFEST_PATH = "./Cargo.toml"


def verify_github_url(parsed_url: ParseResult) -> None:
    """
    Verify that a parsed URL is a simple GitHub URL without fragment identifier or query string.

    :param ParseResult parsed_url: the parsed URL to verify
    :raises RuntimeError: if the URL is not on GitHub, or contains a fragment identifier or query string
    """
    if parsed_url.netloc != "github.com":
        raise RuntimeError(f"URL is not on GitHub: {parsed_url.geturl()}")
    if parsed_url.fragment:
        raise RuntimeError(f"URL contains fragment identifier: {parsed_url.geturl()}")
    if parsed_url.query:
        raise RuntimeError(f"URL contains query string: {parsed_url.geturl()}")


def release_stamp() -> str:
    """
    Return a release stamp. Should be unique to the second.
    :rtype: str
    :returns: a release stamp for modifying releases
    """
    command = ["git", "rev-parse", "--short=8", "HEAD"]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as proc:
        stdout = proc.stdout
        assert stdout is not None, "stdout set in subprocess call"
        commit_hash = stdout.readline().strip().decode("utf-8")
    return f"{datetime.today():%Y%m%d%H%M}git{commit_hash}"


class ReleaseVersion:
    """
    Release version for the package.
    """

    def __init__(self, base: Version, *, pre: bool = False, post: bool = False) -> None:
        """
        Initializer.
        :param Version base: Base version
        :param bool pre: whether or not this is a pre-release
        """
        assert not (pre and post), "Impossible for a release to be pre- and post-"
        self.base = base
        self.pre = pre
        self.post = post

    def __str__(self) -> str:
        return f"{self.base}{'~pre' if self.pre else ''}{'^post' if self.post else ''}"

    def release(self) -> str | None:
        """
        Optional release string for spec file.
        """
        return release_stamp() if self.pre or self.post else None


def edit_specfile(
    specfile_path: str | None,
    *,
    release_version: ReleaseVersion | None = None,
    sources: list[str] | None = None,
    arbitrary: Callable[[specfile.Specfile], None] | None = None,
) -> None:
    """
    Edit the specfile in place
    :param specfile_path: abspath of specfile
    :type specfile_path: str or NoneType
    :param ReleaseVersion release_version: release version to set in spec file
    :param sources: local source files
    :type sources: list of str or NoneType
    :param arbitrary: a function that takes the spec and does some action
    :type arbitrary: Specfile -> NoneType
    """
    if specfile_path is not None:
        with specfile.Specfile(specfile_path) as spec:
            if release_version is not None:
                spec.version = str(release_version)
                release = release_version.release()
                if release is not None:
                    spec.release = release
            if sources is not None:
                with spec.sources() as entries:
                    for index, value in enumerate(sources):
                        entries[index].location = value
            if arbitrary is not None:
                arbitrary(spec)


def get_python_package_info(name: str) -> tuple[Version, ParseResult]:
    """
    Get info about the python package.

    :param str name: the project name
    :returns: Version * ParseResult
    """
    config = project_wheel_metadata(".")
    assert config.get("Name") == name

    release_version = Version(config.get("version"))

    github_url = dict(u.split(", ", 1) for u in config.get_all("Project-URL", [])).get(
        "Homepage"
    )
    if github_url is None:
        raise RuntimeError("Homepage URL not found in project metadata")

    github_repo = urlparse(github_url)
    verify_github_url(github_repo)
    return (release_version, github_repo)


def get_package_info(
    manifest_abs_path: str, package_name: str
) -> tuple[Version, ParseResult]:
    """
    Extract the version string and repo URL from Cargo.toml and return it.

    :param str manifest_path: absolute path to a Cargo.toml file
    :param str package_name: the expected name of the package
    :returns: stratisd version string and repository URL
    :rtype: Version * ParseResult
    """
    assert os.path.isabs(manifest_abs_path)

    command = ["cargo", "get", f"--entry={manifest_abs_path}"]
    run_cargo = partial(subprocess.run, capture_output=True, text=True, check=True)

    name = run_cargo(command + ["package.name"]).stdout.strip()
    assert name == package_name, (
        f"package name in Cargo.toml ({name}) != specified package name ({package_name})"
    )

    repository = run_cargo(command + ["package.repository"]).stdout.strip()
    version = run_cargo(command + ["package.version"]).stdout.strip()

    github_repo = urlparse(repository.rstrip("/"))
    verify_github_url(github_repo)
    return (Version(version), github_repo)


def verify_tag(tag: str) -> bool:
    """
    Verify that the designated tag exists and point at current HEAD.

    :param str tag: the tag to check
    :returns: true if the tag exists, otherwise false
    :rtype: bool
    """
    command = ["git", "tag", "--points-at"]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as proc:
        stdout = proc.stdout
        assert stdout is not None, "stdout set in subprocess call"
        tag_str = stdout.readline()
    return tag_str.decode("utf-8").rstrip() == tag


def set_tag(tag: str, message: str) -> None:
    """
    Set specified tag on HEAD if it does not already exist.

    :param str tag: the tag to set
    :param str message: attach message to the tag
    :raises CalledProcessError:
    """
    if not verify_tag(tag):
        subprocess.run(
            ["git", "tag", "--annotate", tag, f"--message={message}"], check=True
        )


def get_branch() -> str:
    """
    Get the current git branch as a string.

    :rtype: str
    """
    command = ["git", "branch", "--show-current"]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as proc:
        stdout = proc.stdout
        assert stdout is not None, "stdout set in subprocess call"
        branch_str = stdout.readline()
    return branch_str.decode("utf-8").rstrip()


def create_release(
    repository: ParseResult,
    tag: str,
    release_version: Version | str,
    changelog_url: str,
    *,
    additional_assets: list[str] | None = None,
) -> GitRelease:
    """
    Create draft release from a pre-established GitHub tag for this repository.

    :param ParseResult repository: Git repository
    :param str tag: release tag
    :param str release_version: release version
    :param str changelog_url: changelog URL for the release notes
    :param additional_assets: names of additional assets to add to release
    :type additional_assets: (list of str) or None
    :return: GitHub release object
    """
    api_key = os.environ.get("GITHUB_API_KEY")
    if api_key is None:
        api_key = getpass("API key: ")

    git = Github(api_key)

    repo = git.get_repo(repository.path.strip("/"))

    release = repo.create_git_release(
        tag, f"Version {release_version}", f"See {changelog_url}", draft=True
    )

    for asset in [] if additional_assets is None else additional_assets:
        release.upload_asset(asset, label=asset)

    return release


def vendor(
    manifest_abs_path: str, release_version: ReleaseVersion, *, filterer: bool = False
) -> str:
    """
    Makes a vendor tarfile, suitable for uploading.

    :param str manifest_abs_path: manifest path (absolute)
    :param ReleaseVersion release_version: the release version
    :param bool filterer: filter dependencies in vendor tarfile
    :return: name of vendored tarfile
    :rtype: str
    """

    vendor_dir = "vendor"

    if filterer:
        subprocess.run(
            [
                "cargo",
                "vendor-filterer",
                f"--manifest-path={manifest_abs_path}",
                vendor_dir,
            ],
            check=True,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.run(
            [
                "cargo",
                "vendor",
                "--quiet",
                f"--manifest-path={manifest_abs_path}",
                vendor_dir,
            ],
            check=True,
        )

    vendor_tarfile_name = f"stratisd-{release_version}-vendor.tar.gz"

    subprocess.run(
        [
            "tar",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "--sort=name",
            "--pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime",
            "-czf",
            vendor_tarfile_name,
            vendor_dir,
        ],
        check=True,
    )

    return vendor_tarfile_name


def make_source_tarball(
    package_name: str, release_version: ReleaseVersion | str, output_dir: str
) -> str:
    """
    Make the source tarball and place it in the output dir.

    Imitate what GitHub does on a tag to the best of our ability.

    :param str package_name: the package name
    :param ReleaseVersion release_version: the release version
    :param str output_dir: the output directory
    :return absolute path of source tarball:
    :rtype: str
    """
    prefix = f"{package_name}-{release_version}"

    assert os.path.isabs(output_dir), f"{output_dir} is not an absolute path"

    output_file = os.path.join(output_dir, f"{prefix}.tar.gz")

    archive_cmd = [
        "git",
        "archive",
        "--format=tar.gz",
        f"--output={output_file}",
        f"--prefix={prefix}/",
        "HEAD",
    ]

    subprocess.run(archive_cmd, check=True)

    return output_file


def get_changelog_url(repository_url: str, branch: str) -> str:
    """
    Get the URL for the changelog in the release message.

    :param str repository_url: object representing the GitHub repo
    :param str branch: the git branch
    """
    changelog_url = f"{repository_url}/blob/{branch}/CHANGES.txt"
    requests_var = requests.get(changelog_url, timeout=30)
    if requests_var.status_code != requests.codes.ok:
        raise RuntimeError(f"Page at URL {changelog_url} not found")

    return changelog_url
