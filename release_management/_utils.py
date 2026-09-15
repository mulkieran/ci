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
from getpass import getpass
from typing import Any, List, Optional
from urllib.parse import urlparse

import requests
from build.util import project_wheel_metadata
from github import Github
from giturlparse import GitUrlParsed, parse
from semantic_version import Version
from specfile import specfile

MANIFEST_PATH = "./Cargo.toml"


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

    def __init__(self, base: Version, *, pre: bool = False, post: bool = False):
        """
        Initializer.
        :param Version base: Base version
        :param bool pre: whether or not this is a pre-release
        """
        assert not (pre and post), "Impossible for a release to be pre- and post-"
        self.base = base
        self.pre = pre
        self.post = post

    def __str__(self):
        return f"{self.base}{'~pre' if self.pre else ''}{'^post' if self.post else ''}"

    def release(self) -> Optional[str]:
        """
        Optional release string for spec file.
        """
        return release_stamp() if self.pre or self.post else None


def edit_specfile(
    specfile_path,
    *,
    release_version: Optional[ReleaseVersion] = None,
    sources: Optional[List[str]] = None,
    arbitrary: Optional[Callable[[specfile.Specfile], None]] = None,
):
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


def get_python_package_info(name: str) -> tuple[Version, GitUrlParsed]:
    """
    Get info about the python package.

    :param str name: the project name
    :returns: Version * ParseResult
    """
    config = project_wheel_metadata(".")
    assert config.get("Name") == name

    release_version = Version(config.get("version"))

    command = ["git", "remote", "get-url", "origin", "--push"]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as proc:
        stdout = proc.stdout
        assert stdout is not None, "stdout set in subprocess call"
        github_url = stdout.readline().strip().decode("utf-8")

    github_repo = parse(github_url)
    assert github_repo.host == "github.com", "specified repo is not on GitHub"
    return (release_version, github_repo)


def get_package_info(manifest_abs_path, package_name) -> tuple[Version, Any]:
    """
    Extract the version string and repo URL from Cargo.toml and return it.

    :param str manifest_path: absolute path to a Cargo.toml file
    :param str package_name: the expected name of the package
    :returns: stratisd version string and repository URL
    :rtype: Version * ParseResult
    """
    assert os.path.isabs(manifest_abs_path)

    with open(manifest_abs_path, "rb") as manifest:
        metadata = tomllib.load(manifest)

    package = metadata["package"]
    assert package["name"] == package_name, (
        f"package name in Cargo.toml ({package['name']}) != specified"
        f"package name ({package_name})"
    )
    github_repo = urlparse(package["repository"].rstrip("/"))
    assert github_repo.netloc == "github.com", "specified repo is not on GitHub"
    return (Version(package["version"]), github_repo)


def verify_tag(tag):
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


def set_tag(tag, message):
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


def get_branch():
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
    repository, tag, release_version, changelog_url, *, additional_assets=None
):
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


def vendor(manifest_abs_path, release_version, *, filterer=False):
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


def make_source_tarball(package_name, release_version, output_dir):
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


def get_changelog_url(repository_url, branch):
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
