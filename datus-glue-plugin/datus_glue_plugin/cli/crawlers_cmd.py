"""`datus glue crawlers ...` — list/inspect/run/stop crawlers and their schedule."""

from __future__ import annotations

import argparse

from datus_aws_common import (
    add_output_option,
    call,
    eprint,
    paginate,
    render_one,
    render_rows,
    wait_until,
)


def register(sub: argparse._SubParsersAction) -> None:
    crawlers = sub.add_parser("crawlers", help="Glue crawlers: list, run, stop, schedule")
    group = crawlers.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("list", help="list crawlers")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_list)

    p = group.add_parser("get", help="describe one crawler")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_get)

    p = group.add_parser("status", help="show a crawler's state and last crawl")
    p.add_argument("name")
    add_output_option(p)
    p.set_defaults(func=cmd_status)

    p = group.add_parser("run", help="start a crawler (updates the catalog only)")
    p.add_argument("name")
    p.add_argument("--wait", action="store_true", help="poll until the crawl finishes")
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--timeout", type=float, default=3600.0)
    p.set_defaults(func=cmd_run)

    p = group.add_parser("stop", help="stop a running crawler")
    p.add_argument("name")
    p.set_defaults(func=cmd_stop)

    p = group.add_parser("history", help="recent crawl history")
    p.add_argument("name")
    p.add_argument("--limit", type=int, default=20)
    add_output_option(p)
    p.set_defaults(func=cmd_history)

    p = group.add_parser("metrics", help="crawler metrics")
    p.add_argument("name", nargs="*", help="crawler name(s) (default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_metrics)

    p = group.add_parser("schedule-pause", help="pause a crawler's schedule")
    p.add_argument("name")
    p.set_defaults(func=cmd_schedule_pause)

    p = group.add_parser("schedule-resume", help="resume a crawler's schedule")
    p.add_argument("name")
    p.set_defaults(func=cmd_schedule_resume)


def cmd_list(ctx, ns) -> int:
    rows = paginate(ctx.client("glue"), "get_crawlers", "Crawlers", limit=ns.limit)
    print(render_rows(rows, ["Name", "State", "DatabaseName"], ns.output))
    return 0


def cmd_get(ctx, ns) -> int:
    crawler = call(ctx.client("glue").get_crawler, Name=ns.name)["Crawler"]
    print(render_one(crawler, ns.output))
    return 0


def cmd_status(ctx, ns) -> int:
    crawler = call(ctx.client("glue").get_crawler, Name=ns.name)["Crawler"]
    last = crawler.get("LastCrawl", {}) or {}
    view = {
        "Name": crawler.get("Name"),
        "State": crawler.get("State"),
        "LastStatus": last.get("Status"),
        "LastStart": last.get("StartTime"),
        "MessagePrefix": last.get("MessagePrefix"),
    }
    print(render_one(view, ns.output))
    return 0


def cmd_run(ctx, ns) -> int:
    client = ctx.client("glue")
    pre = (call(client.get_crawler, Name=ns.name)["Crawler"].get("LastCrawl") or {}).get("StartTime")
    call(client.start_crawler, Name=ns.name)
    print(f"started crawler {ns.name}")
    if not ns.wait:
        return 0

    def poll():
        return call(client.get_crawler, Name=ns.name)["Crawler"]

    def done(crawler):
        if crawler.get("State") != "READY":
            return False
        return (crawler.get("LastCrawl") or {}).get("StartTime") != pre

    final = wait_until(
        poll, done, timeout=ns.timeout, interval=ns.interval,
        on_change=lambda c: eprint(f"crawler {ns.name}: {c.get('State')}"),
    )
    status = (final.get("LastCrawl") or {}).get("Status")
    print(f"crawler {ns.name}: {status or 'READY'}")
    return 0 if status in (None, "SUCCEEDED") else 1


def cmd_stop(ctx, ns) -> int:
    call(ctx.client("glue").stop_crawler, Name=ns.name)
    print(f"stopping crawler {ns.name}")
    return 0


def cmd_history(ctx, ns) -> int:
    resp = call(ctx.client("glue").list_crawls, CrawlerName=ns.name, MaxResults=ns.limit)
    rows = resp.get("Crawls", [])
    print(render_rows(rows, ["CrawlId", "State", "StartTime", "EndTime", "Summary"], ns.output))
    return 0


def cmd_metrics(ctx, ns) -> int:
    kwargs = {"CrawlerNameList": ns.name} if ns.name else {}
    resp = call(ctx.client("glue").get_crawler_metrics, **kwargs)
    rows = resp.get("CrawlerMetricsList", [])
    print(render_rows(rows, ["CrawlerName", "LastRuntimeSeconds", "TablesCreated", "TablesUpdated", "TablesDeleted"], ns.output))
    return 0


def cmd_schedule_pause(ctx, ns) -> int:
    call(ctx.client("glue").stop_crawler_schedule, CrawlerName=ns.name)
    print(f"paused schedule for crawler {ns.name}")
    return 0


def cmd_schedule_resume(ctx, ns) -> int:
    call(ctx.client("glue").start_crawler_schedule, CrawlerName=ns.name)
    print(f"resumed schedule for crawler {ns.name}")
    return 0
