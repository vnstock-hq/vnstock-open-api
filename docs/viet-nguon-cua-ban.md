# Viết nguồn của bạn trên nền vnstock-oapi

> **Ngôn ngữ / Language:** Tiếng Việt | [English](write-your-own-source.md)  
> **Tài liệu chính thức / Official Docs:** [vnstocks.com/docs/vnstock-oapi](https://vnstocks.com/docs/vnstock-oapi)

`vnstock-oapi` lo phần khó và giống nhau ở mọi nguồn: đọc khoá từ biến môi trường, `.env` hay keyring; ký request
hay giữ token trong một kho phiên dùng chung cả máy; TLS đầy đủ; danh sách host đóng; timeout; tôn trọng `429`;
che khoá trong log và lỗi; trả body nguyên văn trong một phong bì có metadata. Gói của bạn chỉ khai báo phần riêng
của nguồn rồi đăng ký với `oapi.register(...)`.

## Cài đặt

```console
pip install git+https://github.com/vnstock-hq/vnstock-open-api.git
# hoặc qua kho gói chung của hệ sinh thái Vnstock
pip install -U --extra-index-url https://vnstocks.com/api/packages vnstock-oapi
```

## Ví dụ tối thiểu

```python
# vnstock_oapi_demo/__init__.py
import vnstock_oapi as oapi
from vnstock_oapi import ext

assert ext.EXT_API == 1, "Gói này viết cho API mở rộng phiên bản 1"

def _bars(symbol, *, start=None, end=None, interval="1D", limit=None, asset="equity"):
    return ext.Plan("bars", {"symbol": ext.symbol("demo", symbol), "from": start, "to": end})

SPEC = ext.SourceSpec(
    name="demo",
    title="Demo API",
    base_url="https://api.example.com/v1",
    hosts=frozenset({"api.example.com"}),
    credentials=(ext.CredentialField("api_key", "API_KEY"),),   # đọc từ DEMO_API_KEY
    auth=lambda source: ext.ApiKeyHeaderAuth(source, header="X-Api-Key"),
    ops={"bars": ext.Op("bars", "/bars/{symbol}", doc="https://example.com/docs/bars")},
    verbs={"ohlc": _bars},
)

TERMS = ext.Terms(
    source="demo",
    title="Demo API",
    terms_url="https://example.com/terms",
    docs_url="https://example.com/docs",
    checked_at="2026-11-01",
    summary=("Chỉ dùng cho cá nhân.", "Không phân phối lại dữ liệu."),
    note="",
)

oapi.register(SPEC, terms=TERMS, producer=("vnstock-oapi-demo", "0.1.0"))
```

```python
import vnstock_oapi_demo          # đăng ký xảy ra ở đây, và chỉ ở đây
import vnstock_oapi as oapi

bars = oapi.demo.ohlc("FPT", start="2026-10-01")
bars[0].producer                  # {"name": "vnstock-oapi-demo", "version": "0.1.0"}
oapi.demo.raw("bars", symbol="FPT")
oapi.demo.preview("ohlc", "FPT")  # xem request sẽ gửi, khoá đã che
```

## Bạn được dùng gì

Chỉ những tên trong `vnstock_oapi.ext.__all__` là API mở rộng. Chúng chỉ đổi khi `EXT_API` tăng. Mọi thứ khác dưới
`vnstock_oapi.core` có thể đổi ở bất kỳ bản phát hành nào.

| Nhóm | Tên |
| --- | --- |
| Khai báo | `SourceSpec`, `Op`, `Plan`, `Builder`, `BodyError`, `Samples`, `VERBS`, `Terms`, `CredentialField` |
| Xác thực | `PublicAuth`, `ApiKeyHeaderAuth`, `SignedAuth` (+ `SignHeaders`), `TokenAuth`, `OtpTokenAuth`, `TokenSet`, `Credentials`, `Auth`, `jwt_expiry`, `fallback_expiry`, `to_epoch_seconds` |
| Từ vựng chung | `canonical`, `native`, `symbol` (đọc `mapping` bạn truyền vào `register`) |
| Đầu ra | `Source`, `Envelope`, `Envelopes`, `redact` |

`verbs` chỉ nhận tám thao tác chung (`ohlc trades quote index foreign symbols calendar events`). Hàm riêng của
nguồn đặt vào `extras` và gọi được qua `oapi.<nguồn>.<tên>(...)`.

## Sàn an toàn áp cho nguồn của bạn

Nguồn đăng ký chạy qua cùng `Transport` với nguồn có sẵn: chỉ `https`, chỉ host đã khai, không theo chuyển hướng,
chỉ `GET` (trừ endpoint lấy token), timeout 10/30 giây, một User-Agent cố định không nêu tên thư viện hay dự án, `429` chờ đúng `Retry-After` một lần.
`register` còn từ chối khi:

| Từ chối | Vì sao |
| --- | --- |
| Tên trùng nguồn có sẵn hay thuộc tính của gói | `oapi.ssi` luôn là SSI của vnstock-oapi |
| Host thuộc một nguồn có sẵn | Một khoá chỉ có một phiên. Nguồn thứ hai trên cùng host sẽ đăng nhập riêng và đá phiên kia ra (SSI). Thiếu thao tác thì đóng góp vào nguồn có sẵn |
| Đường dẫn có một đoạn là `order(s)`, `account(s)`, `position(s)`, `portfolio(s)`, `balance(s)`, có trading token hay tham số tài khoản (`{accountNo}`), kể cả `login_path` và `refresh_path` | vnstock-oapi chỉ chở dữ liệu thị trường |
| Trường khoá đọc private key | Khoá đó chỉ dùng để ký lệnh |
| `producer` là `vnstock-oapi` hay để trống | Phong bì phải ghi đúng gói đã tạo ra nó |
| Thiếu `terms` có `terms_url` https | Người dùng gói của bạn cần biết điều khoản của nguồn |
| `mapping` dùng giá trị không chuẩn ở vế chung (`1h` thay vì `1H`) | Một nguồn từ vựng cho cả hệ sinh thái |

Luật xét theo **cả đoạn** của đường dẫn, nên `order-book`, `orderbook`, `/trading/foreign` hay
`/trading/market/session` đều qua. Một đường dẫn dữ liệu thị trường mà vẫn trùng luật, ví dụ thống kê lệnh toàn
thị trường, thì khai đúng đường dẫn đó kèm lý do:

```python
oapi.register(
    SPEC, terms=TERMS, producer=("vnstock-oapi-demo", "0.1.0"),
    market_paths={"/stocks/{symbol}/trading/orders": "Thống kê lệnh toàn thị trường, không phải lệnh của người dùng"},
)
```

`market_paths` chỉ nhận đúng đường dẫn của một `Op` đã khai, không nhận tiền tố, và cần lý do. Trading token và
tham số tài khoản thì không bao giờ được miễn. Lý do được ghi vào log `vnstock_oapi.registry` khi đăng ký.

Đăng ký chỉ có hiệu lực trong tiến trình đã gọi `register`. Lệnh `vnstock-oapi` không thấy nguồn đăng ký.

## Trách nhiệm

- Mã nguồn khai qua `register` là mã của bạn. Phong bì mang `producer` của gói bạn, không mang tên vnstock-oapi.
- Bạn tự đọc và tuân thủ điều khoản của nguồn, gồm điều kiện lưu trữ, hiển thị và chia sẻ dữ liệu.
- `vnstock-oapi` dùng theo giấy phép trong kho ([LICENSE](../LICENSE)). Mã nguồn được công khai để đọc, kiểm tra
  và học, nhưng đây không phải giấy phép mã nguồn mở.
- Mục 1.1 của giấy phép cho phép công bố và chia sẻ miễn phí gói mở rộng của bạn, kể cả khai `vnstock-oapi` là phụ
  thuộc, mà không cần xin phép riêng, nếu gói:
  - không chép hay đóng gói kèm mã `vnstock-oapi`; người dùng tự cài `vnstock-oapi`;
  - không thu phí;
  - để mỗi người dùng tự kết nối bằng tài khoản và key của chính họ, không chạy dịch vụ lấy dữ liệu thay người
    khác, không cung cấp lại dữ liệu;
  - không gửi lệnh giao dịch, không lấy thông tin tài khoản;
  - không sửa, tắt hay vượt qua các kiểm soát của `vnstock-oapi`;
  - ghi rõ là dùng `vnstock-oapi`, kèm liên kết tới kho gốc và giấy phép, không khiến người khác hiểu là gói của
    Vnstock.
- Dùng cho hoạt động thương mại, hay ngoài các điều kiện trên, thì cần văn bản đồng ý riêng (mục 2).

## Kiểm thử gói của bạn

Dùng `httpx.MockTransport` để không gọi mạng thật:

```python
import httpx
from vnstock_oapi.core.transport import Transport   # chỉ dùng trong test

source = oapi.demo
source._transport = Transport(
    "demo", source.spec.hosts,
    client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"c": [1]}))),
)
```
