"""The ``vnstock-oapi`` command line: same names, same order as the Python API.

Dòng lệnh ``vnstock-oapi``: cùng tên, cùng thứ tự với API Python.

``<nguồn>`` is the source you picked; ``vnstock-oapi status`` lists them.
``<nguồn>`` là nguồn bạn chọn; ``vnstock-oapi status`` liệt kê các nguồn.

vnstock-oapi status
vnstock-oapi keys set <nguồn>
vnstock-oapi check
vnstock-oapi auth <nguồn>
vnstock-oapi ohlc <nguồn> FPT --start 2026-01-01 -o fpt.jsonl
vnstock-oapi ops <nguồn>
vnstock-oapi raw <nguồn> <thao-tác> [khoá=giá_trị ...]
vnstock-oapi dump <nguồn> --symbol FPT
vnstock-oapi stream <nguồn> <kênh...> --symbols FPT,HPG --seconds 60 -o frames.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import json
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import vnstock_oapi as oapi
from vnstock_oapi.core import credentials as creds_mod
from vnstock_oapi.core.envelope import Envelopes, open_private
from vnstock_oapi.core.shape import render_markdown, shape_of
from vnstock_oapi.core.source import VERBS, Plan, Source
from vnstock_oapi.core.timeutil import VN_TZ
from vnstock_oapi.errors import InputError, OapiError, UnsupportedError

DATA_WARNING = (
    "Dữ liệu thật của nguồn. Điều khoản của các nguồn cấm đưa cho bên thứ ba, kể cả dữ liệu đã "
    "xử lý: không commit, không chia sẻ, không dán vào trợ lý AI trên đám mây."
)

_INT = re.compile(r"^-?(?:0|[1-9]\d*)$")


STREAM_WARNING = (
    "Client Python tham chiếu: dễ đọc nhưng chậm. Đọc socket và xử lý dùng chung một GIL, xử lý nặng làm "
    "trễ nhịp tim và nguồn có thể đóng kết nối."
)


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _source(args: argparse.Namespace) -> Source:
    return oapi.source(args.source, args.profile)


def _parse_kv(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"Tham số phải có dạng khoá=giá_trị: {pair!r}")
        out[key] = int(value) if _INT.match(value) else value
    return out


def _emit(envelopes: Envelopes, output: str | None) -> None:
    if output:
        path = envelopes.save(output)
        print(f"Đã ghi {len(envelopes)} phong bì vào {path}", file=sys.stderr)
        print(DATA_WARNING, file=sys.stderr)
        return
    _print(envelopes.summary())
    if envelopes:
        text = json.dumps(envelopes[0].body, ensure_ascii=False, indent=2)
        print(text[:2000] + ("\n... (cắt bớt; dùng -o để ghi đủ)" if len(text) > 2000 else ""))


# -- commands -----------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    _print(oapi.status(args.profile))


def cmd_keys(args: argparse.Namespace) -> None:
    source = _source(args)
    for field in source.spec.credentials:
        if args.action == "delete":
            creds_mod.keyring_delete(source.name, source.profile, field.name)
            continue
        value = getpass.getpass(f"{source.spec.title} · {field.label or field.name}: ")
        if not value.strip():
            if field.required:
                raise SystemExit(f"{field.name} không được để trống")
            continue
        creds_mod.keyring_set(source.name, source.profile, field.name, value.strip())
    done = "Đã xoá" if args.action == "delete" else "Đã cất vào keyring"
    print(f"{done} khoá của {source.name} (hồ sơ {source.profile})")


def cmd_check(args: argparse.Namespace) -> None:
    names = args.sources or oapi.sources()
    unknown = sorted(set(names) - set(oapi.sources()))
    if unknown:
        raise SystemExit(f"Không có nguồn: {', '.join(unknown)}")
    results = []
    for name in names:
        source = oapi.source(name, args.profile)
        row: dict[str, Any] = {"source": name}
        try:
            row["keys"] = dict(source.credentials.origins)
            plan = source.spec.check() if source.spec.check else None
            if plan is None:
                row["call"] = "không có lệnh kiểm tra"
            else:
                envelopes = source.raw(plan.operation, **plan.params)
                row["call"] = f"{plan.operation}: HTTP {envelopes[0].http_status}"
            row["ok"] = True
        except OapiError as exc:
            row["ok"] = False
            row["error"] = f"{type(exc).__name__}: {exc}"
        results.append(row)
    _print(results)


def cmd_auth(args: argparse.Namespace) -> None:
    """Open a session with a source (`auth <source>`), or drop it (`auth <source> --clear`, `--clear --all`).

    This is the source's own authentication (OTP), not a Vnstock account.
    """
    if args.clear:
        if not args.all and not args.source:
            raise SystemExit("Chọn một nguồn, hoặc --all")
        names = oapi.sources() if args.all else [args.source]
        for name in names:
            oapi.source(name, args.profile).clear_auth()
        print(f"Đã xoá phiên: {', '.join(names)}")
        return
    if not args.source:
        raise SystemExit("Chọn một nguồn")
    source = _source(args)
    asks = source.auth.asks
    if asks is None:
        _print(source.authenticate())
        return
    typed = getpass.getpass(f"{asks[0].upper()}{asks[1:]} {source.spec.title} (không hiện khi gõ): ")
    _print(source.authenticate(typed.strip()))


def cmd_ops(args: argparse.Namespace) -> None:
    _print(_source(args).ops)


def cmd_terms(args: argparse.Namespace) -> None:
    print(oapi.terms(args.source))


def cmd_verb(args: argparse.Namespace) -> None:
    source = _source(args)
    kwargs: dict[str, Any] = {}
    for name in ("start", "end", "interval", "limit", "asset"):
        value = getattr(args, name, None)
        if value is not None:
            kwargs[name] = value
    positional = [args.symbol] if args.symbol else []
    if args.verb == "symbols":
        kwargs = _parse_kv(args.params)
    elif args.verb == "calendar":
        kwargs, positional = {}, []
    elif args.verb == "quote":
        kwargs = {}
    elif args.verb in ("trades", "foreign", "index", "events"):
        kwargs.pop("interval", None)
        kwargs.pop("asset", None)
        if args.verb == "events":
            kwargs.pop("limit", None)
    if args.preview:
        _print(source.preview(args.verb, *positional, **kwargs))
        return
    _emit(getattr(source, args.verb)(*positional, **kwargs), args.output)


def cmd_call(args: argparse.Namespace) -> None:
    source = _source(args)
    positional = [token for token in args.args if "=" not in token]
    kwargs = _parse_kv([token for token in args.args if "=" in token])
    if args.preview:
        _print(source.preview(args.name, *positional, **kwargs))
        return
    if args.name not in source.spec.extras:
        raise SystemExit(
            f"{source.name} không có hàm {args.name!r}. Có: {', '.join(sorted(source.spec.extras))}"
        )
    _emit(getattr(source, args.name)(*positional, **kwargs), args.output)


def cmd_raw(args: argparse.Namespace) -> None:
    source = _source(args)
    params = _parse_kv(args.params)
    if args.preview:
        _print(source.preview(args.operation, only_page=args.page, **params))
        return
    _emit(source.raw(args.operation, only_page=args.page, **params), args.output)


# -- stream -------------------------------------------------------------------


def _stream_module() -> ModuleType | None:
    # The stream package ships only with a source that declares a stream; a build may leave it out.
    try:
        return importlib.import_module("vnstock_oapi.stream")
    except ImportError:
        return None


def _stream_sources() -> list[str]:
    module = _stream_module()
    return list(module.stream_sources()) if module else []


def cmd_stream(args: argparse.Namespace) -> None:
    stream = _stream_module()
    if stream is None:
        raise UnsupportedError("Bản này không có stream")
    spec = stream.load_stream_spec(args.source)
    if not args.channels:
        _print(
            [
                {"pattern": rule.pattern, "symbols": rule.symbols, "description": rule.description}
                for rule in spec.channels
            ]
        )
        return
    client = stream.client_for(args.source, profile=args.profile)
    symbols = [symbol for symbol in (args.symbols or "").split(",") if symbol.strip()]
    for channel in args.channels:
        client.subscribe(channel, symbols if spec.resolve(channel).symbols else ())
    print(STREAM_WARNING, file=sys.stderr)
    counts: Counter[str] = Counter()

    async def run() -> None:
        asyncio.get_running_loop().call_later(args.seconds, client.stop)
        handle = open_private(args.output) if args.output else None
        flushed = time.monotonic()
        try:
            async for frame in client.frames():
                counts[frame.channel or frame.kind] += 1
                if handle:
                    handle.write(json.dumps(frame.to_dict(), ensure_ascii=False) + "\n")
                    # At most a second of frames is lost if the recording is cut short.
                    if frame.kind != "data" or time.monotonic() - flushed >= 1.0:
                        handle.flush()
                        flushed = time.monotonic()
                elif frame.kind != "data":
                    print(f"[{frame.kind}] {frame.detail}", file=sys.stderr)
        finally:
            if handle:
                handle.close()

    asyncio.run(run())
    _print(dict(counts))
    if args.output:
        print(f"Đã ghi frame vào {args.output}", file=sys.stderr)
        print(DATA_WARNING, file=sys.stderr)


# -- dump ---------------------------------------------------------------------


def last_session_day(now: datetime | None = None) -> date:
    """The latest weekday whose session has closed (15:00 Vietnam time); holidays are not known.

    Ngày giao dịch gần nhất đã đóng phiên (15:00 giờ Việt Nam); chưa tính ngày lễ.
    """
    now = now or datetime.now(VN_TZ)
    day = now.date() if now.hour >= 15 else now.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _dump_plans(source: Source, symbol: str, index: str) -> list[tuple[str, Plan]]:
    today = last_session_day()
    month_ago = today - timedelta(days=30)
    plans: list[tuple[str, Plan]] = []

    def verb(label: str, name: str, *args: Any, **kwargs: Any) -> None:
        if name not in source.spec.verbs:
            return
        # Some sources refuse ``limit``; snapshot-only sources refuse start/end too. Drop what the
        # source refuses, step by step; a refusal left after that is a real error.
        attempts = [kwargs]
        for dropped in (("limit",), ("start", "end", "limit")):
            variant = {k: v for k, v in kwargs.items() if k not in dropped}
            if variant != attempts[-1]:
                attempts.append(variant)
        for attempt in attempts:
            try:
                found = source.plan(name, *args, **attempt)
            except InputError:
                if attempt is attempts[-1]:
                    raise
                continue
            plans.extend((label, plan) for plan in found)
            return

    verb("ohlc_1D", "ohlc", symbol, start=month_ago, end=today, interval="1D")
    verb("ohlc_15m", "ohlc", symbol, start=today, end=today, interval="15m")
    verb("ohlc_index_1D", "ohlc", index, start=month_ago, end=today, interval="1D", asset="index")
    verb("trades", "trades", symbol, start=today, end=today, limit=100)
    verb("quote", "quote", symbol)
    verb("foreign", "foreign", symbol, start=today, end=today, limit=100)
    verb("index", "index", index, start=today, end=today, limit=100)
    verb("calendar", "calendar")
    verb("events", "events", symbol)
    samples = source.spec.samples(symbol, index) if source.spec.samples else []
    return plans + samples


def _sample_symbols(source: Source, args: argparse.Namespace) -> tuple[str, str]:
    # A global source has no Vietnamese market: a Vietnamese symbol would only waste its quota.
    if source.spec.market == "global":
        return args.global_symbol.upper(), args.global_index.upper()
    return args.symbol.upper(), args.index.upper()


def cmd_dump(args: argparse.Namespace) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.out) / stamp
    report = [f"# Hình dạng dữ liệu thô · {stamp}", "", DATA_WARNING, ""]
    manifest: list[dict[str, Any]] = []
    for name in args.sources:
        source = oapi.source(name, args.profile)
        report += [f"## {source.spec.title}", ""]
        for label, plan in _dump_plans(source, *_sample_symbols(source, args)):
            row: dict[str, Any] = {"source": name, "label": label, "operation": plan.operation}
            try:
                envelopes = source.raw(plan.operation, **plan.params)
                path = root / name / f"{label}.jsonl"
                envelopes.save(path, append=path.exists())
                row.update(
                    ok=True,
                    envelopes=len(envelopes),
                    file=str(path),
                    http_status=[e.http_status for e in envelopes],
                )
                report.append(render_markdown(f"{label} · `{plan.operation}`", shape_of(envelopes.bodies)))
            except OapiError as exc:
                row.update(ok=False, error=f"{type(exc).__name__}: {exc}")
                report.append(f"### {label} · `{plan.operation}`\n\nLỗi: {type(exc).__name__}: {exc}\n")
            manifest.append(row)
            print(("✓ " if row["ok"] else "✗ ") + f"{name}.{label}", file=sys.stderr)
    root.mkdir(parents=True, exist_ok=True)
    (root / "shapes.md").write_text("\n".join(report), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã ghi vào {root}/ (shapes.md chỉ có tên trường và kiểu, không có giá trị)", file=sys.stderr)
    print(DATA_WARNING, file=sys.stderr)


# -- parser -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vnstock-oapi",
        description="Đăng nhập và nạp dữ liệu thô qua Open API chính thức. Chỉ đọc, không đặt lệnh.",
    )
    parser.add_argument("--version", action="version", version=oapi.__version__)
    parser.add_argument("--profile", default="default", help="hồ sơ khoá, ví dụ work")
    sub = parser.add_subparsers(dest="command", required=True)
    names = oapi.sources()

    sub.add_parser("status", help="khoá và phiên trên máy, không gọi mạng").set_defaults(func=cmd_status)

    keys = sub.add_parser("keys", help="cất hoặc xoá khoá trong keyring")
    keys.add_argument("action", choices=["set", "delete"])
    keys.add_argument("source", choices=names)
    keys.set_defaults(func=cmd_keys)

    check = sub.add_parser("check", help="tìm khoá và gọi thử một thao tác rẻ nhất")
    check.add_argument("sources", nargs="*", help=f"mặc định: {' '.join(names)}")
    check.set_defaults(func=cmd_check)

    auth = sub.add_parser(
        "auth",
        help="xác thực với nguồn: mở phiên (hỏi OTP nếu nguồn cần); --clear xoá phiên đang giữ",
    )
    auth.add_argument("source", nargs="?", choices=names)
    auth.add_argument("--clear", action="store_true", help="xoá phiên của nguồn (hoặc --all)")
    auth.add_argument("--all", action="store_true", help="cùng --clear: mọi nguồn")
    auth.set_defaults(func=cmd_auth)

    for name, func, help_text in (
        ("ops", cmd_ops, "thao tác nguồn hỗ trợ"),
        ("terms", cmd_terms, "tóm tắt điều khoản và liên kết gốc"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("source", choices=names)
        p.set_defaults(func=func)

    for verb in VERBS:
        p = sub.add_parser(verb, help=f"thao tác chung {verb}")
        p.add_argument("source", choices=names)
        if verb not in ("calendar", "symbols"):
            p.add_argument("symbol", nargs="?" if verb == "events" else None)
        else:
            p.set_defaults(symbol=None)
        if verb == "symbols":
            p.add_argument("params", nargs="*", help="bộ lọc gốc của nguồn, dạng khoá=giá_trị")
        else:
            p.set_defaults(params=[])
        p.add_argument("--start")
        p.add_argument("--end")
        p.add_argument("--interval")
        p.add_argument("--limit", type=int)
        p.add_argument("--asset")
        p.add_argument("-o", "--output", help="ghi JSONL")
        p.add_argument("--preview", action="store_true", help="xem request, không gửi")
        p.set_defaults(func=cmd_verb, verb=verb)

    call = sub.add_parser(
        "call", help="gọi hàm riêng của nguồn: call <nguồn> <hàm> [tham số]; xem hàm bằng ops <nguồn>"
    )
    call.add_argument("source", choices=names)
    call.add_argument("name", help="tên hàm, xem `ops <nguồn>`")
    call.add_argument("args", nargs="*", help="đối số theo vị trí, hoặc khoá=giá_trị")
    call.add_argument("-o", "--output")
    call.add_argument("--preview", action="store_true")
    call.set_defaults(func=cmd_call)

    raw = sub.add_parser("raw", help="gọi thao tác gốc của nguồn")
    raw.add_argument("source", choices=names)
    raw.add_argument("operation")
    raw.add_argument("params", nargs="*", help="tham số gốc, dạng khoá=giá_trị")
    raw.add_argument("--page", type=int)
    raw.add_argument("-o", "--output")
    raw.add_argument("--preview", action="store_true")
    raw.set_defaults(func=cmd_raw)

    dump = sub.add_parser("dump", help="gọi một bộ thao tác mẫu, ghi JSONL và báo cáo hình dạng")
    dump.add_argument("sources", nargs="+", choices=names)
    dump.add_argument("--symbol", default="FPT")
    dump.add_argument("--index", default="VNINDEX")
    dump.add_argument("--global-symbol", default="AAPL", help="mã cho nguồn quốc tế")
    dump.add_argument("--global-index", default="^GSPC", help="chỉ số cho nguồn quốc tế")
    dump.add_argument("--out", default="samples")
    dump.set_defaults(func=cmd_dump)

    streams = _stream_sources()
    if not streams:
        return parser
    stream = sub.add_parser("stream", help="nhận stream dữ liệu thị trường qua cổng chính thức")
    stream.add_argument("source", choices=streams)
    stream.add_argument("channels", nargs="*", help="kênh của nguồn; bỏ trống để xem kênh được phép")
    stream.add_argument("--symbols", help="danh sách mã, cách nhau bởi dấu phẩy")
    stream.add_argument("--seconds", type=float, default=60.0)
    stream.add_argument("-o", "--output", help="ghi frame v1 dạng JSONL")
    stream.set_defaults(func=cmd_stream)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except OapiError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
