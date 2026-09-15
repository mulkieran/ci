#!/usr/bin/python3
#
# Copyright 2020 Red Hat, Inc.
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
Make a GitHub Draft release.
"""

import argparse
import os
import subprocess
from unittest.mock import patch

from _utils import (
    MANIFEST_PATH,
    ReleaseVersion,
    create_release,
    get_branch,
    get_changelog_url,
    get_package_info,
    get_python_package_info,
    set_tag,
    vendor,
)


def _with_dry_run(dry_run):
    """
    Run closure either w/ or w/out patch for name.

    :param bool dry_run: True if dry_run only
    """

    def func_patch(to_patch_str):
        """
        Make a generic patch for an imported method.

        :param str to_patch_str: the string that identifies the method
        """

        def side_effect(name):
            """
            Utility method for printing a message when mocking a method.

            :param str name: the method name to print
            """

            def print_message(*args, **kwargs):
                print(f"Mocking {name}: {args} {kwargs}")

            return print_message

        return patch(
            to_patch_str, return_value=None, side_effect=side_effect(to_patch_str)
        )

    def func(name, closure, *, skip=False):
        """
        :param str name: the name of the method to mock
        :param closure: the closure to run, invoked w/ no arguments
        :param bool skip: If True, just skip.
        """
        if skip:
            return

        if dry_run:
            with func_patch(name):
                closure()
        else:
            closure()

    return func


def _push_tag(repository_url: str, tag: str):
    """
    Push a tag.

    :param str repository_url: the repo to push to
    :param str tag: the tag to push
    """
    subprocess.run(["git", "push", repository_url, "tag", tag], check=True)


class RustCrates:
    """
    Methods for assisting in building and releasing Rust crates.
    """

    @staticmethod
    def set_up_subcommand(
        subcmd,
        subparsers,
        *,
        add_github_release_option=False,
        add_vendor_option=False,
        subcmd_aliases=None,
    ):
        """
        Set up subcommand parsers
        :param str subcmd: the name of the subcommand
        :param argparse subparsers: the subparsers variable
        :param bool add_github_release_option: whether to pass no-github-release option
        :param bool add_vendor_option: whether to allow no-vendor option
        """

        subcmd_aliases = [] if subcmd_aliases is None else subcmd_aliases

        new_subparser = subparsers.add_parser(
            subcmd, help=f"Create a release for {subcmd}.", aliases=subcmd_aliases
        )

        new_subparser.set_defaults(
            func=lambda namespace: RustCrates.tag_rust_library(namespace, subcmd)
        )

        new_subparser.add_argument(
            "--no-publish",
            action="store_true",
            default=False,
            dest="no_publish",
            help="Do not publish to crates.io",
        )

        if add_github_release_option:
            new_subparser.add_argument(
                "--no-github-release",
                action="store_true",
                default=False,
                dest="no_github_release",
                help="Do not release to GitHub",
            )
        else:
            new_subparser.set_defaults(no_github_release=True)

        if add_vendor_option:
            new_subparser.add_argument(
                "--no-vendor",
                action="store_true",
                default=False,
                dest="no_vendor",
                help="Do not make a vendor tarfile",
            )
            new_subparser.add_argument(
                "--vendor-method",
                action="store",
                help="Method of Rust vendoring",
                choices=["standard", "filtered"],
                default="standard",
            )
        else:
            new_subparser.set_defaults(no_vendor=True)

    @staticmethod
    def tag_rust_library(namespace, name):
        """
        Set new tag for rust library.

        :param namespace: parser namespace
        :param str name: the Rust name (as in Cargo.toml) and the GitHub repo name
        """
        dry_run_caller = _with_dry_run(namespace.dry_run)

        manifest_abs_path = os.path.abspath(MANIFEST_PATH)
        if not os.path.exists(manifest_abs_path):
            raise RuntimeError(
                "Need script to run at top-level of package, in same directory as Cargo.toml"
            )

        (release_version, repository) = get_package_info(manifest_abs_path, name)

        try:
            subprocess.run(
                ["cargo", "package", "--manifest-path", MANIFEST_PATH], check=True
            )
        finally:
            subprocess.run(["cargo", "clean"], check=True)

        additional_assets = []
        if not namespace.no_vendor:
            filtered = namespace.vendor_method == "filtered"
            vendor_tarfile_name = vendor(
                manifest_abs_path, ReleaseVersion(release_version), filterer=filtered
            )
            subprocess.run(
                ["sha256sum", os.path.abspath(vendor_tarfile_name)], check=True
            )
            additional_assets = [vendor_tarfile_name]

        if namespace.no_tag:
            return

        tag = f"{name}-v{release_version}"

        dry_run_caller(
            "__main__.set_tag",
            lambda: set_tag(tag, f"{name} version {release_version}"),
        )

        if namespace.no_release:
            return

        push_git_url = (
            repository.geturl() if namespace.git_repo is None else namespace.git_repo
        )

        dry_run_caller("__main__._push_tag", lambda: _push_tag(push_git_url, tag))

        dry_run_caller(
            "__main__.create_release",
            lambda: create_release(
                repository,
                tag,
                release_version,
                get_changelog_url(repository.geturl(), get_branch()),
                additional_assets=additional_assets,
            ),
            skip=namespace.no_github_release,
        )

        # The lambda is necessary in order to prevent the interpreter from
        # resolving _publish before the mock method is put into place.
        dry_run_caller(
            "__main__.RustCrates._publish",
            lambda: RustCrates._publish(),  # noqa: PLW0108
            skip=namespace.no_publish,
        )

    @staticmethod
    def _publish():
        """
        Run git commands to publish a crate to crates.io.
        """
        subprocess.run(["git", "clean", "-xdf"], check=True)
        subprocess.run(["cargo", "clean"], check=True)
        subprocess.run(["cargo", "publish"], check=True)


class PythonPackages:
    """
    Methods for assisting in building and releasing Python packages.
    """

    @staticmethod
    def set_up_subcommand(subcmd, subparsers, *, add_github_release_option=False):
        """
        Set up subcommand parsers
        :param str subcmd: the name of the subcommand
        :param argparse subparsers: the subparsers variable
        :param bool add_github_release_option: whether to pass no-github-release option
        """
        new_subparser = subparsers.add_parser(
            subcmd, help=f"Create a release for {subcmd}"
        )
        new_subparser.set_defaults(
            func=lambda namespace: PythonPackages.tag_python_library(namespace, subcmd)
        )

        if add_github_release_option:
            new_subparser.add_argument(
                "--no-github-release",
                action="store_true",
                default=False,
                dest="no_github_release",
                help="Do not release to GitHub",
            )
        else:
            new_subparser.set_defaults(no_github_release=True)

    @staticmethod
    def tag_python_library(namespace, name):
        """
        Tag a Python library.

        :param namespace: parser namespace
        :param str name: package_name
        """
        dry_run_caller = _with_dry_run(namespace.dry_run)

        (release_version, repository) = get_python_package_info(name)

        if namespace.no_tag:
            return

        tag = f"v{release_version}"

        dry_run_caller(
            "__main__.set_tag",
            lambda: set_tag(tag, f"{name} version {release_version}"),
        )

        if namespace.no_release:
            return

        push_git_url = (
            repository.url if namespace.git_repo is None else namespace.git_repo
        )

        dry_run_caller("__main__._push_tag", lambda: _push_tag(push_git_url, tag))

        dry_run_caller(
            "__main__.create_release",
            lambda: create_release(
                repository,
                tag,
                release_version,
                get_changelog_url(repository.url, get_branch()),
            ),
            skip=namespace.no_github_release,
        )


def _create_rust_subcommands(subparsers):
    RustCrates.set_up_subcommand(
        "stratisd", subparsers, add_github_release_option=True, add_vendor_option=True
    )

    RustCrates.set_up_subcommand(
        "devicemapper",
        subparsers,
        add_github_release_option=True,
        subcmd_aliases=["devicemapper-rs"],
    )

    RustCrates.set_up_subcommand(
        "devicemapper-sys", subparsers, subcmd_aliases=["devicemapper-rs-sys"]
    )

    RustCrates.set_up_subcommand("libcryptsetup-rs", subparsers)

    RustCrates.set_up_subcommand("libcryptsetup-rs-sys", subparsers)

    RustCrates.set_up_subcommand("libblkid-rs", subparsers)

    RustCrates.set_up_subcommand("libblkid-rs-sys", subparsers)

    RustCrates.set_up_subcommand("stratisd_proc_macros", subparsers)

    RustCrates.set_up_subcommand("loopdev-3", subparsers, subcmd_aliases=["loopdev"])


def _create_python_subcommands(subparsers):
    PythonPackages.set_up_subcommand(
        "stratis-cli", subparsers, add_github_release_option=True
    )

    PythonPackages.set_up_subcommand("pyudev", subparsers)

    PythonPackages.set_up_subcommand("dbus-python-client-gen", subparsers)

    PythonPackages.set_up_subcommand("dbus-client-gen", subparsers)

    PythonPackages.set_up_subcommand("into-dbus-python", subparsers)

    PythonPackages.set_up_subcommand("dbus-signature-pyparsing", subparsers)

    PythonPackages.set_up_subcommand("justbases", subparsers)

    PythonPackages.set_up_subcommand("justbytes", subparsers)

    PythonPackages.set_up_subcommand("hs-dbus-signature", subparsers)

    PythonPackages.set_up_subcommand("testing", subparsers)


def _get_parser():
    """
    Build parser
    """
    parser = argparse.ArgumentParser(description="Create a GitHub Draft release.")

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Only report actions, do not do them",
    )

    parser.add_argument(
        "--no-tag",
        action="store_true",
        default=False,
        dest="no_tag",
        help="only create artifacts",
    )

    parser.add_argument(
        "--no-release",
        action="store_true",
        default=False,
        dest="no_release",
        help="stop before pushing any changes to GitHub repo",
    )

    parser.add_argument(
        "--git-repo", dest="git_repo", help="Git repository URL for tag push"
    )

    subparsers = parser.add_subparsers(title="subcommands", required=True)

    rust_subparser = subparsers.add_parser(
        "rust", help="Create a release for a rust package."
    ).add_subparsers(title="rust", required=True)

    _create_rust_subcommands(rust_subparser)

    python_subparser = subparsers.add_parser(
        "python", help="Create a release for a python package."
    ).add_subparsers(title="python", required=True)

    _create_python_subcommands(python_subparser)

    return parser


def main():
    """
    Main function
    """

    parser = _get_parser()

    namespace = parser.parse_args()

    namespace.func(namespace)

    return 0


if __name__ == "__main__":
    main()
