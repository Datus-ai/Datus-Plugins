"""Site discovery and selection: unconfigured sites must be chosen, not guessed."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from datus_tableau_plugin import cli
from datus_tableau_plugin.cli import main
from datus_tableau_plugin.client import TableauClient
from datus_tableau_plugin.config import Settings
from datus_tableau_plugin.errors import ConfigError, PluginError
from test_cli_contract import PKG

NO_SITE = {
    "server_url": "https://tableau.example.test",
    "auth_mode": "token",
    "token_name": "automation",
    "token_secret": "never-print",
    "api_version": "3.21",
}
DEFAULT_SITE = {**NO_SITE, "site_content_url": ""}
NAMED_SITE = {**NO_SITE, "site_content_url": "Sales"}


class RecordingClient:
    """Captures the Settings used for sign-in and answers status/sites."""

    def __init__(self, settings, *, sites=None, deny=False):
        self.settings = settings
        self.sites = sites or []
        self.deny = deny
        self.calls = []

    def __enter__(self):
        self.calls.append(("enter", self.settings.site_content_url))
        return self

    def __exit__(self, *args):
        self.calls.append("exit")
        return False

    def status(self):
        return {
            "site_content_url": self.settings.site_content_url,
            "site_configured": self.settings.site_content_url is not None,
        }

    def list_sites(self):
        if self.deny:
            raise PluginError("listing sites requires a Server Administrator account")
        return self.sites

    def list_resources(self, group, *, project_id=None):
        self.calls.append(("list", group, project_id))
        return []


def install(monkeypatch, **kw):
    holder = {}

    def construct(settings):
        holder["client"] = RecordingClient(settings, **kw)
        return holder["client"]

    monkeypatch.setattr(cli, "TableauClient", construct)
    return holder


# --- configuration semantics ------------------------------------------------


def test_settings_distinguish_unset_from_default_site():
    assert Settings.from_profile(NO_SITE).site_content_url is None
    assert Settings.from_profile(DEFAULT_SITE).site_content_url == ""
    assert Settings.from_profile(NAMED_SITE).site_content_url == "Sales"
    with pytest.raises(ConfigError):
        Settings.from_profile({**NO_SITE, "site_content_url": 3})


def test_require_site_and_override():
    base = Settings.from_profile(NO_SITE)
    with pytest.raises(ConfigError, match="sites list"):
        base.require_site()
    assert base.with_site(None) is base
    assert base.with_site("").site_content_url == ""
    assert base.with_site("Finance").site_content_url == "Finance"
    base.with_site("").require_site()


# --- CLI enforcement ---------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["projects", "list"],
        ["dashboards", "list"],
        ["workbooks", "get", "book1"],
        ["sync", "run", "--all"],
        ["context", "export-dashboard", "dashboard:book1:Overview"],
        ["api", "call", "sites"],
    ],
)
def test_site_scoped_commands_refuse_to_run_without_a_site(argv, monkeypatch, capsys):
    holder = install(monkeypatch)
    assert main(argv, NO_SITE) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "site_content_url is not configured" in captured.err
    assert "sites list" in captured.err and "--site" in captured.err
    assert "client" not in holder  # refused before any sign-in


def test_site_override_targets_that_site_for_one_command(monkeypatch, capsys):
    holder = install(monkeypatch)
    assert main(["projects", "list", "--site", "Finance"], NO_SITE) == 0
    assert holder["client"].calls[0] == ("enter", "Finance")
    holder = install(monkeypatch)
    assert main(["projects", "list", "--site", ""], NO_SITE) == 0
    assert holder["client"].calls[0] == ("enter", "")
    holder = install(monkeypatch)
    assert main(["projects", "list", "--site", "Ops"], NAMED_SITE) == 0
    assert holder["client"].calls[0] == ("enter", "Ops")
    capsys.readouterr()


def test_status_works_without_a_site_and_reports_it(monkeypatch, capsys):
    install(monkeypatch)
    assert main(["status"], NO_SITE) == 0
    assert json.loads(capsys.readouterr().out) == {
        "site_content_url": None,
        "site_configured": False,
    }
    install(monkeypatch)
    assert main(["status"], NAMED_SITE) == 0
    assert json.loads(capsys.readouterr().out) == {
        "site_content_url": "Sales",
        "site_configured": True,
    }


def test_sites_list_works_without_a_site_and_lists_choices(monkeypatch, capsys):
    sites = [
        {"id": "s1", "name": "Default", "content_url": "", "state": "Active", "default": True},
        {"id": "s2", "name": "Sales", "content_url": "Sales", "state": "Active", "default": False},
    ]
    holder = install(monkeypatch, sites=sites)
    assert main(["sites", "list"], NO_SITE) == 0
    assert json.loads(capsys.readouterr().out) == sites
    assert holder["client"].calls[0] == ("enter", None)
    install(monkeypatch, sites=sites)
    assert main(["sites", "list", "-o", "yaml"], NO_SITE) == 0
    assert yaml.safe_load(capsys.readouterr().out) == sites


def test_sites_list_denied_for_non_admin_is_actionable(monkeypatch, capsys):
    install(monkeypatch, deny=True)
    assert main(["sites", "list"], NO_SITE) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Server Administrator" in captured.err


# --- real SDK path -----------------------------------------------------------

_NS = 'xmlns="http://tableau.com/api"'


def _response(url, status, body=b"", content_type="application/xml"):
    response = requests.Response()
    response.url = url
    response.status_code = status
    response.headers["Content-Type"] = content_type
    response._content = body
    response._content_consumed = True
    return response


def _signin(url):
    return _response(
        url,
        200,
        f'<tsResponse {_NS}><credentials token="t"><site id="site-luid" contentUrl=""/>'
        '<user id="u"/></credentials></tsResponse>'.encode(),
    )


def test_unconfigured_site_signs_in_to_default_site_and_pages_sites(monkeypatch):
    calls = []
    pages = 0

    def request(session, method, url, **kwargs):
        nonlocal pages
        calls.append((method, url, kwargs))
        if url.endswith("/auth/signin"):
            return _signin(url)
        if url.endswith("/auth/signout"):
            return _response(url, 204)
        if urlsplit(url).path.endswith("/sites"):
            pages += 1
            return _response(
                url,
                200,
                f'<tsResponse {_NS}><pagination pageNumber="{pages}" pageSize="1" totalAvailable="2"/>'
                f'<sites><site id="s{pages}" name="Site {pages}" contentUrl="{"" if pages == 1 else "Sales"}" state="Active"/></sites></tsResponse>'.encode(),
            )
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(requests.Session, "request", request)
    with TableauClient(Settings.from_profile(NO_SITE)) as client:
        sites = client.list_sites()
    assert b'contentUrl=""' in calls[0][2]["data"]
    assert [(s["content_url"], s["default"]) for s in sites] == [("", True), ("Sales", False)]
    assert [s["name"] for s in sites] == ["Site 1", "Site 2"]


def test_list_sites_forbidden_maps_to_guidance(monkeypatch):
    def request(session, method, url, **kwargs):
        if url.endswith("/auth/signin"):
            return _signin(url)
        if url.endswith("/auth/signout"):
            return _response(url, 204)
        if urlsplit(url).path.endswith("/sites"):
            return _response(
                url,
                403,
                f'<tsResponse {_NS}><error code="403004"><summary>Forbidden</summary>'
                "<detail>User is not a server administrator</detail></error></tsResponse>".encode(),
            )
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(requests.Session, "request", request)
    with TableauClient(Settings.from_profile(NO_SITE)) as client:
        with pytest.raises(PluginError, match="Server Administrator"):
            client.list_sites()


# --- manifest, prompt and skills ---------------------------------------------


def test_manifest_makes_site_optional_and_sites_list_allowed():
    manifest = yaml.safe_load((PKG / "datus-plugin.yml").read_text())
    schema = manifest["config_schema"]
    assert schema["required"] == ["server_url", "auth_mode"]
    assert schema["additionalProperties"] is False
    assert "default" not in schema["properties"]["site_content_url"]
    assert "auth_type" not in schema["properties"]
    assert schema["properties"]["auth_mode"]["enum"] == ["token", "login"]
    assert schema["properties"]["auth_mode"]["default"] == "token"
    # the conditional credential requirement follows the login/token vocabulary
    branch = schema["allOf"][0]
    assert branch["if"]["properties"]["auth_mode"] == {"const": "login"}
    assert branch["then"]["required"] == ["username", "password"]
    assert branch["else"]["required"] == ["token_name", "token_secret"]
    # same key and values as the Superset plugin
    superset = yaml.safe_load(
        (PKG.parents[1] / "datus-superset-plugin/datus_superset_plugin/datus-plugin.yml").read_text()
    )
    assert set(superset["config_schema"]["properties"]["auth_mode"]["enum"]) == {"login", "token"}
    for mode in ("normal", "auto"):
        assert "sites list:*" in manifest["permissions"][mode]["allow"]


@pytest.mark.parametrize(
    "profile,message",
    [
        ({"server_url": "https://t.example.test", "token_name": "n", "token_secret": "s"}, "auth_mode is required"),
        ({"server_url": "https://t.example.test", "auth_mode": "pat", "token_name": "n", "token_secret": "s"}, "login or token"),
        ({"server_url": "https://t.example.test", "auth_mode": "token", "username": "u", "password": "p"}, "token auth_mode requires token_name"),
        ({"server_url": "https://t.example.test", "auth_mode": "login", "token_name": "n", "token_secret": "s"}, "login auth_mode requires username"),
    ],
)
def test_auth_mode_is_required_and_drives_credential_requirements(profile, message):
    with pytest.raises(ConfigError, match=message):
        Settings.from_profile(profile)


def test_auth_mode_is_normalized_and_selects_the_sdk_auth():
    assert Settings.from_profile({**NO_SITE, "auth_mode": " Token "}).auth_mode == "token"
    login = Settings.from_profile(
        {"server_url": "https://t.example.test", "auth_mode": "LOGIN", "username": "u", "password": "p"}
    )
    assert login.auth_mode == "login"


def test_prompt_flags_unconfigured_site_and_points_to_sites_list():
    manifest = yaml.safe_load((PKG / "datus-plugin.yml").read_text())
    env = Environment(loader=FileSystemLoader(PKG), undefined=StrictUndefined)
    template = env.get_template(manifest["system_prompt"])
    common = {"plugin_name": "tableau", "config_path": None, "config_mutable": True}
    unset = template.render(profiles={"prod": {"server_url": "https://t"}}, **common)
    assert "site=NOT CONFIGURED" in unset and "sites list" in unset
    default = template.render(
        profiles={"prod": {"server_url": "https://t", "site_content_url": ""}}, **common
    )
    assert "site=default" in default
    named = template.render(
        profiles={"prod": {"server_url": "https://t", "site_content_url": "Sales"}},
        **common,
    )
    assert "site=Sales" in named


def test_skills_require_user_choice_for_site_project_and_dashboard():
    setup = (PKG / "skills/tableau-setup/SKILL.md").read_text()
    assert "sites list" in setup and "Server Administrator" in setup
    assert "`auth_mode` is required" in setup and "`token`" in setup and "`login`" in setup
    entry = (PKG / "skills/tableau/SKILL.md").read_text()
    assert "sites list" in entry and "--site" in entry
    assert "ask the user to pick" in entry
    sync = (PKG / "skills/tableau/references/sync.md").read_text()
    assert "projects list" in sync and "dashboards list --project-id" in sync
    assert "Only use `--all` when the user explicitly asks" in sync
    rebuild = (PKG / "skills/tableau/references/rebuild.md").read_text()
    assert "present the choices and ask" in rebuild
